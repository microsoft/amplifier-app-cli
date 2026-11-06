"""CLI approval system implementation using rich terminal UX."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

from rich.console import Console
from rich.prompt import Prompt

if TYPE_CHECKING:
    from amplifier_app_cli.stdin_arbiter import StdinArbiter

logger = logging.getLogger(__name__)


# Import exception from kernel for reuse
if TYPE_CHECKING:
    from amplifier_core.approval import ApprovalTimeoutError
else:
    try:
        from amplifier_core.approval import ApprovalTimeoutError
    except ImportError:
        # Fallback for standalone usage
        class ApprovalTimeoutError(Exception):
            """Raised when user approval times out."""

            pass


class CLIApprovalSystem:
    """Terminal-based approval with Rich formatting and timeout.

    When an ``arbiter`` is provided, the approval prompt claims exclusive
    stdin access before reading input so the mid-turn steering reader
    suspends for the duration of the prompt.  Without this coordination
    the steering reader can consume the approval response, leaving the
    approval waiting indefinitely (up to its timeout).
    """

    def __init__(self, arbiter: StdinArbiter | None = None):
        self.console = Console()
        self.cache: dict[str, str] = {}  # Session-scoped approval cache
        self._arbiter = arbiter

    async def request_approval(
        self,
        prompt: str,
        options: list[str],
        timeout: float,
        default: Literal["allow", "deny"],
    ) -> str:
        """
        Show approval prompt in terminal with timeout.

        Args:
            prompt: Question to ask user
            options: Available choices
            timeout: Seconds to wait
            default: Action on timeout ("allow" or "deny")

        Returns:
            Selected option

        Raises:
            ApprovalTimeoutError: User didn't respond within timeout
        """
        # Check cache (for "Allow always" decisions)
        cache_key = f"{prompt}:{','.join(options)}"
        if cache_key in self.cache:
            cached_decision = self.cache[cache_key]
            self.console.print(
                f"[dim]Using cached approval decision: {cached_decision}[/dim]"
            )
            return cached_decision

        # Claim stdin so the steering reader suspends and cannot consume
        # keystrokes meant for this prompt.  Mirrors CLIApprovalProvider's
        # begin_approval/end_approval pattern.
        if self._arbiter is not None:
            self._arbiter.begin_approval()
        try:
            choice = await self._prompt_user(prompt, options, timeout, default)

            # Cache "Allow always" decisions
            if choice == "Allow always":
                self.cache[cache_key] = "Allow once"  # Cache as simplified "allow"
                self.console.print(
                    "[green]✓ Approval cached for this session[/green]"
                )

            return choice
        finally:
            if self._arbiter is not None:
                self._arbiter.end_approval()

    async def _prompt_user(
        self,
        prompt: str,
        options: list[str],
        timeout: float,
        default: Literal["allow", "deny"],
    ) -> str:
        """Display the approval prompt and collect user input."""
        # Display prompt
        self.console.print()
        self.console.print("[yellow]⚠️  Hook Approval Required[/yellow]")
        self.console.print(f"\n{prompt}")
        self.console.print(f"\nOptions: {', '.join(options)}")
        self.console.print(f"[dim]Timeout in {timeout}s, defaults to: {default}[/dim]")
        self.console.print()

        # Get user input with timeout
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.to_thread(
                    Prompt.ask, "Your choice", choices=options
                )
        except TimeoutError:
            self.console.print(
                f"\n[yellow]⏱  Timeout ({timeout}s) - using default: {default}[/yellow]"
            )
            raise ApprovalTimeoutError(f"User approval timeout after {timeout}s")
