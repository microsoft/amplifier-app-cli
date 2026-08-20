"""Focused tests for one-shot deep planning."""

import asyncio
import signal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.deep_plan import (
    DEFAULT_PLANNER_EFFORT,
    DEFAULT_PLANNER_MODEL,
    DEFAULT_PLANNER_PROVIDER,
    MAX_CONTEXT_CHARS,
    DeepPlanConfig,
    DeepPlanError,
    DeepPlanResult,
    build_execution_prompt,
    build_recent_context,
    execute_deep_plan_turn,
    preflight_planner_target,
    resolve_planner_config,
    resolve_planner_provider,
    run_deep_plan,
)
from amplifier_app_cli.interrupt import run_with_interrupt
from amplifier_app_cli.main import CommandProcessor, process_runtime_mentions


def _provider(
    vendor: str,
    model: str,
    *,
    priority: int = 1,
) -> MagicMock:
    provider = MagicMock()
    provider.priority = priority
    provider.config = {"priority": priority}
    if model == DEFAULT_PLANNER_MODEL:
        provider.config.update(
            {
                "reasoning_effort": DEFAULT_PLANNER_EFFORT,
                "refusal_fallback_enabled": False,
                "fallback_on_overload": False,
            }
        )
    provider.get_info.return_value = SimpleNamespace(
        id=vendor,
        defaults={"model": model},
    )
    return provider


def _parent_session(
    mounted: dict[str, MagicMock],
    *,
    current: str | None = None,
) -> tuple[MagicMock, MagicMock]:
    context = MagicMock()
    context.get_messages = AsyncMock(return_value=[])
    pin = MagicMock()
    pin.available.return_value = list(mounted)
    pin.current.return_value = current
    cancellation = MagicMock()
    cancellation.is_cancelled = False

    coordinator = MagicMock()
    coordinator.get.side_effect = lambda name: {
        "providers": mounted,
        "context": context,
    }.get(name)
    coordinator.get_capability.side_effect = lambda name: (
        pin if name == "conversation.provider_pin" else None
    )
    coordinator.cancellation = cancellation

    session = MagicMock()
    session.coordinator = coordinator
    session.session_id = "parent"
    return session, pin


def _child_session(
    mounted: dict[str, MagicMock],
) -> tuple[MagicMock, MagicMock, dict[str, Any]]:
    pin = MagicMock()
    pin.current.side_effect = lambda: (
        pin.pin.call_args.args[0] if pin.pin.called else None
    )
    callbacks: dict[str, Any] = {}
    hooks = MagicMock()

    def register(event: str, callback: Any, **_kwargs: Any) -> MagicMock:
        callbacks[event] = callback
        return MagicMock()

    hooks.register.side_effect = register
    coordinator = MagicMock()
    coordinator.get.side_effect = lambda name: {
        "providers": mounted,
        "hooks": hooks,
    }.get(name)
    coordinator.get_capability.side_effect = lambda name: (
        pin if name == "conversation.provider_pin" else None
    )
    child = MagicMock()
    child.coordinator = coordinator
    return child, pin, callbacks


async def _emit_resolution(
    callbacks: dict[str, Any],
    *,
    provider: str = DEFAULT_PLANNER_PROVIDER,
    model: str = DEFAULT_PLANNER_MODEL,
    basis: str = "pinned",
) -> None:
    callback = callbacks["provider:resolve"]
    await callback(
        "provider:resolve",
        {
            "scope": "conversation",
            "provider": provider,
            "model": model,
            "basis": basis,
        },
    )


