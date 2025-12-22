"""Custom Click help formatter that separates deprecated commands.

This module provides AmplifierGroup, a custom Click group that displays
deprecated commands in their own clearly-marked section at the bottom
of the help output.
"""

import click
from click import Context
from click import HelpFormatter

from amplifier_app_cli.utils.deprecation import DEPRECATED_SECTION_HEADER


class AmplifierGroup(click.Group):
    """Custom Click group that separates deprecated commands in help output.

    Deprecated commands (those with deprecated=True) are displayed in a
    separate section at the bottom of the help output, making it clear
    to users which commands they should avoid and what to use instead.
    """

    def format_commands(self, ctx: Context, formatter: HelpFormatter) -> None:
        """Write all commands, separating deprecated ones into their own section."""
        commands = []
        deprecated_commands = []

        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None or cmd.hidden:
                continue

            if cmd.deprecated:
                deprecated_commands.append((subcommand, cmd))
            else:
                commands.append((subcommand, cmd))

        # Active commands section
        if commands:
            limit = formatter.width - 6 - max(len(cmd[0]) for cmd in commands)
            rows = []
            for subcommand, cmd in commands:
                help_text = cmd.get_short_help_str(limit=limit)
                rows.append((subcommand, help_text))
            if rows:
                with formatter.section("Commands"):
                    formatter.write_dl(rows)

        # Deprecated commands section - shown at the bottom with clear header
        if deprecated_commands:
            limit = formatter.width - 6 - max(len(cmd[0]) for cmd in deprecated_commands)
            rows = []
            for subcommand, cmd in deprecated_commands:
                help_text = cmd.get_short_help_str(limit=limit)
                # Strip the redundant "(DEPRECATED)" suffix that Click adds
                # since the section header already indicates deprecation
                help_text = help_text.replace(" (DEPRECATED)", "")
                rows.append((subcommand, help_text))
            if rows:
                with formatter.section(DEPRECATED_SECTION_HEADER):
                    formatter.write_dl(rows)
