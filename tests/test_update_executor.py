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
    """Regression: a real PyPI update triggers the umbrella self-update.

    Scenario: no git-sourced dep has changes, but check_pypi_packages_for_updates()
    detects that amplifier-core has a new release on PyPI.  execute_updates must
    be called with umbrella_info so the CLI performs `uv tool install --upgrade`.

    This guards against the v1.0.7 silent-staleness regression: the old code
    only walked git deps (direct_url.json) and never noticed PyPI bumps, so
    execute_updates was never called and the user stayed on the stale version.
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

    # check_pypi_packages_for_updates returns True → amplifier-core has a new release.
    async def fake_pypi_has_update():
        return True

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
            # Still present in the import (noqa: F401) but no longer gates execution.
            "amplifier_app_cli.utils.update_executor.check_umbrella_dependencies_for_updates",
        ),
        patch(
            # The new PyPI preflight — imported inside the update() body from
            # update_executor, so patch the source module attribute.
            # Returns True (update available) to simulate amplifier-core having a release.
            "amplifier_app_cli.utils.update_executor.check_pypi_packages_for_updates",
            side_effect=fake_pypi_has_update,
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
        "check_pypi_packages_for_updates() returned True but the update gate did not\n"
        "forward umbrella_info to execute_updates — PyPI update detection is broken.\n"
        f"captured={captured!r}\n"
        f"Command output:\n{result.output}"
    )


def test_update_command_exits_early_when_pypi_is_current():
    """When PyPI reports both packages are current, the early-exit path fires.

    This is the anti-regression test for the original bug:
      `amplifier update` prompted and ran `uv tool install` on every invocation
      even when all SHAs matched, because has_umbrella_updates was unconditionally
      set to True.

    After the fix, check_pypi_packages_for_updates() returning False means
    nothing_to_update=True and execute_updates must NOT be called.
    """
    from click.testing import CliRunner

    from amplifier_app_cli.commands.update import update
    from amplifier_app_cli.utils.source_status import UpdateReport

    fake_info = UmbrellaInfo(
        url="https://github.com/microsoft/amplifier",
        ref="main",
        commit_id=None,
    )
    execute_updates_called = False

    async def fake_execute_updates(
        report, umbrella_info=None, progress_callback=None, force=False
    ):
        nonlocal execute_updates_called
        execute_updates_called = True
        from amplifier_app_cli.utils.update_executor import ExecutionResult

        return ExecutionResult(success=True, updated=[], messages=[])

    # check_pypi_packages_for_updates returns False → everything is current.
    async def fake_pypi_all_current():
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
            # Imported inside the update() body from update_executor —
            # patch the source module attribute.
            "amplifier_app_cli.utils.update_executor.check_pypi_packages_for_updates",
            side_effect=fake_pypi_all_current,
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
            "amplifier_app_cli.commands.update.save_update_last_check",
            return_value=None,
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(update, ["--yes"])

    if result.exception and not isinstance(result.exception, SystemExit):
        import traceback

        raise AssertionError(
            f"update command raised an exception:\n"
            f"{''.join(traceback.format_exception(type(result.exception), result.exception, result.exception.__traceback__))}\n"
            f"Output:\n{result.output}"
        )

    assert not execute_updates_called, (
        "execute_updates was called when check_pypi_packages_for_updates() returned False.\n"
        "The early-exit path ('All sources up to date') must fire when PyPI confirms\n"
        "both packages are current — the spurious prompt bug has regressed.\n"
        f"Command output:\n{result.output}"
    )
    assert "up to date" in result.output.lower(), (
        "Expected '✓ All sources up to date' in output when nothing needs updating.\n"
        f"Actual output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Unit tests for check_pypi_packages_for_updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_pypi_both_current_returns_false():
    """amplifier-core at PyPI-confirmed latest → returns False (no update prompt).

    Only amplifier-core is checked; the amplifier umbrella is git-sourced and
    covered by the amplifier-app-cli / amplifier-foundation git rows.
    """
    import importlib.metadata
    from unittest.mock import AsyncMock, MagicMock

    from amplifier_app_cli.utils.update_executor import check_pypi_packages_for_updates

    installed = {"amplifier-core": "1.0.10"}
    latest = {"amplifier-core": "1.0.10"}

    def fake_version(pkg: str) -> str:
        if pkg in installed:
            return installed[pkg]
        raise importlib.metadata.PackageNotFoundError(pkg)

    async def fake_get(url: str, **kwargs):
        for pkg in latest:
            if f"/{pkg}/json" in url:
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json.return_value = {"info": {"version": latest[pkg]}}
                return resp
        raise ValueError(f"Unexpected PyPI URL: {url}")

    mock_client = MagicMock()
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("importlib.metadata.version", side_effect=fake_version),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await check_pypi_packages_for_updates()

    assert result is False, (
        f"Expected False when amplifier-core matches PyPI latest, but got {result!r}"
    )


@pytest.mark.asyncio
async def test_check_pypi_amplifier_core_update_returns_true():
    """amplifier-core has a newer PyPI release → returns True."""
    import importlib.metadata
    from unittest.mock import AsyncMock, MagicMock

    from amplifier_app_cli.utils.update_executor import check_pypi_packages_for_updates

    installed = {"amplifier-core": "1.0.9"}
    latest = {"amplifier-core": "1.0.10"}

    def fake_version(pkg: str) -> str:
        if pkg in installed:
            return installed[pkg]
        raise importlib.metadata.PackageNotFoundError(pkg)

    async def fake_get(url: str, **kwargs):
        for pkg in latest:
            if f"/{pkg}/json" in url:
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json.return_value = {"info": {"version": latest[pkg]}}
                return resp
        raise ValueError(f"Unexpected PyPI URL: {url}")

    mock_client = MagicMock()
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("importlib.metadata.version", side_effect=fake_version),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await check_pypi_packages_for_updates()

    assert result is True, (
        "Expected True when amplifier-core has a newer release on PyPI, "
        f"but got {result!r}"
    )


@pytest.mark.asyncio
async def test_check_pypi_timeout_returns_true_and_logs_warning(caplog):
    """PyPI timeout → returns True (assume stale) and emits a WARNING.

    This is the conservative failure policy: we'd rather run a redundant
    `uv tool install` than silently leave a user on a stale version.
    """
    import importlib.metadata
    import logging
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from amplifier_app_cli.utils.update_executor import check_pypi_packages_for_updates

    def fake_version(pkg: str) -> str:
        if pkg == "amplifier-core":
            return "1.0.0"
        raise importlib.metadata.PackageNotFoundError(pkg)

    async def fake_get_timeout(url: str, **kwargs):
        raise httpx.TimeoutException("timed out", request=MagicMock())

    mock_client = MagicMock()
    mock_client.get = fake_get_timeout
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with caplog.at_level(
        logging.WARNING, logger="amplifier_app_cli.utils.update_executor"
    ):
        with (
            patch("importlib.metadata.version", side_effect=fake_version),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await check_pypi_packages_for_updates()

    assert result is True, (
        f"Expected True (assume stale) when PyPI request times out, but got {result!r}"
    )
    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("stale" in m.lower() or "assuming" in m.lower() for m in warning_msgs), (
        "Expected a WARNING mentioning 'stale' or 'assuming' on PyPI timeout.\n"
        f"Actual warnings: {warning_msgs}"
    )


@pytest.mark.asyncio
async def test_check_pypi_no_false_positive_on_locally_newer_version():
    """Installed 1.4.10 with PyPI at 1.4.9 must NOT trigger an update.

    String comparison "1.4.10" < "1.4.9" is True (lexicographic), but
    packaging.version.Version("1.4.10") > Version("1.4.9") — the correct result.
    This test catches a regression back to string-based comparison.
    """
    import importlib.metadata
    from unittest.mock import AsyncMock, MagicMock

    from amplifier_app_cli.utils.update_executor import check_pypi_packages_for_updates

    # Installed is NEWER than what PyPI reports (e.g. pre-release locally)
    installed = {"amplifier-core": "1.4.10"}
    latest = {"amplifier-core": "1.4.9"}

    def fake_version(pkg: str) -> str:
        if pkg in installed:
            return installed[pkg]
        raise importlib.metadata.PackageNotFoundError(pkg)

    async def fake_get(url: str, **kwargs):
        for pkg in latest:
            if f"/{pkg}/json" in url:
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json.return_value = {"info": {"version": latest[pkg]}}
                return resp
        raise ValueError(f"Unexpected PyPI URL: {url}")

    mock_client = MagicMock()
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("importlib.metadata.version", side_effect=fake_version),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await check_pypi_packages_for_updates()

    assert result is False, (
        "False positive: installed 1.4.10 was flagged as outdated vs PyPI 1.4.9.\n"
        "This means string comparison is being used instead of packaging.version.Version.\n"
        f"Got: {result!r}"
    )


@pytest.mark.asyncio
async def test_check_pypi_returns_false_on_404(caplog):
    """HTTP 404 from PyPI means 'package not on PyPI' → returns False, INFO logged.

    404 is a definitive answer (not an outage), so we treat it as
    'no update available for this package' rather than 'assume stale'.
    This is the fix for the amplifier umbrella always-prompts bug: the
    amplifier package is not on PyPI and returns 404, which the old code
    incorrectly treated as an outage and assumed stale.
    """
    import importlib.metadata
    import logging
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from amplifier_app_cli.utils.update_executor import check_pypi_packages_for_updates

    def fake_version(pkg: str) -> str:
        if pkg == "amplifier-core":
            return "1.0.0"
        raise importlib.metadata.PackageNotFoundError(pkg)

    async def fake_get_404(url: str, **kwargs):
        mock_response = MagicMock()
        mock_response.status_code = 404
        raise httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=mock_response,
        )

    mock_client = MagicMock()
    mock_client.get = fake_get_404
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with caplog.at_level(
        logging.INFO, logger="amplifier_app_cli.utils.update_executor"
    ):
        with (
            patch("importlib.metadata.version", side_effect=fake_version),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await check_pypi_packages_for_updates()

    assert result is False, (
        f"Expected False when PyPI returns 404 (package not found), but got {result!r}"
    )
    info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "not found" in m.lower()
        or "not on pypi" in m.lower()
        or "skipping" in m.lower()
        for m in info_msgs
    ), (
        "Expected an INFO message indicating the package was not found on PyPI.\n"
        f"Actual INFO messages: {info_msgs}"
    )
    # Specifically must NOT emit a WARNING (404 is not an outage).
    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warning_msgs, (
        "Expected no WARNING for a 404 response (it's a definitive answer, not an outage).\n"
        f"Actual warnings: {warning_msgs}"
    )


@pytest.mark.asyncio
async def test_check_pypi_returns_true_on_500(caplog):
    """HTTP 500 from PyPI → returns True (assume stale) and emits a WARNING.

    A 5xx response means PyPI is having trouble — we can't confirm whether
    we're up to date, so the conservative policy (assume stale) applies.
    This is different from 404 which is a definitive 'not on PyPI' answer.
    """
    import importlib.metadata
    import logging
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from amplifier_app_cli.utils.update_executor import check_pypi_packages_for_updates

    def fake_version(pkg: str) -> str:
        if pkg == "amplifier-core":
            return "1.0.0"
        raise importlib.metadata.PackageNotFoundError(pkg)

    async def fake_get_500(url: str, **kwargs):
        mock_response = MagicMock()
        mock_response.status_code = 500
        raise httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=mock_response,
        )

    mock_client = MagicMock()
    mock_client.get = fake_get_500
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with caplog.at_level(
        logging.WARNING, logger="amplifier_app_cli.utils.update_executor"
    ):
        with (
            patch("importlib.metadata.version", side_effect=fake_version),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await check_pypi_packages_for_updates()

    assert result is True, (
        f"Expected True (assume stale) when PyPI returns 500, but got {result!r}"
    )
    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("stale" in m.lower() or "assuming" in m.lower() for m in warning_msgs), (
        "Expected a WARNING mentioning 'stale' or 'assuming' on PyPI 500 response.\n"
        f"Actual warnings: {warning_msgs}"
    )
