"""Focused coverage for interactive ``/status`` mode reporting."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from amplifier_app_cli.ui.command_sessions import CommandSessionMixin


class _Coordinator:
    def __init__(self, state: dict[str, Any]) -> None:
        self.session_id = "status-session"
        self.session_state = state

    def get(self, _name: str) -> None:
        return None


class _StatusCommands(CommandSessionMixin):
    def __init__(self, state: dict[str, Any]) -> None:
        self.session = SimpleNamespace(coordinator=_Coordinator(state))
        self.bundle_name = "foundation"


@pytest.mark.asyncio
async def test_status_reports_active_ui_mode_without_bundle_mode() -> None:
    commands = _StatusCommands({"active_mode": None, "ui.active_mode": "auto"})

    result = await commands._get_status()

    assert "- Mode: `auto`" in result


@pytest.mark.asyncio
async def test_status_preserves_active_bundle_mode() -> None:
    commands = _StatusCommands(
        {"active_mode": "context-intelligence", "ui.active_mode": "auto"}
    )

    result = await commands._get_status()

    assert "- Mode: `context-intelligence`" in result