class TestPlannerConfigResolution:
    def test_implicit_default_is_anthropic_fable_max(self) -> None:
        config = resolve_planner_config({})

        assert config == DeepPlanConfig(
            provider=DEFAULT_PLANNER_PROVIDER,
            model=DEFAULT_PLANNER_MODEL,
            effort=DEFAULT_PLANNER_EFFORT,
        )
        assert resolve_planner_provider({}) == DEFAULT_PLANNER_PROVIDER

    def test_provider_only_configuration_is_preserved(self) -> None:
        assert resolve_planner_config(
            {"deep_plan": {"provider": " fable "}}
        ) == DeepPlanConfig(provider="fable")

    def test_fable_exact_model_defaults_effort_to_max(self) -> None:
        assert resolve_planner_config(
            {
                "deep_plan": {
                    "provider": "anthropic",
                    "model": "claude-fable-5",
                }
            }
        ) == DeepPlanConfig(
            provider="anthropic",
            model="claude-fable-5",
            effort="max",
        )

    def test_explicit_valid_effort_is_normalized(self) -> None:
        config = resolve_planner_config(
            {
                "deep_plan": {
                    "provider": "anthropic",
                    "model": "claude-fable-5",
                    "effort": " HIGH ",
                }
            }
        )

        assert config.effort == "high"

    @pytest.mark.parametrize(
        "settings",
        [
            {"deep_plan": None},
            {"deep_plan": {}},
            {"deep_plan": {"provider": ""}},
            {"deep_plan": {"provider": "  "}},
            {"deep_plan": {"provider": 7}},
            {"deep_plan": {"provider": "anthropic-*"}},
            {"deep_plan": {"provider": "anthropic", "model": ""}},
            {"deep_plan": {"provider": "anthropic", "model": 7}},
            {"deep_plan": {"provider": "anthropic", "model": "claude-*"}},
            {"deep_plan": {"provider": "anthropic", "effort": ""}},
            {"deep_plan": {"provider": "anthropic", "effort": "ultra"}},
            {"deep_plan": {"provider": "anthropic", "effort": 7}},
        ],
    )
    def test_rejects_malformed_explicit_configuration(
        self,
        settings: dict[str, Any],
    ) -> None:
        with pytest.raises(DeepPlanError):
            resolve_planner_config(settings)


class TestPlannerPreflight:
    def test_default_specializes_only_mounted_anthropic_provider(self) -> None:
        parent, _pin = _parent_session(
            {"anthropic": _provider("anthropic", "claude-opus-5")}
        )

        target = preflight_planner_target(parent, resolve_planner_config({}))

        assert target.provider == "anthropic"
        assert target.model == "claude-fable-5"
        assert len(target.provider_preferences) == 1
        preference = target.provider_preferences[0]
        assert preference.provider == "anthropic"
        assert preference.model == "claude-fable-5"
        assert preference.config == {
            "reasoning_effort": "max",
            "refusal_fallback_enabled": False,
            "fallback_on_overload": False,
        }
        assert target.expected_provider_config == preference.config

    def test_provider_only_uses_mounted_instance_default_model(self) -> None:
        parent, _pin = _parent_session(
            {
                "anthropic": _provider("anthropic", "claude-opus-5", priority=1),
                "fable": _provider("anthropic", "claude-fable-5", priority=4),
            }
        )

        target = preflight_planner_target(
            parent,
            DeepPlanConfig(provider="fable"),
        )

        assert target.provider == "fable"
        assert target.model == "claude-fable-5"
        assert target.effort == "max"

    def test_unmounted_declared_provider_has_actionable_guidance(self) -> None:
        parent, _pin = _parent_session(
            {"anthropic": _provider("anthropic", "claude-opus-5")}
        )

        with pytest.raises(
            DeepPlanError,
            match="not mounted.*Mounted provider IDs: anthropic.*not merely",
        ):
            preflight_planner_target(
                parent,
                DeepPlanConfig(provider="fable"),
            )

    def test_cross_vendor_target_fails_closed(self) -> None:
        parent, _pin = _parent_session(
            {
                "openai": _provider("openai", "gpt-5", priority=1),
                "anthropic": _provider("anthropic", "claude-opus-5", priority=2),
            }
        )

        with pytest.raises(DeepPlanError, match="Cross-vendor"):
            preflight_planner_target(
                parent,
                DeepPlanConfig(
                    provider="anthropic",
                    model="claude-fable-5",
                ),
            )

    def test_ambiguous_mixed_vendor_automatic_route_fails_closed(self) -> None:
        parent, _pin = _parent_session(
            {
                "openai": _provider("openai", "gpt-5", priority=1),
                "anthropic": _provider("anthropic", "claude-opus-5", priority=1),
            }
        )

        with pytest.raises(
            DeepPlanError,
            match="equally preferred providers from different vendors",
        ):
            preflight_planner_target(parent, resolve_planner_config({}))

    @pytest.mark.parametrize("bad_vendor", ["", None])
    def test_unverifiable_vendor_fails_closed(self, bad_vendor: Any) -> None:
        provider = _provider("anthropic", "claude-opus-5")
        provider.get_info.return_value = SimpleNamespace(
            id=bad_vendor,
            defaults={"model": "claude-opus-5"},
        )
        parent, _pin = _parent_session({"anthropic": provider})

        with pytest.raises(DeepPlanError, match="vendor identity"):
            preflight_planner_target(parent, resolve_planner_config({}))


