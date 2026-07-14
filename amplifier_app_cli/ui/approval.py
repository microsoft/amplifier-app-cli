"""CLI approval system implementation using rich terminal UX."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Literal

from rich.console import Console
from rich.prompt import Prompt

logger = logging.getLogger(__name__)

ApprovalHandler = Callable[
    [str, tuple[str, ...], float, Literal["allow", "deny"]], Awaitable[str]
]
_MAX_DECISION_HISTORY = 512
_MAX_APPROVAL_PROMPT = 512


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Bounded evidence of a decision the user made in this session."""

    prompt: str
    choice: str


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
    """Terminal-based approval with Rich formatting and timeout."""

    def __init__(self, *, bypass_permissions: bool = False):
        self.console = Console()
        self.cache: dict[str, str] = {}  # Session-scoped approval cache
        self._handler: ApprovalHandler | None = None
        self._decision_history: list[ApprovalDecision] = []
        self._bypass_permissions = bool(bypass_permissions)

    @property
    def decision_history(self) -> tuple[ApprovalDecision, ...]:
        return tuple(self._decision_history)

    @property
    def bypass_permissions(self) -> bool:
        """Return whether approvals are explicitly being auto-allowed."""
        return self._bypass_permissions

    def bind_handler(self, handler: ApprovalHandler) -> Callable[[], None]:
        """Route approvals through the active interactive surface."""
        if not callable(handler):
            raise TypeError("approval handler must be callable")
        self._handler = handler

        def unbind() -> None:
            if self._handler is handler:
                self._handler = None

        return unbind

    def set_bypass_permissions(self, enabled: bool) -> None:
        """Auto-allow approval requests while the explicit bypass mode is active."""
        self._bypass_permissions = bool(enabled)

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

        if self._bypass_permissions:
            choice = next(
                (option for option in options if option.lower().startswith("allow")),
                options[0],
            )
            self._record_decision(prompt, choice)
            return choice

        if self._handler is not None:
            try:
                async with asyncio.timeout(timeout):
                    choice = await self._handler(
                        prompt,
                        tuple(options),
                        timeout,
                        default,
                    )
            except TimeoutError as error:
                raise ApprovalTimeoutError(
                    f"User approval timeout after {timeout}s"
                ) from error
            if choice not in options:
                raise ValueError("approval handler returned an unknown option")
            self._record_decision(prompt, choice)
            self._cache_choice(cache_key, choice)
            return choice

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
                choice = await asyncio.to_thread(
                    Prompt.ask, "Your choice", choices=options
                )

                # Cache "Allow always" decisions
                self._record_decision(prompt, choice)
                self._cache_choice(cache_key, choice)
                return choice

        except TimeoutError:
            self.console.print(
                f"\n[yellow]⏱  Timeout ({timeout}s) - using default: {default}[/yellow]"
            )
            raise ApprovalTimeoutError(f"User approval timeout after {timeout}s")

    def _cache_choice(self, cache_key: str, choice: str) -> None:
        if choice != "Allow always":
            return
        self.cache[cache_key] = "Allow once"
        self.console.print("[green]✓ Approval cached for this session[/green]")

    def _record_decision(self, prompt: str, choice: str) -> None:
        clean_prompt = " ".join(
            "".join(character for character in prompt if ord(character) >= 32).split()
        )[:_MAX_APPROVAL_PROMPT]
        clean_choice = " ".join(choice.split())[:40]
        if not clean_prompt or not clean_choice:
            return
        self._decision_history.append(ApprovalDecision(clean_prompt, clean_choice))
        if len(self._decision_history) > _MAX_DECISION_HISTORY:
            del self._decision_history[
                : len(self._decision_history) - _MAX_DECISION_HISTORY
            ]
