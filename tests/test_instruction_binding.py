"""Entry-path coverage for the optional execution-input v1 capability."""

from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.instruction_binding import EXECUTION_INPUT_CAPABILITY
from amplifier_app_cli.instruction_binding import bind_execution_input
from amplifier_app_cli.session_runner import SessionConfig
from amplifier_app_cli.session_runner import _mark_host_checkpoint
from amplifier_app_cli.session_runner import create_initialized_session


class _CapabilityCoordinator:
    def __init__(self, context=None) -> None:
        self.capabilities: dict[str, object] = {}
        self.context = context
        self.cancellation = MagicMock()
        self.cancellation.is_cancelled = False
        self.cancellation.is_immediate = False
        self.session_state: dict = {}
        self.display_system = MagicMock()
        self.approval_system = MagicMock()
        self.config: dict = {}

    def register_capability(self, name: str, value: object) -> None:
        self.capabilities[name] = value

    def get_capability(self, name: str):
        return self.capabilities.get(name)

    def get(self, name: str):
        return self.context if name == "context" else None

    async def mount(self, name: str, value: object) -> None:
        return None

    async def collect_contributions(self) -> list:
        return []


class _Context:
    def __init__(self, *, checkpoint_restore: bool = False) -> None:
        self.messages: list[dict] = []
        self.checkpoints: list[list[dict]] = []
        self.checkpoint_restore = checkpoint_restore

    async def get_messages(self) -> list[dict]:
        return self.messages

    async def set_messages(self, messages: list[dict]) -> None:
        self.messages = messages

    async def add_message(self, message: dict) -> None:
        self.messages.append(message)

    async def restore_host_checkpoint(self, messages: list[dict]) -> None:
        if not self.checkpoint_restore:
            raise AssertionError("untrusted history must not use checkpoint restoration")
        self.checkpoints.append(messages)
        self.messages = messages


def _session(coordinator: _CapabilityCoordinator, *, response: str = "response") -> MagicMock:
    session = MagicMock()
    session.coordinator = coordinator
    session.config = {}
    session.session_id = "test-session"
    session.initialize = AsyncMock()
    session.cleanup = AsyncMock()
    session.execute = AsyncMock(return_value=response)
    return session


def test_binding_is_fresh_and_ignored_when_old_coordinator_has_no_api() -> None:
    coordinator = _CapabilityCoordinator()

    first_id = bind_execution_input(coordinator, origin="human")
    first = coordinator.capabilities[EXECUTION_INPUT_CAPABILITY]
    second_id = bind_execution_input(coordinator, origin="delegation")

    assert first_id and second_id and first_id != second_id
    assert first == {"version": 1, "input_id": first_id, "origin": "human"}
    assert coordinator.capabilities[EXECUTION_INPUT_CAPABILITY] == {
        "version": 1,
        "input_id": second_id,
        "origin": "delegation",
    }
    assert bind_execution_input(object(), origin="human") is None