class TestContextAndPromptBoundaries:
    def test_context_excludes_system_and_tool_messages_and_is_bounded(self) -> None:
        messages = [
            {"role": "system", "content": "not visible"},
            {"role": "tool", "content": "not visible"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]

        assert build_recent_context(messages) == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]

    def test_context_limits_characters(self) -> None:
        context = build_recent_context(
            [{"role": "user", "content": "x" * (MAX_CONTEXT_CHARS + 1)}]
        )

        assert len(context) == 1
        assert len(context[0]["content"]) == MAX_CONTEXT_CHARS

    def test_execution_prompt_marks_plan_untrusted_and_preserves_task(self) -> None:
        prompt = build_execution_prompt("implement the feature", "use a safe migration")

        assert prompt.startswith("implement the feature")
        assert "untrusted advisory plan" in prompt
        assert "<deep_plan_advisory>" in prompt


@pytest.mark.asyncio
async def test_default_deep_plan_specializes_child_and_preserves_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_provider = _provider("anthropic", "claude-opus-5")
    parent, parent_pin = _parent_session({"anthropic": parent_provider})
    child, child_pin, callbacks = _child_session(
        {"anthropic": _provider("anthropic", "claude-fable-5")}
    )

    async def spawn(**kwargs: Any) -> dict[str, Any]:
        preferences = kwargs["provider_preferences"]
        assert len(preferences) == 1
        assert preferences[0].model == "claude-fable-5"
        kwargs["post_initialize_callback"](child)
        await _emit_resolution(callbacks)
        return {
            "status": "success",
            "output": "A valid plan",
            "session_id": "child-123",
        }

    spawn_mock = AsyncMock(side_effect=spawn)
    monkeypatch.setattr("amplifier_app_cli.deep_plan.spawn_sub_session", spawn_mock)
    parent_executor = AsyncMock(return_value=True)
    displayed = MagicMock()

    result = await execute_deep_plan_turn(
        parent,
        "Implement it",
        resolve_planner_config({}),
        planner_runner=lambda awaitable: awaitable,
        parent_executor=parent_executor,
        on_plan=displayed,
    )

    assert result == DeepPlanResult(
        plan="A valid plan",
        provider="anthropic",
        model="claude-fable-5",
        session_id="child-123",
    )
    assert result.attribution == "provider: anthropic; model: claude-fable-5"
    child_pin.pin.assert_called_once_with("anthropic")
    parent_pin.pin.assert_not_called()
    assert parent_provider.get_info().defaults["model"] == "claude-opus-5"
    parent_executor.assert_awaited_once()
    displayed.assert_called_once_with(result)
    assert spawn_mock.call_args.kwargs["tool_inheritance"] == {"inherit_tools": []}
    assert spawn_mock.call_args.kwargs["hook_inheritance"] == {
        "exclude_hooks": ["hooks-routing", "hooks-matrix-guard"]
    }
    assert spawn_mock.call_args.kwargs["agent_configs"] == {
        "deep-plan": {"agents": "none"}
    }
    assert spawn_mock.call_args.kwargs["expand_instruction_mentions"] is False


