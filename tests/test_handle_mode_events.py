"""Tests for _handle_mode event emission — payload shape and emit-before-mutation order.

Covers all 7 scenarios specified by Brian's PR #186 review:

1. /mode <name> on  (no prior mode)          → mode:activated  with full payload
2. /mode <name> on  (prior mode)             → mode:changed    with full payload
3. /mode <name> off (currently active)       → mode:cleared    with minimal payload
4. /mode off        (any active mode)        → mode:cleared    with minimal payload
5. /mode <name>     (toggle, no prior)       → mode:activated  with full payload
6. /mode <name>     (toggle, prior ≠ target) → mode:changed    with full payload
7. /mode <name>     (toggle, already active) → mode:cleared    with minimal payload

Every test also asserts that the emit fires BEFORE session_state["active_mode"] is
mutated, i.e. the old value is still visible to listeners at emit time.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# Canonical field values used across all "full-payload" assertions.
_MODE_DESC = "CI investigation"
_MODE_DEFAULT_ACTION = "warn"
_MODE_SAFE_TOOLS = ["read_file", "grep"]
_MODE_WARN_TOOLS = ["bash"]
_MODE_CONFIRM_TOOLS = []
_MODE_BLOCK_TOOLS = []


def _make_mode_def(name: str = "context-intelligence") -> MagicMock:
    """Return a fake ModeDefinition with all fields the payload contracts require."""
    md = MagicMock()
    md.name = name
    md.description = _MODE_DESC
    md.default_action = _MODE_DEFAULT_ACTION
    md.safe_tools = _MODE_SAFE_TOOLS
    md.warn_tools = _MODE_WARN_TOOLS
    md.confirm_tools = _MODE_CONFIRM_TOOLS
    md.block_tools = _MODE_BLOCK_TOOLS
    return md


def _make_cp(
    *,
    active_mode: str | None = None,
    mode_name: str = "context-intelligence",
    mode_def: MagicMock | None = None,
) -> tuple[Any, AsyncMock, list[Any]]:
    """Build a (CommandProcessor, emit_mock, state_at_emit) triple.

    Returns:
        cp: CommandProcessor instance
        emit_mock: AsyncMock wired onto hooks.emit
        state_snapshots: list that the emit side-effect appends to;
            each entry is the value of session_state["active_mode"] captured
            at the moment emit() is called — used to verify emit-before-mutation.
    """
    from amplifier_app_cli.main import CommandProcessor

    mock_session = MagicMock()
    mock_session.coordinator = MagicMock()

    session_state: dict[str, Any] = {
        "active_mode": active_mode,
        "mode_hooks": MagicMock(),
    }

    # Wire discovery
    mock_discovery = MagicMock()
    if mode_def is not None:
        mock_discovery.find.return_value = mode_def
    else:
        mock_discovery.find.return_value = _make_mode_def(mode_name)
    session_state["mode_discovery"] = mock_discovery

    mock_session.coordinator.session_state = session_state

    # Capture session_state["active_mode"] at the moment emit() fires
    state_snapshots: list[Any] = []

    async def _capture_emit(event: str, payload: dict) -> None:  # noqa: ARG001
        state_snapshots.append(session_state.get("active_mode"))

    emit_mock = AsyncMock(side_effect=_capture_emit)
    mock_session.coordinator.hooks.emit = emit_mock

    cp = CommandProcessor(mock_session, "test-bundle")
    return cp, emit_mock, state_snapshots


# ---------------------------------------------------------------------------
# Test 1 — explicit `on`, no prior mode  →  mode:activated with full payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_mode_emits_activated_on_explicit_on() -> None:
    """/mode context-intelligence on, no prior mode → mode:activated with full payload."""
    cp, emit_mock, state_snapshots = _make_cp(active_mode=None)

    await cp._handle_mode("context-intelligence on")

    emit_mock.assert_called_once()
    event, payload = emit_mock.call_args[0]

    assert event == "mode:activated"
    # Canonical fields
    assert payload["name"] == "context-intelligence"
    assert payload["mode"] == "context-intelligence"
    assert payload["description"] == _MODE_DESC
    assert payload["default_action"] == _MODE_DEFAULT_ACTION
    assert payload["safe_tools"] == _MODE_SAFE_TOOLS
    assert payload["warn_tools"] == _MODE_WARN_TOOLS
    assert payload["confirm_tools"] == _MODE_CONFIRM_TOOLS
    assert payload["block_tools"] == _MODE_BLOCK_TOOLS

    # Emit-before-mutation: active_mode was None when emit fired
    assert state_snapshots == [None], (
        f"Expected active_mode=None at emit time, got {state_snapshots}"
    )


# ---------------------------------------------------------------------------
# Test 2 — explicit `on`, prior mode present  →  mode:changed with full payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_mode_emits_changed_on_explicit_on_switching() -> None:
    """/mode context-intelligence on, prior mode=other-mode → mode:changed full payload."""
    cp, emit_mock, state_snapshots = _make_cp(active_mode="other-mode")

    await cp._handle_mode("context-intelligence on")

    emit_mock.assert_called_once()
    event, payload = emit_mock.call_args[0]

    assert event == "mode:changed"
    # Canonical fields
    assert payload["old"] == "other-mode"
    assert payload["new"] == "context-intelligence"
    assert payload["from_mode"] == "other-mode"
    assert payload["to_mode"] == "context-intelligence"
    assert payload["description"] == _MODE_DESC
    assert payload["default_action"] == _MODE_DEFAULT_ACTION
    assert payload["safe_tools"] == _MODE_SAFE_TOOLS
    assert payload["warn_tools"] == _MODE_WARN_TOOLS
    assert payload["confirm_tools"] == _MODE_CONFIRM_TOOLS
    assert payload["block_tools"] == _MODE_BLOCK_TOOLS

    # Emit-before-mutation: active_mode was still "other-mode" when emit fired
    assert state_snapshots == ["other-mode"], (
        f"Expected active_mode='other-mode' at emit time, got {state_snapshots}"
    )


# ---------------------------------------------------------------------------
# Test 3 — explicit `off`, currently active  →  mode:cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_mode_emits_cleared_on_explicit_off() -> None:
    """/mode context-intelligence off, currently active → mode:cleared."""
    cp, emit_mock, state_snapshots = _make_cp(active_mode="context-intelligence")

    await cp._handle_mode("context-intelligence off")

    emit_mock.assert_called_once()
    event, payload = emit_mock.call_args[0]

    assert event == "mode:cleared"
    assert payload["name"] == "context-intelligence"
    assert payload["previous_mode"] == "context-intelligence"

    # Emit-before-mutation: active_mode was still "context-intelligence"
    assert state_snapshots == ["context-intelligence"], (
        f"Expected active_mode='context-intelligence' at emit time, got {state_snapshots}"
    )


# ---------------------------------------------------------------------------
# Test 4 — bare `/mode off`  →  mode:cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_mode_emits_cleared_on_bare_off() -> None:
    """/mode off, current mode=context-intelligence → mode:cleared."""
    cp, emit_mock, state_snapshots = _make_cp(active_mode="context-intelligence")

    await cp._handle_mode("off")

    emit_mock.assert_called_once()
    event, payload = emit_mock.call_args[0]

    assert event == "mode:cleared"
    assert payload["name"] == "context-intelligence"
    assert payload["previous_mode"] == "context-intelligence"

    # Emit-before-mutation: active_mode was still "context-intelligence"
    assert state_snapshots == ["context-intelligence"], (
        f"Expected active_mode='context-intelligence' at emit time, got {state_snapshots}"
    )


# ---------------------------------------------------------------------------
# Test 5 — toggle, no prior mode  →  mode:activated with full payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_mode_emits_activated_on_toggle_no_prior() -> None:
    """/mode context-intelligence (toggle), no prior mode → mode:activated full payload."""
    cp, emit_mock, state_snapshots = _make_cp(active_mode=None)

    await cp._handle_mode("context-intelligence")

    emit_mock.assert_called_once()
    event, payload = emit_mock.call_args[0]

    assert event == "mode:activated"
    assert payload["name"] == "context-intelligence"
    assert payload["mode"] == "context-intelligence"
    assert payload["description"] == _MODE_DESC
    assert payload["default_action"] == _MODE_DEFAULT_ACTION
    assert payload["safe_tools"] == _MODE_SAFE_TOOLS
    assert payload["warn_tools"] == _MODE_WARN_TOOLS
    assert payload["confirm_tools"] == _MODE_CONFIRM_TOOLS
    assert payload["block_tools"] == _MODE_BLOCK_TOOLS

    # Emit-before-mutation: active_mode was None when emit fired
    assert state_snapshots == [None], (
        f"Expected active_mode=None at emit time, got {state_snapshots}"
    )


# ---------------------------------------------------------------------------
# Test 6 — toggle, prior ≠ target  →  mode:changed with full payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_mode_emits_changed_on_toggle_switching() -> None:
    """/mode context-intelligence (toggle), prior=other-mode → mode:changed full payload."""
    cp, emit_mock, state_snapshots = _make_cp(active_mode="other-mode")

    await cp._handle_mode("context-intelligence")

    emit_mock.assert_called_once()
    event, payload = emit_mock.call_args[0]

    assert event == "mode:changed"
    assert payload["old"] == "other-mode"
    assert payload["new"] == "context-intelligence"
    assert payload["from_mode"] == "other-mode"
    assert payload["to_mode"] == "context-intelligence"
    assert payload["description"] == _MODE_DESC
    assert payload["default_action"] == _MODE_DEFAULT_ACTION
    assert payload["safe_tools"] == _MODE_SAFE_TOOLS
    assert payload["warn_tools"] == _MODE_WARN_TOOLS
    assert payload["confirm_tools"] == _MODE_CONFIRM_TOOLS
    assert payload["block_tools"] == _MODE_BLOCK_TOOLS

    # Emit-before-mutation: active_mode was still "other-mode" when emit fired
    assert state_snapshots == ["other-mode"], (
        f"Expected active_mode='other-mode' at emit time, got {state_snapshots}"
    )


# ---------------------------------------------------------------------------
# Test 7 — toggle, already active  →  mode:cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_mode_emits_cleared_on_toggle_already_active() -> None:
    """/mode context-intelligence (toggle), already active → mode:cleared."""
    cp, emit_mock, state_snapshots = _make_cp(active_mode="context-intelligence")

    await cp._handle_mode("context-intelligence")

    emit_mock.assert_called_once()
    event, payload = emit_mock.call_args[0]

    assert event == "mode:cleared"
    assert payload["name"] == "context-intelligence"
    assert payload["previous_mode"] == "context-intelligence"

    # Emit-before-mutation: active_mode was still "context-intelligence"
    assert state_snapshots == ["context-intelligence"], (
        f"Expected active_mode='context-intelligence' at emit time, got {state_snapshots}"
    )


def _make_builtin_cp(ui_mode: str | None = None) -> Any:
    from amplifier_app_cli.main import CommandProcessor

    session = MagicMock()
    session.coordinator = MagicMock()
    session.coordinator.session_state = {
        "active_mode": None,
        "ui.active_mode": ui_mode,
    }
    session.coordinator.get_capability.return_value = None
    session.coordinator.hooks.emit = AsyncMock()
    return CommandProcessor(session, "test-bundle")


@pytest.mark.asyncio
async def test_builtin_mode_query_reports_tui_mode_without_discovery() -> None:
    cp = _make_builtin_cp("plan")
    state_before = dict(cp.session.coordinator.session_state)

    result = await cp._handle_mode("")

    assert result == "Active mode: plan"
    assert cp.session.coordinator.session_state == state_before
    cp.session.coordinator.hooks.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_builtin_mode_switch_does_not_require_bundle_discovery() -> None:
    cp = _make_builtin_cp("chat")

    result = await cp._handle_mode("auto")

    assert result.startswith("Mode: auto")
    assert cp.session.coordinator.session_state["ui.active_mode"] == "auto"
    assert cp.session.coordinator.session_state["active_mode"] is None
    cp.session.coordinator.hooks.emit.assert_not_awaited()


def test_builtin_mode_shortcuts_and_completions_are_always_available() -> None:
    cp = _make_builtin_cp()

    for name in ("chat", "plan", "brainstorm", "build", "auto"):
        action, data = cp.process_input(f"/{name}")
        assert action == "handle_mode"
        assert data["args"] == name
        assert name in cp._get_mode_completion_names()


@pytest.mark.asyncio
async def test_mode_query_does_not_reapply_trust_profile() -> None:
    from amplifier_app_cli.main import _apply_ui_mode_transition
    from amplifier_app_cli.ui.interaction_state import TrustState
    from amplifier_app_cli.ui.mode_profiles import ModeProfileRegistry
    from amplifier_app_cli.ui.mode_profiles import ModeRuntimeBinding

    state = {"ui.active_mode": "chat"}
    coordinator = MagicMock()
    coordinator.session_state = state
    coordinator.get.return_value = None
    coordinator.get_capability.return_value = None
    trust = TrustState(initial="bypass")
    profiles = ModeProfileRegistry()
    binding = ModeRuntimeBinding(coordinator, profiles)

    selected = await _apply_ui_mode_transition(
        state,
        "chat",
        profiles,
        binding,
        {"last": "chat"},
    )

    assert selected == "chat"
    assert trust.active.name == "bypass"
    assert binding.snapshot is None


@pytest.mark.asyncio
async def test_changed_builtin_mode_applies_runtime_profile() -> None:
    from amplifier_app_cli.main import _apply_ui_mode_transition
    from amplifier_app_cli.ui.interaction_state import TrustState
    from amplifier_app_cli.ui.mode_profiles import ModeProfileRegistry
    from amplifier_app_cli.ui.mode_profiles import ModeRuntimeBinding

    state = {"ui.active_mode": "plan"}
    coordinator = MagicMock()
    coordinator.session_state = state
    coordinator.get.return_value = None
    coordinator.get_capability.return_value = None
    trust = TrustState(initial="bypass")
    profiles = ModeProfileRegistry()
    binding = ModeRuntimeBinding(coordinator, profiles)
    active = {"last": "chat"}

    selected = await _apply_ui_mode_transition(
        state,
        "chat",
        profiles,
        binding,
        active,
        trust,
    )

    assert selected == "plan"
    assert trust.active.name == "plan"
    assert binding.snapshot is not None
    assert binding.snapshot.mode.value == "plan"
    assert active["last"] == "plan"
