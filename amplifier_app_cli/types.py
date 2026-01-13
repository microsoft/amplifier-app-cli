"""Protocol-based type definitions for CLI function signatures.

This module provides Protocol classes for the main entry point functions,
enabling proper static type checking at call sites. These replace the
weaker `Callable[...]` type aliases that can't express optional/keyword parameters.

Usage:
    from amplifier_app_cli.types import InteractiveChatProtocol, ExecuteSingleProtocol
"""

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle


class InteractiveChatProtocol(Protocol):
    """Protocol for interactive_chat function signature.

    Defines the contract for the main interactive REPL entry point.
    Supports both new sessions and resume mode via optional initial_transcript.
    """

    async def __call__(
        self,
        config: dict,
        search_paths: list[Path],
        verbose: bool,
        session_id: str | None = None,
        profile_name: str = "unknown",
        prepared_bundle: "PreparedBundle | None" = None,
        initial_prompt: str | None = None,
        initial_transcript: list[dict] | None = None,
    ) -> None:
        """Run an interactive chat session.

        Args:
            config: Resolved mount plan configuration
            search_paths: Module search paths
            verbose: Enable verbose output
            session_id: Optional session ID (generated if not provided)
            profile_name: Bundle name (e.g., "bundle:foundation")
            prepared_bundle: PreparedBundle for bundle mode
            initial_prompt: Optional prompt to auto-execute
            initial_transcript: If provided, restore this transcript (resume mode)
        """
        ...


class ExecuteSingleProtocol(Protocol):
    """Protocol for execute_single function signature.

    Defines the contract for single-shot prompt execution.
    Supports both new sessions and resume mode via optional initial_transcript.
    """

    async def __call__(
        self,
        prompt: str,
        config: dict,
        search_paths: list[Path],
        verbose: bool,
        session_id: str | None = None,
        profile_name: str = "unknown",
        output_format: str = "text",
        prepared_bundle: "PreparedBundle | None" = None,
        initial_transcript: list[dict] | None = None,
    ) -> None:
        """Execute a single prompt and exit.

        Args:
            prompt: The user prompt to execute
            config: Effective configuration dict
            search_paths: Paths for module resolution
            verbose: Enable verbose output
            session_id: Optional session ID (generated if None)
            profile_name: Bundle name for metadata (e.g., "bundle:foundation")
            output_format: Output format (text, json, json-trace)
            prepared_bundle: PreparedBundle for bundle mode
            initial_transcript: If provided, restore this transcript (resume mode)
        """
        ...


class SearchPathProviderProtocol(Protocol):
    """Protocol for module search path provider functions."""

    def __call__(self) -> list[Path]:
        """Return list of paths to search for modules."""
        ...


__all__ = [
    "InteractiveChatProtocol",
    "ExecuteSingleProtocol",
    "SearchPathProviderProtocol",
]