@pytest.mark.asyncio
async def test_provider_only_mounted_fable_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, parent_pin = _parent_session(
        {
            "anthropic": _provider("anthropic", "claude-opus-5", priority=1),
            "fable": _provider("anthropic", "claude-fable-5", priority=4),
        }
    )
    child, child_pin, callbacks = _child_session(
        {
            "anthropic": _provider("anthropic", "claude-opus-5", priority=1),
            "fable": _provider("anthropic", "claude-fable-5", priority=0),
        }
    )

    async def spawn(**kwargs: Any) -> dict[str, Any]:
        kwargs["post_initialize_callback"](child)
        await _emit_resolution(
            callbacks,
            provider="fable",
            model="claude-fable-5",
        )
        return {
            "status": "success",
            "output": "Named instance plan",
            "session_id": "named-child",
        }

    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.spawn_sub_session",
        AsyncMock(side_effect=spawn),
    )

    result = await run_deep_plan(
        parent,
        "Plan it",
        DeepPlanConfig(provider="fable"),
    )

    assert result.provider == "fable"
    assert result.model == "claude-fable-5"
    child_pin.pin.assert_called_once_with("fable")
    parent_pin.pin.assert_not_called()


@pytest.mark.asyncio
async def test_unmounted_fable_never_starts_planner_or_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, _pin = _parent_session(
        {"anthropic": _provider("anthropic", "claude-opus-5")}
    )
    spawn = AsyncMock()
    monkeypatch.setattr("amplifier_app_cli.deep_plan.spawn_sub_session", spawn)
    parent_executor = AsyncMock()

    with pytest.raises(DeepPlanError, match="Mounted provider IDs: anthropic"):
        await execute_deep_plan_turn(
            parent,
            "Implement it",
            DeepPlanConfig(provider="fable"),
            planner_runner=lambda awaitable: awaitable,
            parent_executor=parent_executor,
            on_plan=MagicMock(),
        )

    spawn.assert_not_awaited()
    parent_executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_unapplied_preference_is_detected_before_child_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, _pin = _parent_session(
        {"anthropic": _provider("anthropic", "claude-opus-5")}
    )
    child, _child_pin, _callbacks = _child_session(
        {"anthropic": _provider("anthropic", "claude-opus-5")}
    )
    child_execute = AsyncMock()

    async def spawn(**kwargs: Any) -> dict[str, Any]:
        kwargs["post_initialize_callback"](child)
        await child_execute()
        return {
            "status": "success",
            "output": "wrong-model plan",
            "session_id": "child",
        }

    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.spawn_sub_session",
        AsyncMock(side_effect=spawn),
    )

    with pytest.raises(DeepPlanError, match="still resolves to model.*claude-opus-5"):
        await run_deep_plan(parent, "Plan it", resolve_planner_config({}))

    child_execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "actual", "expected"),
    [
        ("reasoning_effort", "high", "max"),
        ("refusal_fallback_enabled", True, False),
        ("fallback_on_overload", True, False),
    ],
)
async def test_unapplied_preference_config_is_detected_before_child_execute(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    actual: object,
    expected: object,
) -> None:
    parent, _pin = _parent_session(
        {"anthropic": _provider("anthropic", "claude-opus-5")}
    )
    child_provider = _provider("anthropic", "claude-fable-5")
    child_provider.config[setting] = actual
    child, _child_pin, _callbacks = _child_session({"anthropic": child_provider})
    child_execute = AsyncMock()

    async def spawn(**kwargs: Any) -> dict[str, Any]:
        kwargs["post_initialize_callback"](child)
        await child_execute()
        return {
            "status": "success",
            "output": "wrong-config plan",
            "session_id": "child",
        }

    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.spawn_sub_session",
        AsyncMock(side_effect=spawn),
    )

    with pytest.raises(
        DeepPlanError,
        match=rf"{setting}={actual!r}.*expected {expected!r}",
    ):
        await run_deep_plan(parent, "Plan it", resolve_planner_config({}))

    child_execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "message"),
    [
        (None, "without observable provider resolution"),
        (
            {
                "provider": "openai",
                "model": "claude-fable-5",
                "basis": "pinned",
            },
            "unexpected provider route",
        ),
        (
            {
                "provider": "anthropic",
                "model": "claude-opus-5",
                "basis": "pinned",
            },
            "unexpected provider route",
        ),
        (
            {
                "provider": "anthropic",
                "model": "claude-fable-5",
                "basis": "automatic",
            },
            "unexpected provider route",
        ),
    ],
)
async def test_invalid_resolution_discards_plan(
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, str] | None,
    message: str,
) -> None:
    parent, _pin = _parent_session(
        {"anthropic": _provider("anthropic", "claude-opus-5")}
    )
    child, _child_pin, callbacks = _child_session(
        {"anthropic": _provider("anthropic", "claude-fable-5")}
    )

    async def spawn(**kwargs: Any) -> dict[str, Any]:
        kwargs["post_initialize_callback"](child)
        if event is not None:
            await _emit_resolution(callbacks, **event)
        return {
            "status": "success",
            "output": "Untrusted result",
            "session_id": "child",
        }

    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.spawn_sub_session",
        AsyncMock(side_effect=spawn),
    )
    parent_executor = AsyncMock()

    with pytest.raises(DeepPlanError, match=message):
        await execute_deep_plan_turn(
            parent,
            "Implement it",
            resolve_planner_config({}),
            planner_runner=lambda awaitable: awaitable,
            parent_executor=parent_executor,
            on_plan=MagicMock(),
        )

    parent_executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_deep_plan_turn_executes_parent_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, _pin = _parent_session({})
    result = DeepPlanResult(
        plan="A valid plan",
        provider="anthropic",
        model="claude-fable-5",
        session_id="child-123",
    )
    planner = AsyncMock(return_value=result)
    monkeypatch.setattr("amplifier_app_cli.deep_plan.run_deep_plan", planner)
    parent_executor = AsyncMock(return_value=True)
    on_plan = MagicMock()
    task = "expanded task snapshot\n<context_file>same content</context_file>"
    config = resolve_planner_config({})

    returned = await execute_deep_plan_turn(
        parent,
        task,
        config,
        planner_runner=lambda awaitable: awaitable,
        parent_executor=parent_executor,
        on_plan=on_plan,
    )

    assert returned is result
    planner.assert_awaited_once_with(parent, task, config)
    parent_executor.assert_awaited_once()
    assert parent_executor.call_args.args[0].startswith(task)
    on_plan.assert_called_once_with(result)


