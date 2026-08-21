"""Tests for pre-allocated session IDs.

Covers the ``--session-id`` flag on the ``run`` command (use a caller-supplied
id for a new session instead of minting a UUID) and the ``session new``
subcommand (print a fresh UUID for pre-allocation).

The run tests build a minimal Click group by registering the run command with
mocked collaborators (so no chat/session is actually launched), then invoke it
and inspect the session id handed to the launch coroutine.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import click
from click.testing import CliRunner

from amplifier_app_cli.commands.run import register_run_command
from amplifier_app_cli.commands.session import register_session_commands

_MODULE = "amplifier_app_cli.commands.run"


def _make_prepared_bundle() -> SimpleNamespace:
    return SimpleNamespace(mount_plan={"hooks": []})


def _build_cli() -> tuple[click.Group, AsyncMock]:
    """Register the run command with mocked collaborators.

    Returns the CLI group and the execute_single AsyncMock so tests can read
    the ``session_id`` kwarg the launch path was called with.
    """
    execute_single = AsyncMock()
    cli = click.Group()
    register_run_command(
        cli,
        interactive_chat=AsyncMock(),
        execute_single=execute_single,
        get_module_search_paths=lambda: [],
        check_first_run=lambda: False,
        prompt_first_run_init=lambda console: True,
    )
    return cli, execute_single


def _invoke(cli: click.Group, prepared_bundle: SimpleNamespace, *args: str):
    with (
        patch(f"{_MODULE}.resolve_config", return_value=({}, prepared_bundle)),
        patch(f"{_MODULE}.create_config_manager", return_value=MagicMock()),
        patch(f"{_MODULE}.AppSettings", return_value=MagicMock()),
        patch(
            "amplifier_app_cli.utils.startup_checker.check_and_notify",
            new=AsyncMock(),
        ),
    ):
        runner = CliRunner()
        return runner.invoke(cli, ["run", *args])


def test_session_id_flag_used_for_new_session():
    """``run --session-id X`` must launch the new session with that exact id."""
    prepared_bundle = _make_prepared_bundle()
    cli, execute_single = _build_cli()

    result = _invoke(
        cli,
        prepared_bundle,
        "hello",
        "--session-id",
        "my-preallocated-id",
        "--mode",
        "single",
        "--output-format",
        "json",
    )

    assert result.exit_code == 0, result.output
    execute_single.assert_awaited_once()
    await_args = execute_single.await_args
    assert await_args is not None
    assert await_args.kwargs["session_id"] == "my-preallocated-id"


def test_no_session_id_flag_mints_uuid():
    """Omitting ``--session-id`` must fall back to a generated UUID."""
    prepared_bundle = _make_prepared_bundle()
    cli, execute_single = _build_cli()

    result = _invoke(
        cli,
        prepared_bundle,
        "hello",
        "--mode",
        "single",
        "--output-format",
        "json",
    )

    assert result.exit_code == 0, result.output
    execute_single.assert_awaited_once()
    await_args = execute_single.await_args
    assert await_args is not None
    generated = await_args.kwargs["session_id"]
    # A valid UUID4 string was minted (raises if not parseable).
    assert str(uuid.UUID(generated)) == generated


def test_session_id_conflicts_with_resume():
    """``--session-id`` combined with ``--resume`` is rejected before launch."""
    prepared_bundle = _make_prepared_bundle()
    cli, execute_single = _build_cli()

    result = _invoke(
        cli,
        prepared_bundle,
        "hello",
        "--session-id",
        "abc",
        "--resume",
        "some-other-session",
        "--mode",
        "single",
    )

    assert result.exit_code != 0
    assert "cannot be combined with --resume" in result.output
    execute_single.assert_not_awaited()


def test_session_new_prints_valid_uuid():
    """``session new`` prints a single parseable UUID and nothing else."""
    cli = click.Group()
    register_session_commands(
        cli,
        interactive_chat=AsyncMock(),
        execute_single=AsyncMock(),
        get_module_search_paths=lambda: [],
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["session", "new"])

    assert result.exit_code == 0, result.output
    printed = result.output.strip()
    assert str(uuid.UUID(printed)) == printed
