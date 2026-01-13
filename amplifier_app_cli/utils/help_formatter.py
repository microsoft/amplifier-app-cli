"""Custom Click help formatter.

This module provides AmplifierGroup, a custom Click group for
consistent help output formatting.
"""

import click
from click import Context
from click import HelpFormatter


class AmplifierGroup(click.Group):
    """Custom Click group for consistent help output formatting."""

    def format_commands(self, ctx: Context, formatter: HelpFormatter) -> None:
        """Write all commands to the help output."""
        commands = []

        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None or cmd.hidden:
                continue
            commands.append((subcommand, cmd))

        # Commands section
        if commands:
            limit = formatter.width - 6 - max(len(cmd[0]) for cmd in commands)
            rows = []
            for subcommand, cmd in commands:
                help_text = cmd.get_short_help_str(limit=limit)
                rows.append((subcommand, help_text))
            if rows:
                with formatter.section("Commands"):
                    formatter.write_dl(rows)