@pytest.mark.asyncio
async def test_mention_is_read_once_and_same_snapshot_reaches_both_phases(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver

    fixture_content = "ONE_TIME_MENTION_SNAPSHOT"
    fixture = tmp_path / "task.md"
    fixture.write_text(fixture_content)
    app_resolver = AppMentionResolver(bundle_mappings={"testbundle": tmp_path})

    class CountingResolver:
        relative_to = None

        def __init__(self) -> None:
            self.mentions: list[str] = []

        def resolve(self, mention: str) -> Any:
            self.mentions.append(mention)
            return app_resolver.resolve(mention)

    resolver = CountingResolver()
    parent, _pin = _parent_session(
        {"anthropic": _provider("anthropic", "claude-opus-5")}
    )
    parent.coordinator.get_capability.side_effect = lambda name: {
        "mention_resolver": resolver,
        "conversation.provider_pin": _pin,
    }.get(name)
    child, _child_pin, callbacks = _child_session(
        {"anthropic": _provider("anthropic", "claude-fable-5")}
    )
    planner_instruction: str | None = None

    async def spawn(**kwargs: Any) -> dict[str, Any]:
        nonlocal planner_instruction
        planner_instruction = kwargs["instruction"]
        assert kwargs["expand_instruction_mentions"] is False
        kwargs["post_initialize_callback"](child)
        await _emit_resolution(callbacks)
        return {
            "status": "success",
            "output": "Use the captured snapshot.",
            "session_id": "child-mention-once",
        }

    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.spawn_sub_session",
        AsyncMock(side_effect=spawn),
    )
    raw_task = "Implement the requirements in @testbundle:task.md"
    expanded_task = await process_runtime_mentions(parent, raw_task)
    parent_executor = AsyncMock(return_value=True)

    await execute_deep_plan_turn(
        parent,
        expanded_task,
        resolve_planner_config({}),
        planner_runner=lambda awaitable: awaitable,
        parent_executor=parent_executor,
        on_plan=MagicMock(),
    )

    assert resolver.mentions == ["@testbundle:task.md"]
    assert planner_instruction is not None
    assert planner_instruction.count(fixture_content) == 1
    assert parent_executor.call_args.args[0].count(fixture_content) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_during_render", [False, True])
