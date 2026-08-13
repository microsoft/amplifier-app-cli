"""Focused tests for one-shot deep planning."""

import asyncio
import signal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.deep_plan import (
    MAX_CONTEXT_CHARS,
    DeepPlanError,
    DeepPlanResult,
    _prepare_child_provider,
    build_execution_prompt,
    build_recent_context,
    execute_deep_plan_turn,
    resolve_planner_provider,
    run_deep_plan,
)
from amplifier_app_cli.interrupt import run_with_interrupt
from amplifier_app_cli.main import CommandProcessor, process_runtime_mentions


class TestPlannerProviderResolution:
    def test_defaults_to_fable_only_when_setting_is_absent(self):
        assert resolve_planner_provider({}) == "fable"

    @pytest.mark.parametrize(
        "settings",
        [
            {"deep_plan": None},
            {"deep_plan": {}},
            {"deep_plan": {"provider": ""}},
            {"deep_plan": {"provider": "  "}},
            {"deep_plan": {"provider": 7}},
        ],
    )
    def test_rejects_malformed_explicit_setting(self, settings):
        with pytest.raises(DeepPlanError):
            resolve_planner_provider(settings)

    def test_strips_configured_provider_id(self):
        assert (
            resolve_planner_provider({"deep_plan": {"provider": " fable "}}) == "fable"
        )


class TestContextAndPromptBoundaries:
    def test_context_excludes_system_and_tool_messages_and_is_bounded(self):
        messages = [
            {"role": "system", "content": "not visible"},
            {"role": "tool", "content": "not visible"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
        context = build_recent_context(messages)

        assert context == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]

    def test_context_limits_characters(self):
        context = build_recent_context(
            [{"role": "user", "content": "x" * (MAX_CONTEXT_CHARS + 1)}]
        )

        assert len(context) == 1
        assert len(context[0]["content"]) == MAX_CONTEXT_CHARS

    def test_execution_prompt_marks_plan_untrusted_and_preserves_task(self):
        prompt = build_execution_prompt("implement the feature", "use a safe migration")

        assert prompt.startswith("implement the feature")
        assert "untrusted advisory plan" in prompt
        assert "<deep_plan_advisory>" in prompt


@pytest.mark.asyncio
async def test_run_deep_plan_pins_child_and_returns_validated_plan(monkeypatch):
    parent = MagicMock()
    parent_pin = MagicMock()
    parent.coordinator.get_capability.return_value = parent_pin
    child = MagicMock()
    pin = MagicMock()
    pin.current.return_value = "fable"
    child.coordinator.get_capability.return_value = pin
    hooks = MagicMock()
    captured_hook: dict[str, Any] = {}

    def register(event, callback, **_kwargs):
        captured_hook["event"] = event
        captured_hook["callback"] = callback
        return MagicMock()

    hooks.register.side_effect = register
    child.coordinator.get.return_value = hooks

    async def spawn(**kwargs):
        callback = kwargs["post_initialize_callback"]
        callback(child)
        resolution_callback = captured_hook["callback"]
        await resolution_callback(
            "provider:resolve",
            {
                "scope": "conversation",
                "provider": "anthropic",
                "model": "claude-fable-5",
            },
        )
        return {
            "status": "success",
            "output": "A valid plan",
            "session_id": "child-123",
        }

    spawn_mock = AsyncMock(side_effect=spawn)
    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.get_recent_parent_context",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr("amplifier_app_cli.deep_plan.spawn_sub_session", spawn_mock)

    result = await run_deep_plan(parent, "Implement it", "fable")

    assert result.plan == "A valid plan"
    assert result.provider == "fable"
    assert result.resolved_provider == "anthropic"
    assert result.resolved_model == "claude-fable-5"
    assert result.attribution == "resolved provider: anthropic; model: claude-fable-5"
    pin.pin.assert_called_once_with("fable")
    parent_pin.pin.assert_not_called()
    assert captured_hook["event"] == "provider:resolve"
    assert spawn_mock.call_args.kwargs["tool_inheritance"] == {"inherit_tools": []}
    assert spawn_mock.call_args.kwargs["expand_instruction_mentions"] is False
    assert spawn_mock.call_args.kwargs["agent_configs"] == {
        "deep-plan": {"agents": "none"}
    }


def test_attribution_does_not_infer_model_from_configured_provider_id():
    result = DeepPlanResult(
        plan="plan",
        provider="fable",
        session_id="child",
    )

    assert "configured provider: fable" in result.attribution
    assert "actual provider/model attribution unavailable" in result.attribution
    assert "claude" not in result.attribution.lower()


def test_cross_vendor_pin_failure_is_clear_and_does_not_touch_parent_pin():
    parent_pin = MagicMock()
    child = MagicMock()
    child_pin = MagicMock()
    child_pin.pin.side_effect = ValueError(
        "Cannot pin Anthropic provider 'fable' while the current provider is OpenAI."
    )
    child.coordinator.get_capability.return_value = child_pin

    with pytest.raises(DeepPlanError, match="current provider is OpenAI"):
        _prepare_child_provider(child, "fable", {})

    parent_pin.pin.assert_not_called()
    child.coordinator.get.assert_not_called()


@pytest.mark.asyncio
async def test_run_deep_plan_rejects_unsuccessful_planner_without_returning_plan(
    monkeypatch,
):
    parent = MagicMock()
    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.get_recent_parent_context",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.spawn_sub_session",
        AsyncMock(
            return_value={
                "status": "error",
                "output": "partial",
                "session_id": "child-123",
            }
        ),
    )

    with pytest.raises(DeepPlanError, match="no execution was started"):
        await run_deep_plan(parent, "Implement it", "fable")


