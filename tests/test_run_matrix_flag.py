"""Tests for the per-session ``--matrix`` flag on the ``run`` command.

The flag mirrors ``--bundle`` for per-session selection, but instead of
choosing a bundle it injects ``default_matrix`` into the mounted
``hooks-routing`` config within the prepared bundle's mount plan. It must
never persist anything to settings (scope is strictly this session).

These tests build a minimal Click group by registering the run command with
mocked collaborators (so no chat/session is actually launched), then invoke
it and inspect the mutated mount plan.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import click
from click.testing import CliRunner

from amplifier_app_cli.commands.run import register_run_command

_MODULE = "amplifier_app_cli.commands.run"


def _make_prepared_bundle(with_routing: bool = True) -> SimpleNamespace:
    """A stand-in prepared bundle exposing a mutable ``mount_plan``."""
    hooks: list[dict] = []
    if with_routing:
        hooks.append({"module": "hooks-routing", "config": {}})
    # Include an unrelated hook to prove we target hooks-routing specifically.
    hooks.append({"module": "hooks-mode", "config": {}})
    return SimpleNamespace(mount_plan={"hooks": hooks})


def _build_cli(prepared_bundle: SimpleNamespace) -> tuple[click.Group, AsyncMock]:
    """Register the run command with mocked collaborators.

    Returns the CLI group and the execute_single AsyncMock so tests can assert
    the session-launch path was reached (i.e. the command didn't bail early).
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
    """Invoke ``run`` with resolve_config and startup checks stubbed out."""
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


def test_matrix_flag_sets_default_matrix_in_mount_plan():
    """``run --matrix foo`` must set default_matrix on the hooks-routing entry."""
    prepared_bundle = _make_prepared_bundle(with_routing=True)
    cli, execute_single = _build_cli(prepared_bundle)

    result = _invoke(
        cli,
        prepared_bundle,
        "hello",
        "--bundle",
        "anchors",
        "--matrix",
        "foo",
        "--mode",
        "single",
        "--output-format",
        "json",
    )

    assert result.exit_code == 0, result.output
    execute_single.assert_awaited_once()

    routing = next(
        h for h in prepared_bundle.mount_plan["hooks"] if h["module"] == "hooks-routing"
    )
    assert routing["config"]["default_matrix"] == "foo"
    # Unrelated hooks are left untouched.
    mode_hook = next(
        h for h in prepared_bundle.mount_plan["hooks"] if h["module"] == "hooks-mode"
    )
    assert "default_matrix" not in mode_hook["config"]


def test_no_matrix_flag_leaves_routing_untouched():
    """Omitting ``--matrix`` must not inject default_matrix anywhere."""
    prepared_bundle = _make_prepared_bundle(with_routing=True)
    cli, execute_single = _build_cli(prepared_bundle)

    result = _invoke(
        cli,
        prepared_bundle,
        "hello",
        "--bundle",
        "anchors",
        "--mode",
        "single",
        "--output-format",
        "json",
    )

    assert result.exit_code == 0, result.output
    execute_single.assert_awaited_once()

    routing = next(
        h for h in prepared_bundle.mount_plan["hooks"] if h["module"] == "hooks-routing"
    )
    assert "default_matrix" not in routing["config"]


def test_matrix_flag_warns_when_routing_hook_missing():
    """When hooks-routing isn't mounted, warn and continue (no crash)."""
    prepared_bundle = _make_prepared_bundle(with_routing=False)
    cli, execute_single = _build_cli(prepared_bundle)

    result = _invoke(
        cli,
        prepared_bundle,
        "hello",
        "--bundle",
        "anchors",
        "--matrix",
        "foo",
        "--mode",
        "single",
        "--output-format",
        "json",
    )

    assert result.exit_code == 0, result.output
    execute_single.assert_awaited_once()
    # No hooks-routing entry means nothing was mutated to carry default_matrix.
    for hook in prepared_bundle.mount_plan["hooks"]:
        assert "default_matrix" not in hook.get("config", {})
