"""Thin passive diagnostic command. No normal CLI startup imports."""

import json
import sys

import click

from ..lib.diagnostics import collect_diagnostics
from ..lib.diagnostics import current_target
from ..lib.diagnostics import render_text
from ..lib.diagnostics import valid_session_id


def _session(_ctx, _param, value):
    if value is not None and not valid_session_id(value):
        raise click.BadParameter("invalid_session_id")
    return value


class _DoctorCommand(click.Command):
    def parse_args(self, ctx, args):
        try:
            return super().parse_args(ctx, args)
        except click.ClickException:
            raise click.UsageError("invalid_doctor_arguments", ctx) from None


@click.command(cls=_DoctorCommand)
@click.option("--format", "output_format", type=click.Choice(["text", "json"]), default="text")
@click.option("--session", "session_id", callback=_session, metavar="SESSION_ID")
def doctor(output_format, session_id):
    """Report local declarations without resolving or activating them."""
    try:
        target = current_target(session_id)
        report = collect_diagnostics(target)
    except Exception:
        # Never expose path, parser, metadata, or OS exception payloads.
        from ..lib.diagnostics import unavailable_report

        report = unavailable_report()
    click.echo(json.dumps(report, ensure_ascii=True) if output_format == "json" else render_text(report))
    if report["collection_status"] != "complete":
        raise click.exceptions.Exit(1)


def main():
    """Exact-route entry; malformed doctor input cannot fall back to main."""
    try:
        code = doctor.main(args=sys.argv[2:], prog_name="amplifier doctor", standalone_mode=False)
        raise SystemExit(code or 0)
    except click.ClickException:
        click.echo("Usage: amplifier doctor [--format text|json] [--session SESSION_ID]", err=True)
        click.echo("Error: invalid_doctor_arguments", err=True)
        raise SystemExit(2) from None
    except click.exceptions.Exit as error:
        raise SystemExit(error.exit_code) from None
    except (EOFError, KeyboardInterrupt):
        click.echo("Error: doctor_interrupted", err=True)
        raise SystemExit(1) from None