@pytest.mark.asyncio
async def test_deep_plan_turn_executes_parent_exactly_once_with_same_task(monkeypatch):
    parent = MagicMock()
    parent.coordinator.cancellation.is_cancelled = False
    result = DeepPlanResult(
        plan="A valid plan",
        provider="fable",
        session_id="child-123",
    )
    planner = AsyncMock(return_value=result)
    monkeypatch.setattr("amplifier_app_cli.deep_plan.run_deep_plan", planner)
    parent_executor = AsyncMock(return_value=True)
    on_plan = MagicMock()

    async def planner_runner(awaitable):
        return await awaitable

    task = "expanded task snapshot\n<context_file>same content</context_file>"
    returned = await execute_deep_plan_turn(
        parent,
        task,
        "fable",
        planner_runner=planner_runner,
        parent_executor=parent_executor,
        on_plan=on_plan,
    )

    assert returned is result
    planner.assert_awaited_once_with(parent, task, "fable")
    parent_executor.assert_awaited_once()
    assert parent_executor.call_args.args[0].startswith(task)
    on_plan.assert_called_once_with(result)


@pytest.mark.asyncio
async def test_mention_is_read_once_and_same_snapshot_reaches_planner_and_parent(
    tmp_path, monkeypatch
):
    """The CLI expansion is the sole read for both phases of a deep-plan turn."""
    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver

    fixture_content = "ONE_TIME_MENTION_SNAPSHOT"
    fixture = tmp_path / "task.md"
    fixture.write_text(fixture_content)
    app_resolver = AppMentionResolver(bundle_mappings={"testbundle": tmp_path})

    class CountingResolver:
        relative_to = None

        def __init__(self):
            self.mentions: list[str] = []

        def resolve(self, mention: str):
            self.mentions.append(mention)
            return app_resolver.resolve(mention)

    resolver = CountingResolver()
    context = MagicMock()
    context.get_messages = AsyncMock(return_value=[])
    cancellation = MagicMock()
    cancellation.is_cancelled = False
    coordinator = MagicMock()
    coordinator.get.side_effect = lambda name: context if name == "context" else None
    coordinator.get_capability.side_effect = lambda name: (
        resolver if name == "mention_resolver" else None
    )
    coordinator.cancellation = cancellation
    parent_session = MagicMock()
    parent_session.coordinator = coordinator

    planner_instruction: str | None = None

    async def fake_spawn_sub_session(**kwargs):
        nonlocal planner_instruction
        planner_instruction = kwargs["instruction"]
        assert kwargs["expand_instruction_mentions"] is False

        pin = MagicMock()
        pin.current.return_value = "fable"
        child_coordinator = MagicMock()
        child_coordinator.get_capability.return_value = pin
        child_coordinator.get.return_value = None
        child = MagicMock()
        child.coordinator = child_coordinator
        kwargs["post_initialize_callback"](child)
        return {
            "status": "success",
            "output": "Use the captured snapshot.",
            "session_id": "child-mention-once",
        }

    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.spawn_sub_session", fake_spawn_sub_session
    )

    raw_task = "Implement the requirements in @testbundle:task.md"
    expanded_task = await process_runtime_mentions(parent_session, raw_task)
    parent_prompts: list[str] = []

    async def parent_executor(prompt: str) -> bool:
        parent_prompts.append(prompt)
        return True

    await execute_deep_plan_turn(
        parent_session,
        expanded_task,
        "fable",
        planner_runner=lambda awaitable: awaitable,
        parent_executor=parent_executor,
        on_plan=lambda _result: None,
    )

    assert resolver.mentions == ["@testbundle:task.md"]
    assert planner_instruction is not None
    assert planner_instruction.count("<context_file") == 1
    assert planner_instruction.count(fixture_content) == 1
    assert expanded_task in planner_instruction
    assert len(parent_prompts) == 1
    assert parent_prompts[0].count("<context_file") == 1
    assert parent_prompts[0].count(fixture_content) == 1
    assert parent_prompts[0].startswith(expanded_task.strip())


