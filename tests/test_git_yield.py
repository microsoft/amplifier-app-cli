from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from amplifier_app_cli.ui.git_yield import capture_git_diff


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.asyncio
async def test_git_snapshot_measures_tracked_and_untracked_turn_delta(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("first\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "initial")
    before = await capture_git_diff(tmp_path)

    tracked.write_text("first\nsecond\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("one\ntwo\n", encoding="utf-8")
    after = await capture_git_diff(tmp_path)
    delta = after.delta_from(before)

    assert before.available is True
    assert delta is not None
    assert delta.files == 2
    assert delta.additions == 3
    assert delta.deletions == 0
    assert delta.diff_label == "+3/−0"


@pytest.mark.asyncio
async def test_git_snapshot_is_unavailable_outside_a_repository(tmp_path: Path) -> None:
    snapshot = await capture_git_diff(tmp_path)

    assert snapshot.available is False