@pytest.mark.asyncio
async def test_headless_entry_binds_human_input_immediately_before_execute(
    tmp_path,
) -> None:
    """The real headless entry registers the human boundary before executing."""
    from amplifier_app_cli.main import execute_single

    context = _Context()
    coordinator = _CapabilityCoordinator(context)
    session = _session(coordinator)
    seen_bindings: list[dict] = []

    async def execute(prompt: str) -> str:
        binding = coordinator.capabilities[EXECUTION_INPUT_CAPABILITY]
        assert isinstance(binding, dict)
        seen_bindings.append(binding)
        assert prompt == "human input"
        return "response"

    session.execute.side_effect = execute
    initialized = MagicMock(session=session, session_id=session.session_id)
    initialized.cleanup = AsyncMock()

    with (
        patch(
            "amplifier_app_cli.main.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch("amplifier_app_cli.main.console"),
    ):
        await execute_single(
            prompt="human input",
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=session.session_id,
            bundle_name="test",
        )

    assert seen_bindings == [
        {
            "version": 1,
            "input_id": seen_bindings[0]["input_id"],
            "origin": "human",
        }
    ]


@pytest.mark.asyncio
async def test_interactive_retry_rebinds_each_human_execution(tmp_path) -> None:
    """A failed interactive execute cannot leak its input ID into the next turn."""
    from amplifier_app_cli.main import interactive_chat

    context = _Context()
    coordinator = _CapabilityCoordinator(context)
    session = _session(coordinator)
    bindings: list[dict] = []

    async def execute(_prompt: str) -> str:
        binding = coordinator.capabilities[EXECUTION_INPUT_CAPABILITY]
        assert isinstance(binding, dict)
        bindings.append(binding)
        if len(bindings) == 1:
            raise RuntimeError("first execution failed")
        return "second response"

    session.execute.side_effect = execute
    initialized = MagicMock(session=session, session_id=session.session_id, configurator=None)
    initialized.cleanup = AsyncMock()
    prompt_session = MagicMock()
    prompt_session.prompt_async = AsyncMock(side_effect=["first", "second", EOFError])
    steering_manager = MagicMock()
    steering_manager.run = AsyncMock()

    with (
        patch(
            "amplifier_app_cli.main.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch("amplifier_app_cli.main._create_prompt_session", return_value=prompt_session),
        patch("amplifier_app_cli.main.patch_stdout", side_effect=lambda **_kwargs: nullcontext()),
        patch("amplifier_app_cli.main.console"),
        patch("amplifier_app_cli.main.AppSettings"),
        patch("amplifier_app_cli.incremental_save.register_incremental_save"),
        patch(
            "amplifier_app_cli.main.process_runtime_mentions",
            new=AsyncMock(side_effect=lambda _session, text: text),
        ),
        patch(
            "amplifier_app_cli.steering_input.SteeringInputManager",
            return_value=steering_manager,
        ),
    ):
        await interactive_chat(
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=session.session_id,
            bundle_name="test",
        )

    assert [binding["origin"] for binding in bindings] == ["human", "human"]
    assert bindings[0]["input_id"] != bindings[1]["input_id"]


@pytest.mark.anyio
async def test_spawn_and_resume_bind_delegated_inputs_and_restore_checkpoints() -> None:
    """Both actual child entry paths receive fresh delegated provenance."""
    from amplifier_app_cli.session_spawner import resume_sub_session, spawn_sub_session

    async def execute_with_binding(
        coordinator: _CapabilityCoordinator, bindings: list[dict], instruction: str
    ) -> str:
        binding = coordinator.capabilities[EXECUTION_INPUT_CAPABILITY]
        assert isinstance(binding, dict)
        bindings.append(binding)
        assert instruction
        return "delegated response"

    parent_coordinator = _CapabilityCoordinator()
    parent = MagicMock()
    parent.coordinator = parent_coordinator
    parent.config = {"session": {"orchestrator": "loop-basic", "context": "simple"}}
    parent.session_id = "parent-session"
    parent.trace_id = "parent-trace"
    parent.loader = None

    spawned_coordinator = _CapabilityCoordinator(_Context())
    spawned_child = _session(spawned_coordinator)
    spawned_bindings: list[dict] = []

    async def spawned_execute(instruction: str) -> str:
        return await execute_with_binding(
            spawned_coordinator, spawned_bindings, instruction
        )

    spawned_child.execute.side_effect = spawned_execute

    with (
        patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=spawned_child,
        ),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore.save"),
    ):
        await spawn_sub_session(
            agent_name="agent",
            instruction="fresh delegated task",
            parent_session=parent,
            agent_configs={"agent": {}},
            sub_session_id="spawned-child",
        )

    resumed_context = _Context(checkpoint_restore=True)
    resumed_coordinator = _CapabilityCoordinator(resumed_context)
    resumed_child = _session(resumed_coordinator)
    resumed_bindings: list[dict] = []

    async def resumed_execute(instruction: str) -> str:
        return await execute_with_binding(
            resumed_coordinator, resumed_bindings, instruction
        )

    resumed_child.execute.side_effect = resumed_execute
    transcript = [{"role": "user", "content": "saved delegated task"}]
    metadata = {
        "config": {"session": {"orchestrator": "loop-basic", "context": "simple"}},
        "agent_name": "agent",
        "parent_id": "parent-session",
        "trace_id": "parent-trace",
        "working_dir": "/test/project",
    }
    store = MagicMock()
    store.exists.return_value = True
    store.load.return_value = (transcript, metadata)

    with (
        patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=resumed_child,
        ),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem"),
    ):
        await resume_sub_session("resumed-child", "continued delegated task")

    assert [binding["origin"] for binding in spawned_bindings + resumed_bindings] == [
        "delegation",
        "delegation",
    ]
    assert spawned_bindings[0]["input_id"] != resumed_bindings[0]["input_id"]
    assert resumed_context.checkpoints == [transcript]


@pytest.mark.anyio
async def test_host_checkpoint_uses_trusted_restore_but_plain_history_stays_generic(
    monkeypatch,
) -> None:
    """Only the opaque SessionStore marker reaches context's trusted API."""
    monkeypatch.setattr(
        "amplifier_app_cli.session_runner.check_first_run", lambda: False, raising=False
    )

    async def initialize(transcript: list[dict], *, trusted: bool) -> _Context:
        context = _Context(checkpoint_restore=trusted)
        coordinator = _CapabilityCoordinator(context)
        session = _session(coordinator)
        config = SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
            session_id="restored-session",
            initial_transcript=(
                _mark_host_checkpoint(transcript) if trusted else transcript
            ),
        )
        with (
            patch(
                "amplifier_app_cli.session_runner._create_bundle_session",
                new=AsyncMock(return_value=session),
            ),
            patch(
                "amplifier_app_cli.commands.init.check_first_run", return_value=False
            ),
            patch(
                "amplifier_app_cli.project_utils.get_project_slug",
                return_value="test-project",
            ),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        ):
            await create_initialized_session(config, MagicMock())
        return context

    transcript = [{"role": "user", "content": "saved"}]
    trusted = await initialize(transcript, trusted=True)
    generic = await initialize(list(transcript), trusted=False)

    assert trusted.checkpoints == [transcript]
    assert generic.checkpoints == []
    assert generic.messages == transcript