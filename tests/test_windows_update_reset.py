"""Focused regressions for Windows reset and deferred update behavior."""

from __future__ import annotations

import base64
import importlib
import re
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from amplifier_app_cli.utils.source_status import UpdateReport
from amplifier_app_cli.utils.umbrella_discovery import UmbrellaInfo
from amplifier_app_cli.utils.update_executor import ExecutionResult
from amplifier_app_cli.utils.update_executor import _defer_self_update
from amplifier_app_cli.utils.update_executor import execute_self_update
from amplifier_app_cli.utils.update_executor import execute_updates
from amplifier_app_cli.utils.uv_utils import CleanupStep, UvStep, defer_uv_tool_swap


reset_module = importlib.import_module("amplifier_app_cli.commands.reset")

_FAKE_UMBRELLA = UmbrellaInfo(
    url="https://github.com/microsoft/amplifier",
    ref="main",
    commit_id=None,
)


def _console_text(console: MagicMock) -> str:
    return "\n".join(str(call.args[0]) for call in console.print.call_args_list)


def _invoke_update_with_result(
    monkeypatch, execution_result: ExecutionResult, args: list[str] | None = None
):
    update_module = importlib.import_module("amplifier_app_cli.commands.update")

    async def fake_check_all_sources(**kwargs):
        return UpdateReport(local_file_sources=[], cached_git_sources=[])

    async def fake_check_all_bundle_status():
        return {}

    async def fake_get_umbrella_dep_details(info):
        return []

    async def fake_pypi_has_update():
        return True

    async def fake_execute_updates(*args, **kwargs):
        return execution_result

    monkeypatch.setattr(
        "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
        lambda: _FAKE_UMBRELLA,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.check_pypi_packages_for_updates",
        fake_pypi_has_update,
    )
    monkeypatch.setattr(update_module, "check_all_sources", fake_check_all_sources)
    monkeypatch.setattr(
        update_module, "_check_all_bundle_status", fake_check_all_bundle_status
    )
    monkeypatch.setattr(
        update_module,
        "_get_umbrella_dependency_details",
        fake_get_umbrella_dep_details,
    )
    monkeypatch.setattr(update_module, "execute_updates", fake_execute_updates)
    monkeypatch.setattr(update_module, "_refresh_skills_cache", lambda console: None)
    monkeypatch.setattr(update_module, "save_update_last_check", lambda value: None)

    return CliRunner().invoke(update_module.update, args or ["--yes"])


def test_remove_amplifier_dir_reports_cache_failure_and_continues(
    tmp_path, monkeypatch
):
    amplifier_dir = tmp_path / ".amplifier"
    (amplifier_dir / "cache").mkdir(parents=True)
    (amplifier_dir / "registry.json").write_text("{}", encoding="utf-8")
    (amplifier_dir / "settings.yaml").write_text("keep", encoding="utf-8")
    removable = amplifier_dir / "remove-me.txt"
    removable.write_text("remove", encoding="utf-8")

    clear_cache = MagicMock(return_value=(0, False))

    def clear_registry(*, dry_run):
        assert dry_run is False
        (amplifier_dir / "registry.json").unlink()
        return True

    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)
    # Redirect both resolutions of ~/.amplifier coherently: reset only
    # delegates to the shared utilities when they agree with the directory
    # being cleaned, so patching one alone would silently bypass them.
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.get_amplifier_dir",
        lambda: amplifier_dir,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.clear_download_cache",
        clear_cache,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.clear_registry",
        clear_registry,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path",
        lambda: tmp_path / "install-state.json",
    )
    fake_console = MagicMock()
    monkeypatch.setattr(reset_module, "console", fake_console)

    success = reset_module._remove_amplifier_dir({"cache", "registry"})

    assert success is False
    clear_cache.assert_called_once_with(dry_run=False)
    assert not (amplifier_dir / "registry.json").exists(), (
        "independent cleanup must continue after cache failure"
    )
    assert (amplifier_dir / "settings.yaml").exists()
    assert removable.exists(), "unmanaged files are never swept by a category"
    output = _console_text(fake_console)
    assert "Cleanup incomplete" in output
    assert "cache" in output


