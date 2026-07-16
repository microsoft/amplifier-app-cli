"""Focused tests for the interactive session resource factory."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from amplifier_app_cli.runtime.interactive_resources import (
    InteractiveResourceDependencies,
)
from amplifier_app_cli.runtime.interactive_resources import InteractiveResourceRequest
from amplifier_app_cli.runtime.interactive_resources import (
    create_interactive_session_resources,
)
from amplifier_app_cli.ui.interaction_state import TRUST_POLICY_VERSION


class _ApprovalSystem:
    def __init__(self) -> None:
        self.bypass_permissions = False
        self.decision_history: tuple[object, ...] = ()
        self.selections: list[bool] = []

    def set_bypass_permissions(self, enabled: bool) -> None:
        self.bypass_permissions = enabled
        self.selections.append(enabled)


class _Coordinator:
    def __init__(self) -> None:
        self.session_state: dict[str, object] = {}
        self.approval_system = _ApprovalSystem()
        self.todo_state = None
        self.capabilities: dict[str, object] = {}
        self.context = MagicMock()
        self.context.get_messages = AsyncMock(return_value=[])

    def get(self, name: str):
        return {
            "context": self.context,
            "hooks": None,
            "orchestrator": None,
            "providers": {},
        }.get(name)

    def register_capability(self, name: str, value: object) -> None:
        self.capabilities[name] = value

    def get_capability(self, name: str):
        return self.capabilities.get(name)


class _Session:
    def __init__(self) -> None:
        self.session_id = "resource-session"
        self.coordinator = _Coordinator()


class _CommandProcessor:
    def __init__(self, session, bundle_name, *, mcp_prompts=()) -> None:
        self.session = session
        self.bundle_name = bundle_name
        self.mcp_prompts = mcp_prompts
        self.configurator = None
        self.mode_calls: list[str] = []

    async def _handle_mode(self, value: str) -> object:
        self.mode_calls.append(value)
        return value


def _dependencies(session: _Session, store: MagicMock):
    initialized = SimpleNamespace(
        session=session,
        session_id=session.session_id,
        configurator=None,
        cleanup=AsyncMock(),
    )
    return InteractiveResourceDependencies(
        console=Console(file=StringIO(), force_terminal=False),
        input_stream=StringIO(),
        create_initialized_session=AsyncMock(return_value=initialized),
        session_store_factory=MagicMock(return_value=store),
        command_processor_factory=_CommandProcessor,
        supports_layered_ui=MagicMock(return_value=False),
        get_layered_app=lambda: None,
    )


@pytest.mark.asyncio
async def test_factory_registers_one_cohesive_safe_default_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _Session()
    store = MagicMock()
    monkeypatch.setattr(
        "amplifier_app_cli.incremental_save.register_incremental_save",
        MagicMock(),
    )
    # Isolate from whatever config.tui.* the real ambient settings.yaml (repo
    # project scope, user global scope) might declare -- this test asserts
    # the safe chat/chat default, which is only true with no startup config.
    monkeypatch.setattr(
        "amplifier_app_cli.runtime.interactive_resources.AppSettings",
        lambda: _FakeAppSettings({}),
    )

    resources = await create_interactive_session_resources(
        InteractiveResourceRequest(
            config={},
            search_paths=[tmp_path],
            verbose=False,
            bundle_name="foundation",
        ),
        _dependencies(session, store),
    )

    assert resources.session is session
    assert resources.session_id == "resource-session"
    assert resources.trust_state.active.name == "chat"
    assert resources.approval_system is session.coordinator.approval_system
    assert session.coordinator.approval_system.bypass_permissions is False
    assert resources.layered_ui_enabled is False
    assert resources.task_tracker is None
    assert {
        "ui.trust_state",
        "ui.notices",
        "ui.outcome_ledger",
        "ui.evidence_links",
        "ui.needs_you",
        "ui.steering_queue",
        "ui.action_governor",
        "ui.session_commands",
        "ui.step_boundary",
        "ui.governance_hook",
    } <= session.coordinator.capabilities.keys()
    assert resources.cleanup.collect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_version", "expected_posture", "expected_bypass"),
    [
        (None, "chat", False),
        (TRUST_POLICY_VERSION, "bypass", True),
    ],
)
async def test_resume_migrates_legacy_bypass_but_restores_explicit_v2_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy_version: int | None,
    expected_posture: str,
    expected_bypass: bool,
) -> None:
    session = _Session()
    store = MagicMock()
    store.get_metadata.return_value = {
        "permission_posture": "bypass",
        "permission_profile": {"name": "bypass"},
        "permission_policy_version": policy_version,
        "show_debug": True,
        "session_cost_usd": "1.25",
    }
    monkeypatch.setattr(
        "amplifier_app_cli.incremental_save.register_incremental_save",
        MagicMock(),
    )

    resources = await create_interactive_session_resources(
        InteractiveResourceRequest(
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=session.session_id,
            bundle_name="foundation",
            initial_transcript=[{"role": "user", "content": "resume"}],
        ),
        _dependencies(session, store),
    )

    assert resources.session_config.is_resume is True
    assert resources.trust_state.active.name == expected_posture
    assert resources.trust_state.bypass_permissions is expected_bypass
    assert session.coordinator.approval_system.bypass_permissions is expected_bypass
    assert resources.active_mode() == "chat"
    assert session.coordinator.session_state["ui.show_debug"] is True


class _FakeAppSettings:
    """Stand-in for AppSettings that only implements get_tui_startup_config()."""

    def __init__(self, tui_config: dict[str, object]) -> None:
        self._tui_config = tui_config

    def get_tui_startup_config(self) -> dict[str, object]:
        return self._tui_config


@pytest.mark.asyncio
async def test_configured_startup_preset_seeds_fresh_session_and_survives_mode_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.tui.startup_mode=auto / startup_permission=bypass on a brand-new
    (non-resumed) session applies both, latches the explicit-trust flag per
    ADR-0005 ("choosing the bypass permissions preset" is the explicit
    action), and a later mode-only cycle must not revert the posture."""
    session = _Session()
    store = MagicMock()
    monkeypatch.setattr(
        "amplifier_app_cli.incremental_save.register_incremental_save",
        MagicMock(),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.runtime.interactive_resources.AppSettings",
        lambda: _FakeAppSettings(
            {"startup_mode": "auto", "startup_permission": "bypass"}
        ),
    )

    resources = await create_interactive_session_resources(
        InteractiveResourceRequest(
            config={},
            search_paths=[tmp_path],
            verbose=False,
            bundle_name="foundation",
        ),
        _dependencies(session, store),
    )

    assert resources.session_config.is_resume is False
    assert resources.active_mode() == "auto"
    assert resources.trust_state.active.name == "bypass"
    assert resources.trust_state.bypass_permissions is True
    assert session.coordinator.approval_system.bypass_permissions is True

    # Mode cycling (Shift-Tab) must never silently revert the explicit posture.
    await resources.cycle_mode()
    assert resources.trust_state.active.name == "bypass"


