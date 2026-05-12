"""Tests for execute_self_update in update_executor."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from amplifier_app_cli.utils.umbrella_discovery import UmbrellaInfo
from amplifier_app_cli.utils.update_executor import execute_self_update

_FAKE_UMBRELLA = UmbrellaInfo(
    url="https://github.com/microsoft/amplifier",
    ref="main",
    commit_id=None,
)


@pytest.mark.asyncio
async def test_execute_self_update_uses_upgrade_reinstall_not_force():
    """execute_self_update must call uv with --upgrade --reinstall, not --force.

    Using --force destroys the entire tool virtualenv and rebuilds from
    scratch, which is unnecessarily slow. --upgrade --reinstall refreshes
    packages without the venv destruction overhead.
    """
    captured_cmd: list[str] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        mock_proc = MagicMock()
        mock_proc.stderr = iter([])  # empty stderr — no output lines
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        return mock_proc

    with patch(
        "amplifier_app_cli.utils.update_executor.subprocess.Popen",
        side_effect=fake_popen,
    ):
        with patch(
            "amplifier_app_cli.utils.update_executor._invalidate_modules_with_missing_deps",
            return_value=(0, 0),
        ):
            with patch("amplifier_app_cli.utils.update_executor.remove_stale_uv_lock"):
                await execute_self_update(_FAKE_UMBRELLA)

    assert "--force" not in captured_cmd, (
        "uv must NOT use --force (it destroys the venv unnecessarily)"
    )
    assert "--upgrade" in captured_cmd, (
        "uv must use --upgrade to check for newer versions"
    )
    assert "--reinstall" in captured_cmd, (
        "uv must use --reinstall to fully refresh packages"
    )


# ---------------------------------------------------------------------------
# New tests for force=True / force=False behaviour (fix/update-pypi-deps-blind)
# ---------------------------------------------------------------------------


def _make_fake_subprocess_trackers():
    """Return (calls_list, fake_run_fn, FakePopen_class) for subprocess mocking.

    All subprocess invocations append their argv to calls_list so tests can
    assert on ordering and content.
    """
    calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    class FakePopen:
        def __init__(self, argv, *args, **kwargs):
            calls.append(list(argv))
            self.args = argv
            self.stderr = iter([])  # no output lines — keeps _drain_stderr fast
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

        def poll(self):
            return 0

    return calls, fake_run, FakePopen


@pytest.mark.asyncio
async def test_execute_self_update_force_runs_cache_clean(monkeypatch):
    """When force=True, uv cache clean must run BEFORE uv tool install.

    This ensures that PyPI's CDN cannot serve a stale 304 response during a
    release rollout window. `--upgrade`/`--reinstall` imply `--refresh` which
    is a conditional revalidation; `uv cache clean` is unconditional — matching
    what `amplifier reset` does (the only update path users report as reliable).
    """
    calls, fake_run, FakePopen = _make_fake_subprocess_trackers()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.remove_stale_uv_lock",
        lambda: None,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor._invalidate_modules_with_missing_deps",
        lambda: (0, 0),
    )

    result = await execute_self_update(_FAKE_UMBRELLA, force=True)

    assert result.success, f"Expected success, got: {result}"
    # First subprocess call must be `uv cache clean`
    assert calls, "Expected at least one subprocess invocation"
    assert calls[0][:3] == ["uv", "cache", "clean"], (
        f"Expected `uv cache clean` as the first subprocess call, got: {calls[0]}"
    )
    # uv tool install must follow
    assert any(c[:4] == ["uv", "tool", "install", "--upgrade"] for c in calls), (
        f"Expected `uv tool install --upgrade ...` in subprocess calls, got: {calls}"
    )


@pytest.mark.asyncio
async def test_execute_self_update_no_force_skips_cache_clean(monkeypatch):
    """When force=False (default), uv cache clean must NOT be run.

    Normal updates rely on uv's own --upgrade/--reinstall behaviour and must
    not incur the extra cache-wipe overhead.
    """
    calls, fake_run, FakePopen = _make_fake_subprocess_trackers()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.remove_stale_uv_lock",
        lambda: None,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor._invalidate_modules_with_missing_deps",
        lambda: (0, 0),
    )

    result = await execute_self_update(_FAKE_UMBRELLA, force=False)

    assert result.success, f"Expected success, got: {result}"
    cache_clean_calls = [c for c in calls if c[:3] == ["uv", "cache", "clean"]]
    assert not cache_clean_calls, (
        f"`uv cache clean` must NOT be called when force=False, but it ran: {calls}"
    )


@pytest.mark.asyncio
async def test_execute_updates_forwards_force_to_execute_self_update(monkeypatch):
    """execute_updates must forward the force flag to execute_self_update.

    This is the plumbing test: execute_updates(force=True) → execute_self_update(force=True).
    If the flag is swallowed here, the cache-clean logic in execute_self_update
    never fires even when --force is passed from the CLI.
    """
    from amplifier_app_cli.utils.source_status import UpdateReport
    from amplifier_app_cli.utils.update_executor import ExecutionResult, execute_updates

    captured: dict = {}

    async def fake_execute_self_update(
        umbrella_info, progress_callback=None, force=False
    ):
        captured["force"] = force
        captured["umbrella_info"] = umbrella_info
        return ExecutionResult(success=True, updated=["amplifier"], messages=[])

    monkeypatch.setattr(
        "amplifier_app_cli.utils.update_executor.execute_self_update",
        fake_execute_self_update,
    )

    empty_report = UpdateReport(local_file_sources=[], cached_git_sources=[])

    await execute_updates(empty_report, umbrella_info=_FAKE_UMBRELLA, force=True)

    assert "force" in captured, (
        "execute_self_update was not called; execute_updates may not be passing umbrella_info"
    )
    assert captured["force"] is True, (
        f"Expected execute_self_update to receive force=True, got force={captured['force']!r}"
    )


def test_update_command_passes_umbrella_info_despite_no_git_updates():
    """Regression: PyPI dep bumps must trigger the umbrella self-update.

    Before the fix, check_umbrella_dependencies_for_updates() returned False
    for PyPI version bumps (e.g. amplifier-core 1.5.1 → 1.5.2). The
    has_umbrella_updates gate set it to False, which caused nothing_to_update
    to be True, and execute_updates was never called. Users stayed stuck on
    the old version with no error message.

    After the fix, has_umbrella_updates=True whenever umbrella_info is
    discovered, so execute_updates is always called with umbrella_info.
    """
    from click.testing import CliRunner

    from amplifier_app_cli.commands.update import update
    from amplifier_app_cli.utils.source_status import UpdateReport
    from amplifier_app_cli.utils.update_executor import ExecutionResult

    fake_info = UmbrellaInfo(
        url="https://github.com/microsoft/amplifier",
        ref="main",
        commit_id=None,
    )
    captured: dict = {}

    async def fake_execute_updates(
        report, umbrella_info=None, progress_callback=None, force=False
    ):
        captured["umbrella_info"] = umbrella_info
        captured["force"] = force
        return ExecutionResult(success=True, updated=["amplifier"], messages=[])

    # Simulates the bug condition: no git-sourced dep has updates,
    # so the old check_umbrella_dependencies_for_updates returns False.
    async def fake_check_umbrella_deps_false(info):
        return False

    empty_report = UpdateReport(local_file_sources=[], cached_git_sources=[])

    async def fake_check_all_sources(**kwargs):
        return empty_report

    async def fake_check_all_bundle_status():
        return {}

    async def fake_get_umbrella_dep_details(info):
        return []

    with (
        patch(
            "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
            return_value=fake_info,
        ),
        patch(
            "amplifier_app_cli.utils.update_executor.check_umbrella_dependencies_for_updates",
            side_effect=fake_check_umbrella_deps_false,
        ),
        patch(
            "amplifier_app_cli.commands.update.check_all_sources",
            side_effect=fake_check_all_sources,
        ),
        patch(
            "amplifier_app_cli.commands.update._check_all_bundle_status",
            side_effect=fake_check_all_bundle_status,
        ),
        patch(
            "amplifier_app_cli.commands.update._get_umbrella_dependency_details",
            side_effect=fake_get_umbrella_dep_details,
        ),
        patch(
            "amplifier_app_cli.commands.update.execute_updates",
            side_effect=fake_execute_updates,
        ),
        patch(
            "amplifier_app_cli.commands.update._refresh_skills_cache",
            return_value=None,
        ),
        patch(
            "amplifier_app_cli.commands.update.save_update_last_check",
            return_value=None,
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(update, ["--yes"])

    # If the command exited with an unexpected exception, surface it.
    if result.exception and not isinstance(result.exception, SystemExit):
        import traceback

        raise AssertionError(
            f"update command raised an exception:\n"
            f"{''.join(traceback.format_exception(type(result.exception), result.exception, result.exception.__traceback__))}\n"
            f"Output:\n{result.output}"
        )

    assert captured.get("umbrella_info") is not None, (
        "execute_updates was called with umbrella_info=None (or was never called).\n"
        "This means the PyPI-dep gate was NOT fixed — `amplifier update` still ignores\n"
        "PyPI version bumps and reports 'All sources up to date' when only amplifier-core\n"
        "(or another PyPI dep) has a newer version.\n"
        f"captured={captured!r}\n"
        f"Command output:\n{result.output}"
    )
