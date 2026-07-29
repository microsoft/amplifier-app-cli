"""Mounted provider acceptance for exactly-once transcript ownership."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest
from amplifier_core import AmplifierSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from amplifier_app_cli.runtime.interactive_input import InteractiveInputRouter
from amplifier_app_cli.runtime.interactive_resources import (
    InteractiveResourceDependencies,
)
from amplifier_app_cli.runtime.interactive_resources import InteractiveResourceRequest
from amplifier_app_cli.runtime.interactive_resources import (
    create_interactive_session_resources,
)
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnBindings
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnConfig
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnRunner
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnServices
from amplifier_app_cli.runtime.session_persistence import InteractiveSessionPersistence
from amplifier_app_cli.session_runner import InitializedSession
from amplifier_app_cli.session_runner import SessionConfig
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.ui.command_processor import CommandProcessor
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.git_yield import GitDiffSnapshot
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl import LayeredReplServices
from amplifier_app_cli.ui.notices import NoticeKind
from amplifier_app_cli.ui.transcript_blocks import AnswerBlock
from amplifier_app_cli.ui.transcript_blocks import ToolBlock
from amplifier_app_cli.ui.transcript_blocks import UserBlock
from amplifier_app_cli.ui.turn_completion import TurnCompletionRenderer
from amplifier_app_cli.ui.ui_events import UiEvent
from amplifier_app_cli.ui.message_renderer import render_message

_USER = "MOUNTED_USER_SENTINEL"
_ANSWER = "MOUNTED_FINAL_SENTINEL"
_TOOL_RESULT = "MOUNTED_TOOL_SENTINEL"


class _Hooks:
    def __init__(self) -> None:
        self._handlers: dict[
            str, list[tuple[int, str, Callable[[str, dict[str, Any]], object]]]
        ] = defaultdict(list)

    def register(
        self,
        event: str,
        handler: Callable[[str, dict[str, Any]], object],
        *,
        priority: int = 0,
        name: str = "",
    ) -> Callable[[], None]:
        entry = (priority, name, handler)
        self._handlers[event].append(entry)

        def unregister() -> None:
            if entry in self._handlers[event]:
                self._handlers[event].remove(entry)

        return unregister

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        handlers = sorted(self._handlers[event], key=lambda item: item[0], reverse=True)
        for _, _, handler in tuple(handlers):
            result = handler(event, data)
            if isinstance(result, Awaitable):
                await result

    def unregister(self, name: str) -> None:
        for event, handlers in self._handlers.items():
            self._handlers[event] = [entry for entry in handlers if entry[1] != name]


class _Context:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def get_messages(self) -> list[dict[str, Any]]:
        return deepcopy(self.messages)


class _Cancellation:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.is_cancelled = False
        self.is_immediate = False
        self.running_tool_names: list[str] = []


class _ApprovalSystem:
    def __init__(self) -> None:
        self.bypass_permissions = False
        self.decision_history: tuple[object, ...] = ()

    def set_bypass_permissions(self, enabled: bool) -> None:
        self.bypass_permissions = enabled


class _MountedProvider:
    """Network-free provider mounted in the same coordinator slot as production."""

    def __init__(self, hooks: _Hooks, context: _Context) -> None:
        self._hooks = hooks
        self._context = context
        self.default_model = "mounted-model"
        self.config: dict[str, Any] = {"default_model": self.default_model}

    async def execute(self, prompt: str) -> str:
        self._context.messages.append({"role": "user", "content": prompt})
        request = {
            "session_id": "mounted-session",
            "request_id": "mounted-request",
            "block_index": 0,
            "block_type": "text",
        }
        await self._hooks.emit("provider:request", request)
        await self._hooks.emit("llm:stream_block_start", request)
        for text in ("MOUNTED_", "FINAL_", "SENTINEL"):
            await self._hooks.emit(
                "llm:stream_block_delta",
                {**request, "text": text},
            )

        tool_event = {
            "session_id": "mounted-session",
            "tool_call_id": "mounted-tool-call",
            "tool_name": "mounted_probe",
            "tool_input": {"query": "acceptance"},
        }
        await self._hooks.emit("tool:pre", tool_event)
        self._context.messages.append(
            {
                "role": "tool",
                "tool_call_id": "mounted-tool-call",
                "content": _TOOL_RESULT,
            }
        )
        completed_tool = {**tool_event, "result": {"output": _TOOL_RESULT}}
        await self._hooks.emit("tool:post", completed_tool)
        # Mounted hook transports may be at-least-once. The transcript owner must
        # collapse a repeated terminal event by its session/call identity.
        await self._hooks.emit("tool:post", completed_tool)

        await self._hooks.emit("llm:stream_block_end", request)
        await self._hooks.emit(
            "llm:response",
            {
                **request,
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 4,
                    "total_tokens": 7,
                },
            },
        )
        self._context.messages.append({"role": "assistant", "content": _ANSWER})
        return _ANSWER


class _Coordinator:
    def __init__(self) -> None:
        self.session_state: dict[str, object] = {}
        self.hooks = _Hooks()
        self.context = _Context()
        self.cancellation = _Cancellation()
        self.approval_system = _ApprovalSystem()
        self.todo_state = None
        self.capabilities: dict[str, object] = {}
        self.orchestrator = SimpleNamespace(config={})
        self.provider = _MountedProvider(self.hooks, self.context)

    def get(self, name: str) -> object | None:
        return {
            "context": self.context,
            "hooks": self.hooks,
            "orchestrator": self.orchestrator,
            "providers": {"mounted": self.provider},
        }.get(name)

    def register_capability(self, name: str, value: object) -> None:
        self.capabilities[name] = value

    def get_capability(self, name: str) -> object | None:
        return self.capabilities.get(name)


class _Session:
    def __init__(self) -> None:
        self.session_id = "mounted-session"
        self.coordinator = _Coordinator()

    async def execute(self, prompt: str) -> str:
        return await self.coordinator.provider.execute(prompt)


class _CommandProcessor(CommandProcessor):
    COMMANDS: dict[str, dict[str, str]] = {}
    MODE_SHORTCUTS: dict[str, dict[str, str]] = {}
    SKILL_SHORTCUTS: dict[str, dict[str, str]] = {}

    def __init__(
        self, session: AmplifierSession, bundle_name: str, *, mcp_prompts=()
    ) -> None:
        self.session = session
        self.bundle_name = bundle_name
        self.mcp_prompts = mcp_prompts
        self.configurator = None
        self.command_registry = CommandRegistry(())

    def process_input(self, user_input: str) -> tuple[str, dict[str, str]]:
        return "prompt", {"text": user_input}

    async def _handle_mode(self, value: str) -> str:
        return value


@dataclass
class _SavedSnapshot:
    session_id: str
    messages: list[dict[str, Any]]
    metadata: dict[str, Any]


class _Store(SessionStore):
    def __init__(self) -> None:
        self.snapshots: list[_SavedSnapshot] = []

    def get_metadata(self, session_id: str) -> dict[str, Any]:
        if not self.snapshots:
            return {}
        return deepcopy(self.snapshots[-1].metadata)

    def save(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        self.snapshots.append(
            _SavedSnapshot(session_id, deepcopy(messages), deepcopy(metadata))
        )


@pytest.mark.asyncio
async def test_mounted_stream_commits_each_conversation_block_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    store = _Store()
    session_config = SessionConfig(config={}, search_paths=[tmp_path], verbose=False)
    initialized = InitializedSession(
        session=cast(AmplifierSession, session),
        session_id=session.session_id,
        config=session_config,
        store=store,
        configurator=None,
    )

    async def create_session(
        config: SessionConfig, console: Console
    ) -> InitializedSession:
        return initialized

    # Isolate from whatever config.tui.* the real ambient settings.yaml
    # (repo project scope, user global scope) might declare -- this test
    # exercises the mounted-provider transcript path, not startup presets.
    monkeypatch.setattr(
        "amplifier_app_cli.runtime.interactive_resources.AppSettings",
        lambda: SimpleNamespace(get_tui_startup_config=lambda: {}),
    )

    resources = await create_interactive_session_resources(
        InteractiveResourceRequest(
            config={"providers": [{"config": {"model": "mounted-model"}}]},
            search_paths=[tmp_path],
            verbose=False,
            bundle_name="mounted-acceptance",
        ),
        InteractiveResourceDependencies(
            console=Console(file=StringIO(), force_terminal=False),
            input_stream=StringIO(),
            create_initialized_session=create_session,
            session_store_factory=lambda: store,
            command_processor_factory=_CommandProcessor,
            supports_layered_ui=lambda input_stream, output_stream: True,
            get_layered_app=lambda: None,
        ),
    )
    assert session.coordinator.get("providers") == {
        "mounted": session.coordinator.provider
    }

    observed: list[UiEvent] = []
    emit = resources.ui_events.emit

    def record(block: UiEvent) -> None:
        observed.append(block)
        emit(block)

    monkeypatch.setattr(resources.ui_events, "emit", record)

    with create_pipe_input() as pipe_input:
        app = LayeredReplApp(
            config=LayeredReplConfig(
                history_path=tmp_path / "history",
                completion=LayeredReplCompletion(CommandRegistry(())),
                bundle_name="mounted-acceptance",
                session_id=session.session_id,
                input=pipe_input,
                output=DummyOutput(),
            ),
            bindings=LayeredReplBindings(on_submit=lambda submission: None),
            services=LayeredReplServices(
                task_tracker=resources.task_tracker,
                stream_status=resources.stream_status,
                runtime_status=resources.runtime_status,
                notice_state=resources.notice_state,
                trust_state=resources.trust_state,
                outcome_ledger=resources.outcome_ledger,
                needs_you=resources.needs_you,
                steering_queue=resources.steering_queue,
                evidence_model=resources.evidence_model,
                event_dispatcher=resources.ui_events,
            ),
        )

        persistence = InteractiveSessionPersistence(
            session=session,
            store=store,
            session_id=session.session_id,
            bundle_name="mounted-acceptance",
            config={"providers": [{"config": {"model": "mounted-model"}}]},
            interaction_state=resources.interaction_state,
            outcome_ledger=resources.outcome_ledger,
            runtime_status=resources.runtime_status,
        )

        async def capture_diff(path: Path) -> GitDiffSnapshot:
            return GitDiffSnapshot(True)

        completion = TurnCompletionRenderer(
            events=resources.ui_events,
            interaction=resources.interaction,
            current_task=lambda: None,
            get_layered_app=lambda: app,
        )
        turn_runner = InteractiveTurnRunner(
            config=InteractiveTurnConfig(session.session_id, tmp_path),
            services=InteractiveTurnServices(
                execute=session.execute,
                cancellation=session.coordinator.cancellation,
                get_hooks=lambda: session.coordinator.hooks,
                repair_transcript=lambda: asyncio.sleep(0, result=False),
                persist=persistence.save,
                render_message=render_message,
                capture_diff=capture_diff,
                events=resources.ui_events,
                outcome_ledger=resources.outcome_ledger,
                completion=completion,
                evidence=resources.evidence_model,
                runtime_status=resources.runtime_status,
                image_injector=resources.image_injector,
            ),
            bindings=InteractiveTurnBindings(
                immediate_interrupt=asyncio.Event(),
                request_interrupt=lambda: True,
                summarize=lambda text, **kwargs: text,
                set_running=lambda value: None,
                set_task_title=lambda value: None,
                refresh_title=lambda title, running: None,
                get_layered_app=lambda: app,
                active_mode=resources.active_mode,
                enqueue_followup=lambda prompt: None,
                notify=lambda text: None,
                steering_queue=resources.steering_queue,
            ),
        )

        async def enqueue_prompt(text: str, attachments: tuple[Any, ...]) -> None:
            await turn_runner.execute(text, attachments)

        router = InteractiveInputRouter(
            command_processor=resources.command_processor,
            session_commands=resources.session_commands,
            interaction=resources.interaction,
            steering_queue=resources.steering_queue,
            events=resources.ui_events,
            active_mode=resources.active_mode,
            is_running=lambda: False,
            expand_prompt=lambda text: asyncio.sleep(0, result=text),
            enqueue_prompt=enqueue_prompt,
            notify=lambda text, kind=NoticeKind.INFO: None,
            get_layered_app=lambda: app,
            summarize=lambda text, **kwargs: text,
        )

        assert await router.handle(_USER) is True

        transcript = app._transcript_view.plain_text()
        assert transcript.count(f"❯ [chat] {_USER}") == 1
        assert transcript.count(_ANSWER) == 1
        assert transcript.count("Ran 1 mounted_probe call") == 1
        assert sum(isinstance(block, UserBlock) for block in observed) == 1
        assert sum(isinstance(block, AnswerBlock) for block in observed) == 1
        assert sum(isinstance(block, ToolBlock) for block in observed) == 1

        # One incremental checkpoint plus one completed-turn checkpoint. The
        # replayed tool:post must not create a third durable write.
        assert len(store.snapshots) == 2
        final = store.snapshots[-1]
        assert final.session_id == session.session_id
        assert [message["role"] for message in final.messages] == [
            "user",
            "tool",
            "assistant",
        ]
        assert sum(message["content"] == _USER for message in final.messages) == 1
        assert (
            sum(message["content"] == _TOOL_RESULT for message in final.messages) == 1
        )
        assert sum(message["content"] == _ANSWER for message in final.messages) == 1
        assert final.metadata["session_id"] == session.session_id
        assert final.metadata["bundle"] == "mounted-acceptance"
        assert final.metadata["model"] == "mounted-model"
        assert final.metadata["turn_count"] == 1

        app.exit()

    for cleanup in resources.cleanup.collect():
        cleanup()