@pytest.mark.asyncio
async def test_no_startup_config_leaves_fresh_session_chat_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default behavior with no config.tui section: unchanged (chat/chat)."""
    session = _Session()
    store = MagicMock()
    monkeypatch.setattr(
        "amplifier_app_cli.incremental_save.register_incremental_save",
        MagicMock(),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.runtime.interactive_resources.AppSettings",
        lambda: _FakeAppSettings({}),
    )

    resources = await create_interactive_session_resources(
        InteractiveResourceRequest(
            config={},
            search_paths=[tmp_path],
            verbose=False,
            bundle_name="foundation",
        ),
        _dependencies(session, store),
    )

    assert resources.active_mode() == "chat"
    assert resources.trust_state.active.name == "chat"
    assert session.coordinator.approval_system.bypass_permissions is False


@pytest.mark.asyncio
async def test_invalid_startup_config_falls_back_to_chat_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid values are rejected safely -- fall back to the safe chat/chat
    default rather than guessing or failing the session start."""
    session = _Session()
    store = MagicMock()
    monkeypatch.setattr(
        "amplifier_app_cli.incremental_save.register_incremental_save",
        MagicMock(),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.runtime.interactive_resources.AppSettings",
        lambda: _FakeAppSettings(
            {"startup_mode": "godmode", "startup_permission": "nope"}
        ),
    )

    resources = await create_interactive_session_resources(
        InteractiveResourceRequest(
            config={},
            search_paths=[tmp_path],
            verbose=False,
            bundle_name="foundation",
        ),
        _dependencies(session, store),
    )

    assert resources.active_mode() == "chat"
    assert resources.trust_state.active.name == "chat"
    assert session.coordinator.approval_system.bypass_permissions is False


@pytest.mark.asyncio
async def test_startup_config_does_not_override_resumed_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resumed session's persisted state wins over the app-wide startup
    default -- resuming is the user actively choosing to continue whatever
    mode/posture that session already had."""
    session = _Session()
    store = MagicMock()
    store.get_metadata.return_value = {
        "permission_posture": "chat",
        "permission_profile": {"name": "chat"},
        "permission_policy_version": TRUST_POLICY_VERSION,
    }
    monkeypatch.setattr(
        "amplifier_app_cli.incremental_save.register_incremental_save",
        MagicMock(),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.runtime.interactive_resources.AppSettings",
        lambda: _FakeAppSettings(
            {"startup_mode": "auto", "startup_permission": "bypass"}
        ),
    )

    resources = await create_interactive_session_resources(
        InteractiveResourceRequest(
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=session.session_id,
            bundle_name="foundation",
            initial_transcript=[{"role": "user", "content": "resume"}],
        ),
        _dependencies(session, store),
    )

    assert resources.session_config.is_resume is True
    assert resources.active_mode() == "chat"
    assert resources.trust_state.active.name == "chat"


def test_cleanup_collection_preserves_named_then_repl_order() -> None:
    from amplifier_app_cli.runtime.interactive_resource_setup import (
        InteractiveCleanupCallbacks,
    )

    calls: list[str] = []

    def callback(name: str):
        return lambda: calls.append(name)

    cleanup = InteractiveCleanupCallbacks(
        task_tracker=callback("task"),
        step_boundary=callback("step"),
        governance=callback("governance"),
        approval_trust=callback("trust"),
    )
    callbacks = cleanup.collect(callback("repl"), callback("title"))
    for item in callbacks:
        item()

    assert calls == ["task", "step", "governance", "trust", "repl", "title"]
