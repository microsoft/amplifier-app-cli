"""Tests for execute_self_update in update_executor."""

from unittest.mock import MagicMock, patch

import pytest

from amplifier_app_cli.utils.update_executor import execute_self_update


class FakeUmbrellaInfo:
    """Minimal stub for UmbrellaInfo."""

    url = "https://github.com/microsoft/amplifier"
    ref = "main"


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

    with patch("amplifier_app_cli.utils.update_executor.subprocess.Popen", side_effect=fake_popen):
        with patch(
            "amplifier_app_cli.utils.update_executor._invalidate_modules_with_missing_deps",
            return_value=(0, 0),
        ):
            await execute_self_update(FakeUmbrellaInfo())

    assert "--force" not in captured_cmd, (
        "uv must NOT use --force (it destroys the venv unnecessarily)"
    )
    assert "--upgrade" in captured_cmd, "uv must use --upgrade to check for newer versions"
    assert "--reinstall" in captured_cmd, "uv must use --reinstall to fully refresh packages"
