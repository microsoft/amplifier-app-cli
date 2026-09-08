"""Focused regressions for in-process self-child base prompt inheritance."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.session_runner import register_session_spawning
from amplifier_app_cli.session_spawner import (
    BASE_SYSTEM_PROMPT_BUILDER_CAPABILITY,
    BASE_SYSTEM_PROMPT_BUILDER_ERROR_CAPABILITY,
    spawn_sub_session,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


class _Context:
    def __init__(self, messages=None):
        self.factory = None
        self.messages = list(messages or [])
        self.set_calls = 0

    async def set_system_prompt_factory(self, factory):
        self.factory = factory
        self.set_calls += 1

    async def add_message(self, message):
        self.messages.append(message)

    async def get_messages(self):
        return self.messages


class _StaticContext:
    def __init__(self):
        self.messages = [{"role": "system", "content": "STATIC_SYSTEM"}]

    async def add_message(self, message):
        self.messages.append(message)

    async def get_messages(self):
        return self.messages


def _child(context):
    child = MagicMock()
    child.session_id = "child-id"
    child_context = MagicMock()
    child_context.cancellation = MagicMock()
    child_context.hooks = MagicMock()
    child_context.hooks.register = MagicMock(return_value=lambda: None)
    child_context.mount = AsyncMock()
    child_context.display_system = MagicMock()
    caps = {}

    def register(name, value):
        caps[name] = value

    def get_cap(name):
        return caps.get(name)

    def get(name):
        if name == "context":
            return context
        if name == "hooks":
            return child_context.hooks
        return None

    child_context.register_capability = register
    child_context.get_capability = get_cap
    child_context.get = get
    child.coordinator = child_context
    child.initialize = AsyncMock()

    async def execute(_instruction):
        factory = getattr(context, "factory", None)
        return await factory() if factory else "no factory"

    child.execute = AsyncMock(side_effect=execute)
    child.cleanup = AsyncMock()
    return child, caps


def _parent(clean_builder=None, builder_error=None):
    parent = MagicMock()
    parent.session_id = "parent-id"
    parent.trace_id = "parent-trace"
    parent.loader = None
    parent.config = {"session": {"orchestrator": "loop-basic"}}
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.approval_system = MagicMock()
    coordinator.display_system = MagicMock()
    coordinator.cancellation = MagicMock()
    coordinator.cancellation.register_child = MagicMock()
    coordinator.cancellation.unregister_child = MagicMock()
    coordinator.get = MagicMock(return_value=None)
    coordinator.get_capability = MagicMock(
        side_effect=lambda name: {
            "session.working_dir": "/child/cwd",
            BASE_SYSTEM_PROMPT_BUILDER_CAPABILITY: clean_builder,
            BASE_SYSTEM_PROMPT_BUILDER_ERROR_CAPABILITY: builder_error,
        }.get(name)
    )
    parent.coordinator = coordinator
    return parent


async def test_root_registration_creates_target_bound_factory_from_prepared_bundle():
    """The root capability retains only the prepared bundle, never root context."""
    root = MagicMock()
    registered = {}
    root.coordinator.register_capability.side_effect = (
        lambda name, value: registered.setdefault(name, value)
    )
    prepared = MagicMock()
    prepared.bundle.instruction = "ROOT_BASE"
    prepared.bundle.context = {}
    prepared.bundle._pending_context = {}
    target = MagicMock()
    target.coordinator.get_capability.return_value = "/target/cwd"

    async def target_factory():
        return "ROOT_BASE"

    prepared.create_system_prompt_factory.return_value = target_factory
    register_session_spawning(root, prepared_bundle=prepared)

    factory = registered[BASE_SYSTEM_PROMPT_BUILDER_CAPABILITY](target)
    assert await factory() == "ROOT_BASE"
    assert prepared.create_system_prompt_factory.call_args.args == (target,)
    assert prepared.create_system_prompt_factory.call_args.kwargs["session_cwd"].as_posix() == (
        "/target/cwd"
    )


async def test_source_bearing_root_without_foundation_api_refuses_self_before_child_creation():
    """A missing public Foundation API must not fall back to parent prompt state."""
    root = MagicMock()
    root_capabilities = {}
    root.coordinator.register_capability.side_effect = (
        lambda name, value: root_capabilities.setdefault(name, value)
    )
    prepared = SimpleNamespace(
        bundle=SimpleNamespace(instruction="ROOT_BASE", context=None, _pending_context=None)
    )
    register_session_spawning(root, prepared_bundle=prepared)

    assert BASE_SYSTEM_PROMPT_BUILDER_CAPABILITY not in root_capabilities
    assert BASE_SYSTEM_PROMPT_BUILDER_ERROR_CAPABILITY in root_capabilities

    parent = _parent(
        builder_error=root_capabilities[BASE_SYSTEM_PROMPT_BUILDER_ERROR_CAPABILITY]
    )
    parent_prompt_factory = AsyncMock()
    parent_context = SimpleNamespace(factory=parent_prompt_factory)
    parent.coordinator.get.side_effect = lambda name: (
        parent_context if name == "context" else None
    )

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession") as session_class,
        patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value="child-id",
        ),
        pytest.raises(
            RuntimeError,
            match="PreparedBundle.create_system_prompt_factory.*Upgrade amplifier-foundation",
        ),
    ):
        await spawn_sub_session("self", "TASK", parent, {})

    session_class.assert_not_called()
    parent_prompt_factory.assert_not_awaited()


async def test_self_child_uses_clean_builder_not_parent_factory_and_freezes_once():
    """ROOT_BASE is rendered for the child, never from parent hook state."""
    calls = []

    def clean_builder(target):
        calls.append(target)

        async def factory():
            return "ROOT_BASE"

        return factory

    parent = _parent(clean_builder)
    parent_context = _Context(messages=[{"role": "user", "content": "PARENT_ONLY"}])

    async def parent_factory():
        raise AssertionError("parent factory must never be read by a child")

    parent_context.factory = parent_factory
    parent.coordinator.get.side_effect = lambda name: (
        parent_context if name == "context" else None
    )
    context = _Context()
    child, caps = _child(context)
    store = MagicMock()

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value="child-id",
        ),
        patch("amplifier_app_cli.session_spawner.bridge_child_cost", new_callable=AsyncMock),
        patch("amplifier_app_cli.session_spawner._extract_bundle_context", return_value=None),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
    ):
        result = await spawn_sub_session("self", "TASK", parent, {})

    assert result["output"] == "ROOT_BASE"
    assert calls == [child]
    assert context.set_calls == 1
    assert context.messages == []
    nested_factory = caps[BASE_SYSTEM_PROMPT_BUILDER_CAPABILITY](MagicMock())
    assert await nested_factory() == "ROOT_BASE"
    assert calls == [child], "nested self uses its frozen child snapshot"
    persisted = store.save.call_args_list[0].args[2]
    assert persisted["base-prompt-snapshot"] == {
        "schema": 1,
        "content": "ROOT_BASE",
    }
    assert "base-prompt-snapshot" not in persisted["config"].get(
        "session", {}
    ).get("metadata", {})


async def test_named_persona_wins_over_clean_root_builder():
    """Named agent bodies retain precedence over an inheritable root base."""
    parent = _parent(
        builder_error="self delegation must upgrade Foundation before proceeding"
    )
    context = _Context()
    child, _ = _child(context)

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value="child-id",
        ),
        patch("amplifier_app_cli.session_spawner.bridge_child_cost", new_callable=AsyncMock),
        patch("amplifier_app_cli.session_spawner._extract_bundle_context", return_value=None),
        patch("amplifier_app_cli.session_store.SessionStore"),
    ):
        result = await spawn_sub_session(
            "named", "TASK", parent, {"named": {"instruction": "NAMED_PERSONA"}}
        )

    assert result["output"] == "NAMED_PERSONA"


async def test_tool_root_registration_receives_the_prepared_bundle():
    """Tool invocation registers the same root prompt policy as the interactive runner."""
    tool_mod = importlib.import_module("amplifier_app_cli.commands.tool")
    tool_instance = MagicMock()
    tool_instance.execute = AsyncMock(return_value="tool result")
    session = MagicMock()
    session.initialize = AsyncMock()
    session.cleanup = AsyncMock()
    session.coordinator.get.side_effect = lambda name: (
        {"probe": tool_instance} if name == "tools" else None
    )
    prepared = MagicMock()
    prepared.resolver = MagicMock()
    prepared.create_session = AsyncMock(return_value=session)
    settings = MagicMock()
    settings.get_merged_settings.return_value = {}

    with (
        patch(
            "amplifier_app_cli.runtime.config.resolve_config_async",
            new=AsyncMock(return_value=({}, prepared)),
        ),
        patch("amplifier_app_cli.lib.settings.AppSettings", return_value=settings),
        patch("amplifier_app_cli.commands.tool.inject_user_providers"),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.lib.bundle_loader.AppModuleResolver"),
        patch(
            "amplifier_app_cli.session_runner.register_session_spawning"
        ) as register_spawning,
    ):
        assert await tool_mod._invoke_tool_from_bundle_async(
            "bundle", "probe", {}
        ) == "tool result"

    register_spawning.assert_called_once_with(session, prepared_bundle=prepared)


async def test_self_without_source_preserves_static_context_without_empty_factory(caplog):
    """Missing reconstruction never overwrites a legacy static system message."""
    parent = _parent()
    context = _StaticContext()
    child, _ = _child(context)

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value="child-id",
        ),
        patch("amplifier_app_cli.session_spawner.bridge_child_cost", new_callable=AsyncMock),
        patch("amplifier_app_cli.session_spawner._extract_bundle_context", return_value=None),
        patch("amplifier_app_cli.session_store.SessionStore"),
    ):
        await spawn_sub_session("self", "TASK", parent, {})

    assert context.messages == [{"role": "system", "content": "STATIC_SYSTEM"}]
    assert "without installing an empty system-prompt factory" in caplog.text