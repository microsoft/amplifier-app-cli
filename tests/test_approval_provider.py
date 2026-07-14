from __future__ import annotations

from io import StringIO

import pytest
from amplifier_core import ApprovalRequest
from rich.console import Console

from amplifier_app_cli.approval_provider import CLIApprovalProvider


class RecordingApprovalSystem:
    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.requests = []

    async def request_approval(self, prompt, options, timeout, default):
        self.requests.append((prompt, options, timeout, default))
        return self.choice


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choice", "approved"), (("Allow once", True), ("Deny", False))
)
async def test_core_approval_provider_uses_shared_inline_system(
    choice: str, approved: bool
) -> None:
    system = RecordingApprovalSystem(choice)
    console_output = StringIO()
    provider = CLIApprovalProvider(
        Console(file=console_output, force_terminal=False), system
    )
    request = ApprovalRequest(
        tool_name="shell",
        action="run git status",
        risk_level="medium",
        timeout=42,
    )

    response = await provider.request_approval(request)

    assert response.approved is approved
    assert system.requests == [
        (
            "Allow shell: run git status?",
            ["Allow once", "Deny"],
            42.0,
            "deny",
        )
    ]
    assert console_output.getvalue() == ""
