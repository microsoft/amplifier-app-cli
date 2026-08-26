"""Focused regressions for Windows reset and deferred update behavior."""

from __future__ import annotations

import importlib
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from amplifier_app_cli.utils.source_status import UpdateReport
from amplifier_app_cli.utils.umbrella_discovery import UmbrellaInfo
from amplifier_app_cli.utils.update_executor import ExecutionResult
from amplifier_app_cli.utils.update_executor import _defer_self_update
from amplifier_app_cli.utils.update_executor import execute_updates
from amplifier_app_cli.utils.uv_utils import UvStep, defer_uv_tool_swap


reset_module = importlib.import_module("amplifier_app_cli.commands.reset")

_FAKE_UMBRELLA = UmbrellaInfo(
    url="https://github.com/microsoft/amplifier",
    ref="main",
    commit_id=None,
)


def _console_text(console: MagicMock) -> str:
    return "\n".join(str(call.args[0]) for call in console.print.call_args_list)


def _invoke_update_with_result(monkeypatch, execution_result: ExecutionResult):
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

    return CliRunner().invoke(update_module.update, ["--yes"])


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

    success = reset_module._remove_amplifier_dir({"settings"})

    assert success is False
    clear_cache.assert_called_once_with(dry_run=False)
    assert not removable.exists(), "independent cleanup must continue after cache failure"
    assert not (amplifier_dir / "registry.json").exists()
    assert (amplifier_dir / "settings.yaml").exists()
    output = _console_text(fake_console)
    assert "Cleanup incomplete" in output
    assert "cache" in output


def test_remove_amplifier_dir_reports_registry_failure(tmp_path, monkeypatch):
    amplifier_dir = tmp_path / ".amplifier"
    amplifier_dir.mkdir()
    (amplifier_dir / "registry.json").write_text("{}", encoding="utf-8")
    (amplifier_dir / "settings.yaml").write_text("keep", encoding="utf-8")

    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)
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

    success = reset_module._remove_amplifier_dir({"settings", "cache"})

    assert success is False
    output = _console_text(fake_console)
    assert "Cleanup incomplete" in output
    assert "registry.json" in output


def test_windows_reset_does_not_stage_after_incomplete_cleanup(monkeypatch):
    defer = MagicMock(return_value=True)
    monkeypatch.setattr(reset_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(reset_module, "_show_plan", MagicMock())
    monkeypatch.setattr(reset_module, "_clean_uv_cache", MagicMock(return_value=True))
    monkeypatch.setattr(
        reset_module, "_remove_amplifier_dir", MagicMock(return_value=False)
    )
    monkeypatch.setattr(reset_module, "_windows_defer_tool_swap", defer)

    result = CliRunner().invoke(reset_module.reset, ["--yes"])

    assert result.exit_code == 1
    defer.assert_not_called()
    assert "cleanup was incomplete" in result.output
    assert "no reinstall was staged" in result.output


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
        lambda preserve, dry_run: calls.append("cleanup") or False,
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

    launched = defer_uv_tool_swap(
        [UvStep(command="uv tool uninstall amplifier", label="Uninstalling...")],
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


def test_execution_result_staged_defaults_are_compatible_and_independent():
    first = ExecutionResult(success=True)
    second = ExecutionResult(success=True)

    assert first.staged == []
    first.staged.append("amplifier")
    assert second.staged == []


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