def test_remove_amplifier_dir_reports_registry_failure(tmp_path, monkeypatch):
    amplifier_dir = tmp_path / ".amplifier"
    amplifier_dir.mkdir()
    (amplifier_dir / "registry.json").write_text("{}", encoding="utf-8")
    (amplifier_dir / "settings.yaml").write_text("keep", encoding="utf-8")

    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)
    # Redirect both resolutions of ~/.amplifier coherently: reset only
    # delegates to the shared utilities when they agree with the directory
    # being cleaned, so patching one alone would silently bypass them.
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.get_amplifier_dir",
        lambda: amplifier_dir,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.clear_registry",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path",
        lambda: tmp_path / "install-state.json",
    )
    fake_console = MagicMock()
    monkeypatch.setattr(reset_module, "console", fake_console)

    success = reset_module._remove_amplifier_dir({"registry"})

    assert success is False
    output = _console_text(fake_console)
    assert "Cleanup incomplete" in output
    assert "registry.json" in output


def test_windows_reset_defers_cleanup_instead_of_failing_before_staging(monkeypatch):
    defer = MagicMock(return_value=True)
    monkeypatch.setattr(reset_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(reset_module, "_show_plan", MagicMock())
    monkeypatch.setattr(reset_module, "_clean_uv_cache", MagicMock(return_value=True))
    monkeypatch.setattr(
        reset_module, "_remove_amplifier_dir", MagicMock(return_value=False)
    )
    monkeypatch.setattr(reset_module, "_windows_defer_tool_swap", defer)

    result = CliRunner().invoke(reset_module.reset, ["--yes"])

    assert result.exit_code == 0
    reset_module._remove_amplifier_dir.assert_not_called()
    deferred_steps = defer.call_args.args[1]
    assert [step.path.name for step in deferred_steps] == ["cache", "registry.json"]


def test_windows_reset_returns_failure_when_finisher_cannot_launch(monkeypatch):
    monkeypatch.setattr(reset_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(reset_module, "_show_plan", MagicMock())
    monkeypatch.setattr(reset_module, "_clean_uv_cache", MagicMock(return_value=True))
    monkeypatch.setattr(
        reset_module, "_remove_amplifier_dir", MagicMock(return_value=True)
    )
    monkeypatch.setattr(
        reset_module, "_windows_defer_tool_swap", MagicMock(return_value=False)
    )

    result = CliRunner().invoke(reset_module.reset, ["--yes"])

    assert result.exit_code == 1
    assert "Reset could not be staged" in result.output


@pytest.mark.parametrize(
    ("tool_list", "expected_uninstalls"),
    [
        (
            "amplifier-app-cli v0.1.1\n- amplifier\n",
            [["uv", "tool", "uninstall", "amplifier-app-cli"]],
        ),
        (
            "amplifier v0.1.0\n- amplifier\n",
            [["uv", "tool", "uninstall", "amplifier"]],
        ),
        (
            "amplifier v0.1.0\n- amplifier\namplifier-app-cli v0.1.1\n- amplifier\n",
            [
                ["uv", "tool", "uninstall", "amplifier"],
                ["uv", "tool", "uninstall", "amplifier-app-cli"],
            ],
        ),
    ],
)
def test_reset_uninstalls_current_and_legacy_tool_distributions(
    monkeypatch, tool_list, expected_uninstalls
):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv == ["uv", "tool", "list"]:
            return SimpleNamespace(stdout=tool_list)
        return SimpleNamespace()

    monkeypatch.setattr(reset_module.subprocess, "run", fake_run)

    assert reset_module._uninstall_amplifier() is True
    assert calls == [["uv", "tool", "list"], *expected_uninstalls]


def test_reset_force_installs_to_repair_an_orphaned_executable(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr(reset_module.subprocess, "run", run)

    assert reset_module._install_amplifier() is True
    run.assert_called_once_with(
        [
            "uv",
            "tool",
            "install",
            "--force",
            reset_module.DEFAULT_INSTALL_SOURCE,
        ],
        check=True,
    )


def test_reset_force_install_still_surfaces_install_errors(monkeypatch):
    run = MagicMock(
        side_effect=subprocess.CalledProcessError(
            1,
            [
                "uv",
                "tool",
                "install",
                "--force",
                reset_module.DEFAULT_INSTALL_SOURCE,
            ],
        )
    )
    fake_console = MagicMock()
    monkeypatch.setattr(reset_module.subprocess, "run", run)
    monkeypatch.setattr(reset_module, "console", fake_console)

    assert reset_module._install_amplifier() is False
    assert "Failed to install amplifier" in _console_text(fake_console)


def _stage_windows_swap(monkeypatch, tool_list: str | None, *, no_install: bool):
    """Run the Windows deferred swap against a given `uv tool list` output.

    `tool_list=None` simulates uv being unreadable.
    """

    def fake_run(argv, **kwargs):
        if argv == ["uv", "tool", "list"]:
            if tool_list is None:
                raise FileNotFoundError("uv")
            return SimpleNamespace(stdout=tool_list)
        return SimpleNamespace()

    defer = MagicMock(return_value=True)
    monkeypatch.setattr(reset_module.subprocess, "run", fake_run)
    monkeypatch.setattr(reset_module, "console", MagicMock())
    monkeypatch.setattr(reset_module, "defer_uv_tool_swap", defer)

    staged = reset_module._windows_defer_tool_swap(no_install=no_install)
    return staged, defer


_MODERN = "amplifier v0.1.0\n- amplifier\n"
_LEGACY = "amplifier-app-cli v0.1.1\n- amplifier\n"
_BOTH = _MODERN + _LEGACY


@pytest.mark.parametrize(
    ("tool_list", "expected_uninstalls"),
    [
        (_MODERN, ["uv tool uninstall amplifier"]),
        (_LEGACY, ["uv tool uninstall amplifier-app-cli"]),
        (
            _BOTH,
            [
                "uv tool uninstall amplifier",
                "uv tool uninstall amplifier-app-cli",
            ],
        ),
        ("", []),
    ],
)
def test_windows_reset_finisher_uninstalls_the_registered_distribution(
    monkeypatch, tool_list, expected_uninstalls
):
    """The name to uninstall is read back from uv, never assumed.

    Hardcoding either name uninstalls the distribution the user does not have
    and leaves the one they do have registered.
    """
    staged, defer = _stage_windows_swap(monkeypatch, tool_list, no_install=False)

    assert staged is True
    forced_install = f"uv tool install --force {reset_module.DEFAULT_INSTALL_SOURCE}"
    expected = [*expected_uninstalls, forced_install]

    assert [step.command for step in defer.call_args.args[0]] == expected
    assert defer.call_args.kwargs["recovery_commands"] == expected


def test_windows_reset_finisher_never_aborts_the_reinstall_on_uninstall_failure(
    monkeypatch,
):
    """Uninstall steps stay best-effort; only the reinstall is required."""
    _, defer = _stage_windows_swap(monkeypatch, _BOTH, no_install=False)

    steps = defer.call_args.args[0]
    assert [step.required for step in steps] == [False, False, True]


@pytest.mark.parametrize(
    ("tool_list", "expected_uninstalls"),
    [
        (_MODERN, ["uv tool uninstall amplifier"]),
        (_LEGACY, ["uv tool uninstall amplifier-app-cli"]),
    ],
)
def test_windows_no_install_uninstalls_what_is_actually_registered(
    monkeypatch, tool_list, expected_uninstalls
):
    """`reset --no-install` must remove the distribution the user has.

    With no reinstall behind it there is no `--force` install to paper over a
    uninstall aimed at the wrong distribution: the step simply fails, the
    deferred script aborts, and the tool stays installed.
    """
    staged, defer = _stage_windows_swap(monkeypatch, tool_list, no_install=True)

    assert staged is True
    steps = defer.call_args.args[0]
    assert [step.command for step in steps] == expected_uninstalls
    assert all(step.required for step in steps)


def test_windows_no_install_stages_nothing_when_no_distribution_is_registered(
    monkeypatch,
):
    staged, defer = _stage_windows_swap(monkeypatch, "", no_install=True)

    assert staged is True
    defer.assert_not_called()


def test_windows_no_install_stages_cleanup_without_a_registered_distribution(
    tmp_path, monkeypatch
):
    defer = MagicMock(return_value=True)
    monkeypatch.setattr(reset_module, "_installed_uv_tool_packages", lambda: ())
    monkeypatch.setattr(reset_module, "defer_uv_tool_swap", defer)
    monkeypatch.setattr(reset_module, "console", MagicMock())

    assert reset_module._windows_defer_tool_swap(
        no_install=True,
        cleanup_steps=[CleanupStep(tmp_path / "cache", "Removing cache...")],
    )

    assert [step.path.name for step in defer.call_args.kwargs["cleanup_steps"]] == [
        "cache"
    ]
    assert defer.call_args.args[0] == []


@pytest.mark.parametrize("no_install", [True, False])
def test_windows_swap_covers_every_distribution_when_uv_cannot_be_read(
    monkeypatch, no_install
):
    """An unreadable `uv tool list` must not collapse to a guessed name.

    Uninstalling a distribution that is not registered is a no-op; missing the
    one that is registered is what strands the user. Both are attempted, and
    both are best-effort because neither is known to be present.
    """
    _, defer = _stage_windows_swap(monkeypatch, None, no_install=no_install)

    commands = [step.command for step in defer.call_args.args[0]]
    for package in reset_module.UV_TOOL_PACKAGES:
        assert f"uv tool uninstall {package}" in commands

    uninstall_steps = [
        step for step in defer.call_args.args[0] if "uninstall" in step.command
    ]
    assert not any(step.required for step in uninstall_steps)


def test_posix_reset_reinstalls_then_fails_after_incomplete_cleanup(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(reset_module, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(reset_module, "_show_plan", MagicMock())
    monkeypatch.setattr(reset_module, "_clean_uv_cache", MagicMock(return_value=True))
    monkeypatch.setattr(
        reset_module,
        "_uninstall_amplifier",
        lambda dry_run: calls.append("uninstall") or True,
    )
    monkeypatch.setattr(
        reset_module,
        "_remove_amplifier_dir",
        lambda remove_cats, full, dry_run: calls.append("cleanup") or False,
    )
    monkeypatch.setattr(
        reset_module,
        "_install_amplifier",
        lambda dry_run: calls.append("install") or True,
    )

    result = CliRunner().invoke(reset_module.reset, ["--yes"])

    assert calls == ["uninstall", "cleanup", "install"]
    assert result.exit_code == 1
    assert "Reset cleanup was incomplete" in result.output
    assert "reinstalled for recovery" in result.output
    assert "Reset complete!" not in result.output


def test_posix_reset_no_install_fails_after_incomplete_cleanup(monkeypatch):
    install = MagicMock(return_value=True)

    monkeypatch.setattr(reset_module, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(reset_module, "_show_plan", MagicMock())
    monkeypatch.setattr(reset_module, "_clean_uv_cache", MagicMock(return_value=True))
    monkeypatch.setattr(reset_module, "_uninstall_amplifier", MagicMock(return_value=True))
    monkeypatch.setattr(
        reset_module, "_remove_amplifier_dir", MagicMock(return_value=False)
    )
    monkeypatch.setattr(reset_module, "_install_amplifier", install)

    result = CliRunner().invoke(reset_module.reset, ["--yes", "--no-install"])

    install.assert_not_called()
    assert result.exit_code == 1
    assert "Reset cleanup was incomplete" in result.output
    assert "No reinstall was requested" in result.output
    assert "Reset complete!" not in result.output


def test_posix_reset_fails_when_reinstall_fails_after_clean_cleanup(monkeypatch):
    """Cleanup fine, reinstall broken -- the user now has no amplifier at all.

    The uninstall has already run by this point, so exiting 0 here would report
    "no amplifier installed" as a success to whatever script drove the reset.
    """
    monkeypatch.setattr(reset_module, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(reset_module, "_show_plan", MagicMock())
    monkeypatch.setattr(reset_module, "_clean_uv_cache", MagicMock(return_value=True))
    monkeypatch.setattr(
        reset_module, "_uninstall_amplifier", MagicMock(return_value=True)
    )
    monkeypatch.setattr(
        reset_module, "_remove_amplifier_dir", MagicMock(return_value=True)
    )
    monkeypatch.setattr(
        reset_module, "_install_amplifier", MagicMock(return_value=False)
    )

    result = CliRunner().invoke(reset_module.reset, ["--yes"])

    assert result.exit_code == 1
    assert "reinstall failed" in result.output
    assert "Reset complete!" not in result.output


@pytest.mark.parametrize("launched", [True, False])
def test_windows_defer_tool_swap_reports_whether_it_launched(monkeypatch, launched):
    fake_console = MagicMock()
    monkeypatch.setattr(reset_module, "console", fake_console)
    monkeypatch.setattr(reset_module, "defer_uv_tool_swap", lambda *a, **k: launched)

    result = reset_module._windows_defer_tool_swap(no_install=False)

    assert result is launched
    output = _console_text(fake_console)
    if launched:
        assert "Reset staged" in output
        assert "Reset was not staged" not in output
    else:
        assert "Reset was not staged" in output
        assert "Reset staged -" not in output


def test_deferred_script_qualifies_windows_utilities(tmp_path, monkeypatch):
    real_mkstemp = tempfile.mkstemp

    def temp_script(*, prefix, suffix):
        return real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)

    popen = MagicMock()
    monkeypatch.setattr(tempfile, "mkstemp", temp_script)
    monkeypatch.setattr(
        "amplifier_app_cli.utils.uv_utils.subprocess.Popen",
        popen,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.uv_utils.console",
        MagicMock(),
    )

    # Both step shapes, because they emit separate `ping` retry lines: reset's
    # real no_install=False path uses a best-effort uninstall followed by a
    # required install, and only the required branch was covered before.
    launched = defer_uv_tool_swap(
        [
            UvStep(
                command="uv tool uninstall amplifier",
                label="Uninstalling (best effort)...",
                attempts=5,
                required=False,
            ),
            UvStep(command="uv tool install amplifier", label="Reinstalling..."),
        ],
        operation="reset",
        intro_lines=["Reset"],
        success_message="Done",
        recovery_commands=["uv tool uninstall amplifier"],
    )

    assert launched is True
    script_path = popen.call_args.args[0][2]
    script = tmp_path.joinpath(script_path).read_text(encoding="ascii")
    assert '"%SystemRoot%\\System32\\tasklist.exe"' in script
    assert '"%SystemRoot%\\System32\\find.exe"' in script
    assert '"%SystemRoot%\\System32\\ping.exe"' in script
    assert "\ntasklist " not in script
    assert "| find " not in script
    assert "\n    ping " not in script
    # Every ping the script emits -- waitloop, required-step retry,
    # best-effort-step retry, and the closing countdown -- must be qualified.
    ping_lines = [line for line in script.splitlines() if "ping" in line]
    assert len(ping_lines) == 4
    assert all('"%SystemRoot%\\System32\\ping.exe"' in line for line in ping_lines)


def test_deferred_script_encodes_cleanup_paths_and_runs_them_before_uv(
    tmp_path, monkeypatch
):
    real_mkstemp = tempfile.mkstemp

    def temp_script(*, prefix, suffix):
        return real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)

    popen = MagicMock()
    monkeypatch.setattr(tempfile, "mkstemp", temp_script)
    monkeypatch.setattr(
        "amplifier_app_cli.utils.uv_utils.subprocess.Popen",
        popen,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.uv_utils.console",
        MagicMock(),
    )

    target = tmp_path / "cache%literal!name"
    assert defer_uv_tool_swap(
        [UvStep(command="uv tool install amplifier", label="Installing...")],
        operation="reset",
        intro_lines=["Reset"],
        success_message="Done",
        recovery_commands=["uv tool install amplifier"],
        cleanup_steps=[CleanupStep(target, "Removing cache...")],
    )

    script_path = popen.call_args.args[0][2]
    batch = tmp_path.joinpath(script_path).read_text(encoding="ascii")
    encoded = re.search(r"-EncodedCommand ([A-Za-z0-9+/=]+)", batch)
    assert encoded is not None
    cleanup = base64.b64decode(encoded.group(1)).decode("utf-16le")
    assert f"$target = '{target}'" in cleanup
    assert "[IO.FileAttributes]::ReparsePoint" in cleanup
    assert "[IO.Directory]::Delete($path, $true)" in cleanup
    assert "[IO.File]::Delete($path)" in cleanup
    assert "Get-ChildItem -LiteralPath $path -Force" in cleanup
    assert "Get-ChildItem -LiteralPath $path -Recurse" not in cleanup
    assert batch.index("Removing cache...") < batch.index("Installing...")


@pytest.mark.asyncio
async def test_windows_forced_update_defers_regenerable_cleanup(monkeypatch):
    captured = {}

    def defer(steps, **kwargs):
        captured["steps"] = steps
        captured["cleanup_steps"] = kwargs["cleanup_steps"]
        return True

    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.os",
        SimpleNamespace(name="nt"),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.defer_uv_tool_swap", defer
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.remove_stale_uv_lock", lambda: None
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.subprocess.run", MagicMock()
    )
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path",
        lambda: Path("/tmp/missing-install-state.json"),
    )

    result = await execute_self_update(_FAKE_UMBRELLA, force=True)

    assert result.success
    assert [step.path.name for step in captured["cleanup_steps"]] == [
        "cache",
        "registry.json",
    ]
    assert captured["cleanup_steps"][0].label == "Clearing Amplifier cache..."
    assert captured["steps"][0].command.startswith("uv tool install --upgrade")


def test_windows_force_update_leaves_app_cache_for_the_finisher(monkeypatch):
    update_module = importlib.import_module("amplifier_app_cli.commands.update")
    monkeypatch.setattr(update_module, "os", SimpleNamespace(name="nt"))

    result = _invoke_update_with_result(
        monkeypatch,
        ExecutionResult(success=True, staged=["amplifier"]),
        args=["--yes", "--force"],
    )

    assert result.exit_code == 0, result.output
    assert "Cache cleanup will finish after this exits" in result.output
    assert "Cleared" not in result.output


def test_windows_force_check_only_does_not_claim_cleanup_was_staged(monkeypatch):
    update_module = importlib.import_module("amplifier_app_cli.commands.update")
    monkeypatch.setattr(update_module, "os", SimpleNamespace(name="nt"))

    result = _invoke_update_with_result(
        monkeypatch,
        ExecutionResult(success=True, staged=["amplifier"]),
        args=["--yes", "--force", "--check-only"],
    )

    assert result.exit_code == 0, result.output
    assert "Cache cleanup is skipped by --check-only" in result.output
    assert "Cache cleanup will finish after this exits" not in result.output


@pytest.mark.asyncio
async def test_windows_force_stages_cleanup_without_an_umbrella_update(monkeypatch):
    staged_cleanup = MagicMock(
        return_value=ExecutionResult(
            success=True,
            staged=["cache"],
            messages=["cache cleanup will finish after this exits"],
        )
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.os",
        SimpleNamespace(name="nt"),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor._defer_forced_cache_cleanup",
        staged_cleanup,
    )

    result = await execute_updates(
        UpdateReport(local_file_sources=[], cached_git_sources=[]),
        umbrella_info=None,
        force=True,
    )

    staged_cleanup.assert_called_once_with()
    assert result.success
    assert result.staged == ["cache"]


@pytest.mark.parametrize("launched", [True, False])
def test_deferred_self_update_distinguishes_staged_from_failed(
    tmp_path, monkeypatch, launched
):
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path",
        lambda: tmp_path / "install-state.json",
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.defer_uv_tool_swap",
        lambda *a, **k: launched,
    )

    result = _defer_self_update(
        "git+https://github.com/microsoft/amplifier@main"
    )

    assert result.success is launched
    assert result.staged == (["amplifier"] if launched else [])
    assert result.updated == []
    if launched:
        assert result.failed == []
    else:
        assert result.failed == ["amplifier"]


@pytest.mark.asyncio
async def test_execute_updates_aggregates_mixed_staged_and_failed_results(monkeypatch):
    async def failed_module_update(*args, **kwargs):
        return ExecutionResult(
            success=False,
            failed=["provider-example"],
            errors={"provider-example": "fetch failed"},
        )

    async def staged_self_update(*args, **kwargs):
        return ExecutionResult(
            success=True,
            staged=["amplifier"],
            messages=["finishes after exit"],
        )

    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.execute_selective_module_update",
        failed_module_update,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.execute_self_update",
        staged_self_update,
    )
    report = UpdateReport(
        local_file_sources=[],
        cached_git_sources=[SimpleNamespace(has_update=True)],
    )

    result = await execute_updates(report, umbrella_info=_FAKE_UMBRELLA)

    assert result.success is False
    assert result.staged == ["amplifier"]
    assert result.updated == []
    assert result.failed == ["provider-example"]
    assert result.errors == {"provider-example": "fetch failed"}
    assert result.messages == ["finishes after exit"]


def test_update_command_renders_staged_items_instead_of_complete(monkeypatch):
    result = _invoke_update_with_result(
        monkeypatch,
        ExecutionResult(
            success=True,
            staged=["amplifier"],
            messages=["Amplifier update will finish after this exits"],
        ),
    )

    assert result.exit_code == 0, result.output
    assert "Update staged" in result.output
    assert "→ amplifier (staged)" in result.output
    assert "Update complete" not in result.output


def test_update_command_renders_mixed_staged_failed_and_messages(monkeypatch):
    result = _invoke_update_with_result(
        monkeypatch,
        ExecutionResult(
            success=False,
            staged=["amplifier"],
            failed=["provider-example"],
            errors={"provider-example": "fetch failed"},
            messages=["Amplifier update will finish after this exits"],
        ),
    )

    assert result.exit_code == 0, result.output
    assert "Update staged with errors" in result.output
    assert "→ amplifier (staged)" in result.output
    assert "✗ provider-example: fetch failed" in result.output
    assert "Amplifier update will finish after this exits" in result.output