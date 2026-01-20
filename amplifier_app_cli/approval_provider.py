"""CLI approval provider for interactive user approval."""

import asyncio
import logging

from amplifier_core import ApprovalRequest
from amplifier_core import ApprovalResponse
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

logger = logging.getLogger(__name__)


class CLIApprovalProvider:
    """
    Provides interactive approval via Rich console.

    Implements ApprovalProvider protocol for CLI environments.
    """

    def __init__(self, console: Console):
        """
        Initialize CLI approval provider.

        Args:
            console: Rich console for output
        """
        self.console = console

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """
        Show approval dialog and wait for user response.

        Args:
            request: Approval request with action details

        Returns:
            Approval decision from user

        Raises:
            TimeoutError: If request.timeout expires
        """
        # Build rich panel with request details
        risk_color = self._get_risk_color(request.risk_level)

        panel_content = self._format_request(request, risk_color)

        # Cap width at 78 to fit standard 80-column terminals
        max_width = min(78, self.console.width - 2) if self.console.width else 78
        self.console.print(
            Panel(
                panel_content,
                title="⚠️  Approval Required",
                border_style=risk_color,
                padding=(1, 2),
                width=max_width,
            )
        )

        # Handle timeout if specified
        try:
            if request.timeout is not None:
                # Use asyncio.wait_for with timeout
                approved = await asyncio.wait_for(
                    self._get_user_input(), timeout=request.timeout
                )
            else:
                # No timeout - wait indefinitely
                approved = await self._get_user_input()
        except TimeoutError:
            self.console.print(
                f"\n[yellow]⏱️  Approval timed out after {request.timeout}s[/yellow]"
            )
            raise TimeoutError(f"Approval timed out after {request.timeout}s")

        # Return response
        return ApprovalResponse(
            approved=approved, reason="User approved" if approved else "User denied"
        )

    def _get_risk_color(self, risk_level: str) -> str:
        """Get Rich color for risk level."""
        risk_colors = {
            "low": "green",
            "medium": "yellow",
            "high": "red",
            "critical": "bold red",
        }
        return risk_colors.get(risk_level.lower(), "white")

    def _format_request(self, request: ApprovalRequest, risk_color: str) -> str:
        """Format approval request for display."""
        lines = [
            f"[bold]Tool:[/bold] {request.tool_name}",
            f"[bold]Action:[/bold] {request.action}",
            f"[bold]Risk Level:[/bold] [{risk_color}]{request.risk_level.upper()}[/{risk_color}]",
        ]

        # Add details if present
        if request.details:
            lines.append("")
            lines.append("[bold]Details:[/bold]")
            for key, value in request.details.items():
                # Truncate long values
                value_str = str(value)
                if len(value_str) > 100:
                    value_str = value_str[:97] + "..."
                lines.append(f"  {key}: {value_str}")

        # Add timeout info if present
        if request.timeout is not None:
            lines.append("")
            lines.append(f"[dim]Timeout: {request.timeout}s[/dim]")

        return "\n".join(lines)

    async def _get_user_input(self) -> bool:
        """
        Get yes/no input from user (async).

        Returns:
            True if approved, False if denied
        """
        # Run synchronous console input in thread pool to make it async
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: Confirm.ask("\nApprove this action?", default=False)
        )
        return result
