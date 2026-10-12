"""Startup-only opt-out coverage; no real update network or shared settings."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.utils import startup_checker


def test_opt_out_precedes_settings_read():
    with (
        patch.dict("os.environ", {"AMPLIFIER_SKIP_STARTUP_UPDATE_CHECK": "1"}),
        patch.object(
            startup_checker, "get_update_settings", side_effect=AssertionError("settings read")
        ) as settings,
    ):
        assert startup_checker.should_check_on_startup() is False
        settings.assert_not_called()


@pytest.mark.asyncio
async def test_opt_out_avoids_network_and_timestamp_write():
    with (
        patch.dict("os.environ", {"AMPLIFIER_SKIP_STARTUP_UPDATE_CHECK": "1"}),
        patch.object(startup_checker, "get_update_settings") as settings,
        patch.object(startup_checker.httpx, "AsyncClient") as client,
        patch.object(startup_checker, "check_all_sources", new_callable=AsyncMock) as check,
        patch.object(startup_checker, "save_update_last_check") as save,
    ):
        await startup_checker.check_and_notify()
        settings.assert_not_called()
        client.assert_not_called()
        check.assert_not_awaited()
        save.assert_not_called()


def test_opt_out_avoids_side_effects_through_startup_wrapper():
    from amplifier_app_cli.commands.run import _run_startup_update_check

    with (
        patch.dict("os.environ", {"AMPLIFIER_SKIP_STARTUP_UPDATE_CHECK": "1"}),
        patch.object(
            startup_checker, "get_update_settings", side_effect=AssertionError("settings read")
        ) as settings,
        patch.object(startup_checker.httpx, "AsyncClient") as client,
        patch.object(startup_checker, "check_all_sources", new_callable=AsyncMock) as check,
        patch.object(startup_checker, "save_update_last_check") as save,
    ):
        _run_startup_update_check()
        settings.assert_not_called()
        client.assert_not_called()
        check.assert_not_awaited()
        save.assert_not_called()


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", "1 ", " 1"])
def test_only_exact_one_skips_startup(value):
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(startup_checker, "get_update_settings", return_value={}) as settings,
    ):
        if value is not None:
            startup_checker.os.environ["AMPLIFIER_SKIP_STARTUP_UPDATE_CHECK"] = value
        assert startup_checker.should_check_on_startup() is True
        settings.assert_called_once_with()


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({}, True),
        ({"auto_prompt": False}, False),
        ({"last_check": None}, True),
        ({"last_check": "invalid"}, True),
        ({"last_check": 123}, True),
        ({"last_check": "2026-01-01T11:00:00"}, False),
        ({"last_check": "2026-01-01T08:00:00"}, True),
        ({"last_check": "2026-01-01T08:00:00", "check_frequency_hours": 8}, False),
    ],
)
def test_default_frequency_policy_is_preserved(settings, expected):
    now = datetime(2026, 1, 1, 12)
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(startup_checker, "get_update_settings", return_value=settings),
        patch.object(startup_checker, "datetime", wraps=datetime) as clock,
    ):
        clock.now.return_value = now
        assert startup_checker.should_check_on_startup() is expected


@pytest.mark.asyncio
async def test_recent_check_avoids_network_and_timestamp_write():
    settings = {"last_check": (datetime.now() - timedelta(minutes=1)).isoformat()}
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(startup_checker, "get_update_settings", return_value=settings),
        patch.object(startup_checker.httpx, "AsyncClient") as client,
        patch.object(startup_checker, "save_update_last_check") as save,
    ):
        await startup_checker.check_and_notify()
        client.assert_not_called()
        save.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_updates", [False, True])
async def test_default_check_still_runs_and_saves_timestamp(has_updates):
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(startup_checker, "get_update_settings", return_value={}),
        patch.object(startup_checker.httpx, "AsyncClient", return_value=fake_client),
        patch.object(
            startup_checker, "check_all_sources",
            new_callable=AsyncMock, return_value=SimpleNamespace(has_updates=has_updates),
        ) as check,
        patch.object(startup_checker, "save_update_last_check") as save,
        patch.object(startup_checker.console, "print") as display,
    ):
        await startup_checker.check_and_notify()
        check.assert_awaited_once_with(client=fake_client, include_all_cached=True)
        save.assert_called_once()
        assert isinstance(save.call_args.args[0], datetime)
        assert display.called is has_updates


@pytest.mark.asyncio
async def test_default_failure_still_saves_timestamp():
    """Characterize network failure handling, not timestamp-save failure handling."""
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(startup_checker, "get_update_settings", return_value={}),
        patch.object(startup_checker.httpx, "AsyncClient", side_effect=RuntimeError("offline")),
        patch.object(startup_checker, "save_update_last_check") as save,
    ):
        await startup_checker.check_and_notify()
        save.assert_called_once()
        assert isinstance(save.call_args.args[0], datetime)


def test_opt_out_does_not_disable_explicit_update():
    from click.testing import CliRunner

    from amplifier_app_cli.commands.update import update
    from amplifier_app_cli.utils.source_status import UpdateReport

    with (
        patch.dict("os.environ", {"AMPLIFIER_SKIP_STARTUP_UPDATE_CHECK": "1"}),
        patch(
            "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
            return_value=SimpleNamespace(),
        ),
        patch(
            "amplifier_app_cli.utils.update_executor.check_pypi_packages_for_updates",
            new_callable=AsyncMock, return_value=False,
        ),
        patch(
            "amplifier_app_cli.commands.update.check_all_sources",
            new_callable=AsyncMock,
            return_value=UpdateReport(local_file_sources=[], cached_git_sources=[]),
        ) as check,
        patch(
            "amplifier_app_cli.commands.update._check_all_bundle_status",
            new_callable=AsyncMock, return_value={},
        ),
        patch(
            "amplifier_app_cli.commands.update._get_umbrella_dependency_details",
            new_callable=AsyncMock, return_value=[],
        ),
        patch("amplifier_app_cli.commands.update.save_update_last_check"),
    ):
        result = CliRunner().invoke(update, ["--check-only"])
        assert result.exit_code == 0, result.output
        check.assert_awaited_once()
