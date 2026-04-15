"""Tests for shared uv utility functions in uv_utils.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_remove_stale_uv_lock_is_importable():
    """remove_stale_uv_lock must live in the shared uv_utils module."""
    from amplifier_app_cli.utils.uv_utils import remove_stale_uv_lock  # noqa: F401


def test_remove_stale_uv_lock_returns_false_when_no_lock(tmp_path):
    """Returns False when no uv.lock file exists in the cache dir."""
    from amplifier_app_cli.utils.uv_utils import remove_stale_uv_lock

    fake_cache = tmp_path / "uv_cache"
    fake_cache.mkdir()

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        if cmd == ["uv", "cache", "dir"]:
            result.returncode = 0
            result.stdout = str(fake_cache) + "\n"
        return result

    with patch("amplifier_app_cli.utils.uv_utils.subprocess.run", side_effect=fake_run):
        result = remove_stale_uv_lock()

    assert result is False


def test_remove_stale_uv_lock_returns_false_when_uv_running(tmp_path):
    """Returns False (lock is legitimate) when uv process is running."""
    from amplifier_app_cli.utils.uv_utils import remove_stale_uv_lock

    fake_cache = tmp_path / "uv_cache"
    fake_cache.mkdir()
    lock_file = fake_cache / "uv.lock"
    lock_file.touch()

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        if cmd == ["uv", "cache", "dir"]:
            result.returncode = 0
            result.stdout = str(fake_cache) + "\n"
        elif cmd == ["pgrep", "-x", "uv"]:
            result.returncode = 0  # uv IS running
        return result

    with patch("amplifier_app_cli.utils.uv_utils.subprocess.run", side_effect=fake_run):
        result = remove_stale_uv_lock()

    assert result is False
    assert lock_file.exists(), "Lock should NOT be removed when uv is running"


def test_remove_stale_uv_lock_removes_orphaned_lock(tmp_path):
    """Removes lock and returns True when lock exists and no uv process running."""
    from amplifier_app_cli.utils.uv_utils import remove_stale_uv_lock

    fake_cache = tmp_path / "uv_cache"
    fake_cache.mkdir()
    lock_file = fake_cache / "uv.lock"
    lock_file.touch()

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        if cmd == ["uv", "cache", "dir"]:
            result.returncode = 0
            result.stdout = str(fake_cache) + "\n"
        elif cmd == ["pgrep", "-x", "uv"]:
            result.returncode = 1  # uv is NOT running
        return result

    with patch("amplifier_app_cli.utils.uv_utils.subprocess.run", side_effect=fake_run):
        with patch("amplifier_app_cli.utils.uv_utils.console"):
            result = remove_stale_uv_lock()

    assert result is True
    assert not lock_file.exists(), "Stale lock should be removed"


def test_execute_self_update_calls_stale_lock_cleanup_before_popen():
    """execute_self_update must call remove_stale_uv_lock before subprocess.Popen.

    This is the guard that prevents amplifier update from hanging when a
    stale uv.lock file is present from a previously interrupted uv process.
    """
    import asyncio

    from amplifier_app_cli.utils.umbrella_discovery import UmbrellaInfo
    from amplifier_app_cli.utils.update_executor import execute_self_update

    umbrella = UmbrellaInfo(
        url="https://github.com/microsoft/amplifier",
        ref="main",
        commit_id=None,
    )

    call_order: list[str] = []

    def fake_remove_stale_lock():
        call_order.append("remove_stale_uv_lock")
        return False

    def fake_popen(cmd, **kwargs):
        call_order.append("Popen")
        mock_proc = MagicMock()
        mock_proc.stderr = iter([])
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        return mock_proc

    with patch(
        "amplifier_app_cli.utils.update_executor.remove_stale_uv_lock",
        side_effect=fake_remove_stale_lock,
    ):
        with patch(
            "amplifier_app_cli.utils.update_executor.subprocess.Popen",
            side_effect=fake_popen,
        ):
            with patch(
                "amplifier_app_cli.utils.update_executor._invalidate_modules_with_missing_deps",
                return_value=(0, 0),
            ):
                asyncio.run(execute_self_update(umbrella))

    assert "remove_stale_uv_lock" in call_order, (
        "execute_self_update must call remove_stale_uv_lock"
    )
    assert "Popen" in call_order, "execute_self_update must call Popen"

    lock_idx = call_order.index("remove_stale_uv_lock")
    popen_idx = call_order.index("Popen")
    assert lock_idx < popen_idx, (
        "remove_stale_uv_lock must be called BEFORE subprocess.Popen "
        f"(got order: {call_order})"
    )


def test_reset_imports_remove_stale_uv_lock_from_uv_utils():
    """reset.py must import _remove_stale_uv_lock from uv_utils, not define it inline."""
    import ast
    import inspect

    from amplifier_app_cli.commands import reset as reset_module

    # The function should NOT be defined in reset.py itself anymore
    source = inspect.getsource(reset_module)
    tree = ast.parse(source)

    inline_defs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_remove_stale_uv_lock"
    ]
    assert not inline_defs, (
        "_remove_stale_uv_lock must NOT be defined inline in reset.py — "
        "it should be imported from amplifier_app_cli.utils.uv_utils"
    )