@pytest.mark.asyncio
async def test_deep_plan_graceful_cancellation_never_executes_parent(monkeypatch):
    parent = MagicMock()
    parent.coordinator.cancellation.is_cancelled = False
    result = DeepPlanResult(
        plan="Plan produced while cancellation propagated",
        provider="fable",
        session_id="child-123",
    )
    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.run_deep_plan",
        AsyncMock(return_value=result),
    )
    parent_executor = AsyncMock()
    on_plan = MagicMock()

    async def cancelled_runner(awaitable):
        planned = await awaitable
        parent.coordinator.cancellation.is_cancelled = True
        return planned

    with pytest.raises(asyncio.CancelledError):
        await execute_deep_plan_turn(
            parent,
            "task",
            "fable",
            planner_runner=cancelled_runner,
            parent_executor=parent_executor,
            on_plan=on_plan,
        )

    parent_executor.assert_not_awaited()
    on_plan.assert_not_called()


@pytest.mark.asyncio
async def test_deep_plan_cancellation_during_plan_render_never_executes_parent(
    monkeypatch,
):
    parent = MagicMock()
    parent.coordinator.cancellation.is_cancelled = False
    result = DeepPlanResult(
        plan="A valid plan",
        provider="fable",
        session_id="child-123",
    )
    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.run_deep_plan",
        AsyncMock(return_value=result),
    )
    parent_executor = AsyncMock(return_value=True)

    def cancel_after_render(_result):
        parent.coordinator.cancellation.is_cancelled = True

    async def planner_runner(awaitable):
        return await awaitable

    with pytest.raises(asyncio.CancelledError):
        await execute_deep_plan_turn(
            parent,
            "task",
            "fable",
            planner_runner=planner_runner,
            parent_executor=parent_executor,
            on_plan=cancel_after_render,
        )

    parent_executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_deep_plan_parent_cancellation_result_propagates(monkeypatch):
    parent = MagicMock()
    parent.coordinator.cancellation.is_cancelled = False
    result = DeepPlanResult(
        plan="A valid plan",
        provider="fable",
        session_id="child-123",
    )
    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.run_deep_plan",
        AsyncMock(return_value=result),
    )
    parent_executor = AsyncMock(return_value=False)

    async def planner_runner(awaitable):
        return await awaitable

    with pytest.raises(asyncio.CancelledError):
        await execute_deep_plan_turn(
            parent,
            "task",
            "fable",
            planner_runner=planner_runner,
            parent_executor=parent_executor,
            on_plan=MagicMock(),
        )

    parent_executor.assert_awaited_once()


@pytest.mark.asyncio
async def test_interrupt_helper_requests_parent_cancellation(monkeypatch):
    stopped = asyncio.Event()

    class Cancellation:
        def __init__(self):
            self.is_cancelled = False
            self.is_immediate = False
            self.running_tool_names: list[str] = []

        def reset(self):
            self.is_cancelled = False
            self.is_immediate = False

        def request_graceful(self):
            self.is_cancelled = True
            stopped.set()

        def request_immediate(self):
            self.is_immediate = True

    cancellation = Cancellation()
    installed: dict[str, Any] = {}

    def fake_signal(_signal_number, handler):
        previous = installed.get("handler", signal.SIG_DFL)
        installed["handler"] = handler
        return previous

    monkeypatch.setattr("amplifier_app_cli.interrupt.signal.signal", fake_signal)

    async def work():
        await stopped.wait()
        return "cancelled cleanly"

    task = asyncio.create_task(
        run_with_interrupt(
            work(),
            cancellation=cancellation,
            console=MagicMock(),
        )
    )
    while "handler" not in installed:
        await asyncio.sleep(0)
    handler = installed["handler"]
    handler(signal.SIGINT, None)

    assert await task == "cancelled cleanly"
    assert cancellation.is_cancelled is True


def test_command_processor_registers_deep_plan_and_help():
    processor = CommandProcessor(MagicMock(), "test-bundle")

    action, data = processor.process_input("/deep-plan implement it")

    assert action == "deep_plan"
    assert data["args"] == "implement it"
    assert "/deep-plan" in processor._format_help()
