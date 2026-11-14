"""Thin read-only CLI entry points for Foundation-owned storage operations.

Candidate integrity is not activation, import authority or durable media ACK.
No transcript reader that performs automatic recovery is used for preview.
"""

from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path

import click

from ..shared_root_state import SharedRootSession, _shared_root_platform_supported, shared_root_id_supported


def register_session_storage_commands(session, *, store_factory):
    @session.group("storage")
    def storage():
        """Inspect media conversion plans and retained operations without applying them."""

    @storage.command("preview")
    @click.argument("session_id")
    @click.option("--backup", multiple=True, type=click.Path(path_type=Path),
                  help="Explicit original backup to inventory (never selected for recovery).")
    @click.option("--records", type=click.IntRange(min=0), default=None,
                  help="Selected prefix count; no suffix assets are inventoried.")
    def preview(session_id, backup, records):
        """Read the exact native-v1 primary under supported writer ownership."""
        from amplifier_foundation.session.media_conversion import preview as owner_preview
        if not _shared_root_platform_supported() or not shared_root_id_supported(session_id):
            raise click.ClickException("Storage preview requires supported exact session writer ownership")
        native = store_factory()
        directory = native.base_dir.absolute() / session_id
        # No implicit fallback to backups, import, source migration or v2 layout.
        for marker in ("transcript.jsonl.append-pending", "transcript.commit.json"):
            if (directory / marker).exists() or (directory / marker).is_symlink():
                raise click.ClickException("Pending or separately gated storage layout; preserve and inspect")

        @contextmanager
        def fence(owner):
            if owner != "amplifier-cli":
                raise ValueError("Storage owner differs from this host")
            root = SharedRootSession.acquire(session_id)
            try:
                root.held.check()
                yield
                root.held.check()
            finally:
                root.release()

        try:
            plan = owner_preview(
                directory / "transcript.jsonl", owner="amplifier-cli",
                session_id=session_id, fence=fence,
                backups=tuple(path.absolute() for path in backup), selected_records=records,
            )
        except (OSError, ValueError, RuntimeError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(json.dumps({"state": "preview-only-not-applied", "plan": asdict(plan)}, sort_keys=True))

    @storage.command("reconcile")
    @click.argument("operation", type=click.Path(path_type=Path))
    @click.option("--installed", type=click.Path(path_type=Path), default=None)
    def reconcile(operation, installed):
        """Read retained conversion facts; never replay replacement or claim ACK."""
        from amplifier_foundation.session.media_conversion import reconcile as owner_reconcile
        try:
            state = owner_reconcile(operation.absolute(), installed=installed.absolute() if installed else None)
        except (OSError, ValueError, RuntimeError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(json.dumps({"state": state, "effect": "read-only"}, sort_keys=True))

    @storage.command("verify-export")
    @click.argument("portable", type=click.Path(path_type=Path))
    def verify_export(portable):
        """Verify only the portable closure directory, not its private parent operation."""
        from amplifier_foundation.session.media_conversion import verify_export as owner_verify
        try:
            owner_verify(portable.absolute())
        except (OSError, ValueError, RuntimeError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(json.dumps({"state": "portable-integrity-verified", "import_authority": False,
                               "checkpoint_acknowledged": False}, sort_keys=True))