async def test_cancellation_never_executes_parent(
    monkeypatch: pytest.MonkeyPatch,
    cancel_during_render: bool,
) -> None:
    parent, _pin = _parent_session({})
    result = DeepPlanResult(
        plan="A valid plan",
        provider="anthropic",
        model="claude-fable-5",
        session_id="child",
    )
    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.run_deep_plan",
        AsyncMock(return_value=result),
    )
    parent_executor = AsyncMock(return_value=True)

    async def planner_runner(awaitable: Any) -> DeepPlanResult:
        planned = await awaitable
        if not cancel_during_render:
            parent.coordinator.cancellation.is_cancelled = True
        return planned

    def on_plan(_result: DeepPlanResult) -> None:
        if cancel_during_render:
            parent.coordinator.cancellation.is_cancelled = True

    with pytest.raises(asyncio.CancelledError):
        await execute_deep_plan_turn(
            parent,
            "task",
            resolve_planner_config({}),
            planner_runner=planner_runner,
            parent_executor=parent_executor,
            on_plan=on_plan,
        )

    parent_executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_parent_cancellation_result_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, _pin = _parent_session({})
    result = DeepPlanResult(
        plan="A valid plan",
        provider="anthropic",
        model="claude-fable-5",
        session_id="child",
    )
    monkeypatch.setattr(
        "amplifier_app_cli.deep_plan.run_deep_plan",
        AsyncMock(return_value=result),
    )
    parent_executor = AsyncMock(return_value=False)

    with pytest.raises(asyncio.CancelledError):
        await execute_deep_plan_turn(
            parent,
            "task",
            resolve_planner_config({}),
            planner_runner=lambda awaitable: awaitable,
            parent_executor=parent_executor,
            on_plan=MagicMock(),
        )

    parent_executor.assert_awaited_once()


@pytest.mark.asyncio
async def test_interrupt_helper_requests_parent_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = asyncio.Event()

    class Cancellation:
        def __init__(self) -> None:
            self.is_cancelled = False
            self.is_immediate = False
            self.running_tool_names: list[str] = []

        def reset(self) -> None:
            self.is_cancelled = False
            self.is_immediate = False

        def request_graceful(self) -> None:
            self.is_cancelled = True
            stopped.set()

        def request_immediate(self) -> None:
            self.is_immediate = True

    cancellation = Cancellation()
    installed: dict[str, Any] = {}

    def fake_signal(_signal_number: int, handler: Any) -> Any:
        previous = installed.get("handler", signal.SIG_DFL)
        installed["handler"] = handler
        return previous

    monkeypatch.setattr("amplifier_app_cli.interrupt.signal.signal", fake_signal)

    async def work() -> str:
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
    installed["handler"](signal.SIGINT, None)

    assert await task == "cancelled cleanly"
    assert cancellation.is_cancelled is True


def test_command_processor_registers_deep_plan_and_help() -> None:
    processor = CommandProcessor(MagicMock(), "test-bundle")

    action, data = processor.process_input("/deep-plan implement it")

    assert action == "deep_plan"
    assert data["args"] == "implement it"
    assert "/deep-plan" in processor._format_help()
