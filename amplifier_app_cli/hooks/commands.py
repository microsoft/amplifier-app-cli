"""Slash commands for hooks management.

Provides interactive commands for:
- /hooks - List loaded hooks and their status
- /hook-enable - Enable a hook
- /hook-disable - Disable a hook
- /hook-stats - Show hook execution statistics
"""

from __future__ import annotations

import logging
from typing import Any

from rich.console import Console
from rich.table import Table

from .manager import HooksManager

logger = logging.getLogger(__name__)


class HookCommands:
    """Slash command handlers for hooks management."""

    def __init__(self, manager: HooksManager, console: Console):
        """Initialize hook commands.

        Args:
            manager: HooksManager instance
            console: Rich console for output
        """
        self.manager = manager
        self.console = console

    def handle_hooks(self, args: str) -> str:
        """Handle /hooks command - list loaded hooks.

        Args:
            args: Optional filter (e.g., "enabled", "disabled", event name)

        Returns:
            Status message (empty if output printed directly)
        """
        hooks = self.manager.list_hooks()

        if not hooks:
            return "No hooks loaded"

        # Filter if requested
        if args:
            args_lower = args.lower()
            if args_lower == "enabled":
                hooks = [h for h in hooks if h["enabled"]]
            elif args_lower == "disabled":
                hooks = [h for h in hooks if not h["enabled"]]
            else:
                # Filter by event
                hooks = [h for h in hooks if args in (h.get("events") or [])]

        if not hooks:
            return f"No hooks matching '{args}'"

        # Build table
        table = Table(title="Loaded Hooks", show_header=True)
        table.add_column("Name", style="cyan")
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Pri")
        table.add_column("Events", style="dim")
        table.add_column("Calls", justify="right")

        for hook in hooks:
            status = "[green]✓[/green]" if hook["enabled"] else "[red]✗[/red]"
            events = ", ".join(hook.get("events") or ["*"])
            table.add_row(
                hook["name"],
                hook["type"],
                status,
                str(hook["priority"]),
                events[:30],
                str(hook["calls"]),
            )

        self.console.print()
        self.console.print(table)
        self.console.print()

        return ""

    def handle_hook_enable(self, args: str) -> str:
        """Handle /hook-enable command - enable a hook.

        Args:
            args: Hook name to enable

        Returns:
            Status message
        """
        if not args:
            return "Usage: /hook-enable <hook-name>"

        if self.manager.enable_hook(args):
            return f"✓ Enabled hook: {args}"
        else:
            return f"Error: Hook '{args}' not found"

    def handle_hook_disable(self, args: str) -> str:
        """Handle /hook-disable command - disable a hook.

        Args:
            args: Hook name to disable

        Returns:
            Status message
        """
        if not args:
            return "Usage: /hook-disable <hook-name>"

        if self.manager.disable_hook(args):
            return f"✓ Disabled hook: {args}"
        else:
            return f"Error: Hook '{args}' not found"

    def handle_hook_stats(self, args: str) -> str:
        """Handle /hook-stats command - show execution statistics.

        Args:
            args: Optional hook name for detailed stats

        Returns:
            Status message (empty if output printed directly)
        """
        stats = self.manager.get_stats()

        if not stats:
            return "No hook statistics available"

        if args:
            # Show stats for specific hook
            hook_stats = stats.get(args)
            if not hook_stats:
                return f"No stats for hook '{args}'"

            self.console.print(f"\n[bold]Statistics for {args}[/bold]")
            self.console.print(f"  Calls: {hook_stats.get('calls', 0)}")
            self.console.print(f"  Errors: {hook_stats.get('errors', 0)}")
            total_ms = hook_stats.get("total_duration_ms", 0)
            calls = hook_stats.get("calls", 0)
            avg_ms = total_ms / calls if calls > 0 else 0
            self.console.print(f"  Total time: {total_ms:.1f}ms")
            self.console.print(f"  Avg time: {avg_ms:.1f}ms")
            self.console.print()
            return ""

        # Show summary table
        table = Table(title="Hook Statistics", show_header=True)
        table.add_column("Hook", style="cyan")
        table.add_column("Calls", justify="right")
        table.add_column("Errors", justify="right")
        table.add_column("Total (ms)", justify="right")
        table.add_column("Avg (ms)", justify="right")

        for name, hook_stats in sorted(stats.items()):
            calls = hook_stats.get("calls", 0)
            errors = hook_stats.get("errors", 0)
            total_ms = hook_stats.get("total_duration_ms", 0)
            avg_ms = total_ms / calls if calls > 0 else 0

            error_style = "red" if errors > 0 else "dim"
            table.add_row(
                name,
                str(calls),
                f"[{error_style}]{errors}[/{error_style}]",
                f"{total_ms:.1f}",
                f"{avg_ms:.1f}",
            )

        self.console.print()
        self.console.print(table)
        self.console.print()

        return ""

    def get_commands(self) -> dict[str, dict[str, Any]]:
        """Get command definitions for registration.

        Returns:
            Dict of command name -> {action, description}
        """
        return {
            "/hooks": {
                "action": "hooks",
                "description": "List loaded hooks: /hooks [enabled|disabled|event]",
            },
            "/hook-enable": {
                "action": "hook_enable",
                "description": "Enable a hook: /hook-enable <name>",
            },
            "/hook-disable": {
                "action": "hook_disable",
                "description": "Disable a hook: /hook-disable <name>",
            },
            "/hook-stats": {
                "action": "hook_stats",
                "description": "Show hook statistics: /hook-stats [name]",
            },
        }

    async def handle_action(self, action: str, args: str) -> str:
        """Dispatch action to handler.

        Args:
            action: Action name
            args: Command arguments

        Returns:
            Result message
        """
        handlers = {
            "hooks": self.handle_hooks,
            "hook_enable": self.handle_hook_enable,
            "hook_disable": self.handle_hook_disable,
            "hook_stats": self.handle_hook_stats,
        }

        handler = handlers.get(action)
        if handler:
            return handler(args)
        return f"Unknown action: {action}"
