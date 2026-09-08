"""Regression coverage for exact Highway tmux lane-session lookups.

The fake tmux deliberately implements tmux's prefix matching for an ordinary
target and exact matching for the ``=<session>`` target form.  It never starts
or talks to a real tmux server.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason="the Highway scripts require GNU/Linux bash (stat -c %Y)",
)

SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "amplifier_app_cli/data/skills/ten-lane-highway/scripts"
)
READINESS = SCRIPTS / "highway_readiness.sh"
STATUS = SCRIPTS / "highway_status.sh"
WATCHDOG = SCRIPTS / "highway_watchdog.sh"
LAUNCH = SCRIPTS / "launch_lane.sh"
VERIFY = SCRIPTS / "verify_lane.sh"

OLD_LANE = "close-team-ci"
REPLACEMENT_LANE = "close-team-ci-r2"
OLD_SESSION = f"hw__batch__{OLD_LANE}"
REPLACEMENT_SESSION = f"hw__batch__{REPLACEMENT_LANE}"


def make_batch(tmp_path: Path) -> Path:
    """Write the old and replacement lanes into one manifest."""
    batch = tmp_path / "batch"
    batch.mkdir()
    rows = ["lane\tworktree\tbranch\tbase_sha\ttmux\tgoal\tlog\tlaunched_at"]
    for lane in (OLD_LANE, REPLACEMENT_LANE):
        worktree = batch / f"worktree-{lane}"
        worktree.mkdir()
        log = batch / f"{lane}.log"
        log.write_text("", encoding="utf-8")
        rows.append(
            f"{lane}\t{worktree}\tlane/{lane}\tHEAD\thw__batch__{lane}\t"
            f"goal\t{log}\t0"
        )
    (batch / "manifest.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return batch


def make_prefix_matching_fakes(tmp_path: Path) -> Path:
    """Create PATH fakes that model only the tmux behavior under test."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "tmux").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[ "$1" = -L ] && [ "$2" = "$EXPECTED_SOCKET" ] || exit 97
printf '%s\\n' "$*" >> "$TMUX_CALLS"
target=
for ((i = 1; i <= $#; i++)); do
  if [ "${!i}" = -t ]; then
    next=$((i + 1))
    target=${!next}
    break
  fi
done
case " $* " in
  *" has-session "*)
    if [[ "$target" = =* ]]; then
      target=${target#=}
      for live in ${LIVE_SESSIONS:-}; do [ "$live" = "$target" ] && exit 0; done
    else
      for live in ${LIVE_SESSIONS:-}; do [[ "$live" = "$target"* ]] && exit 0; done
    fi
    exit 1
    ;;
  *" new-session "*) exit 0 ;;
esac
exit 98
""",
        encoding="utf-8",
    )
    (bindir / "amplifier").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$AMPLIFIER_CALLS"
""",
        encoding="utf-8",
    )
    for command in bindir.iterdir():
        command.chmod(0o755)
    return bindir


def env_for(tmp_path: Path, bindir: Path) -> dict[str, str]:
    socket = f"exact-tmux-test-{tmp_path.name}"
    return {
        **{
            key: value
            for key, value in os.environ.items()
            if key not in {"BASH_ENV", "HIGHWAY_TMUX_SOCKET"}
        },
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "EXPECTED_SOCKET": socket,
        "HIGHWAY_TMUX_SOCKET": socket,
        "LIVE_SESSIONS": REPLACEMENT_SESSION,
        "TMUX_CALLS": str(tmp_path / "tmux-calls"),
        "AMPLIFIER_CALLS": str(tmp_path / "amplifier-calls"),
        "HIGHWAY_HB_GRACE": "0",
        "HIGHWAY_ACTIVE_WINDOW": "0",
        "HIGHWAY_WAKE_GAP": "0",
    }


def publish(batch: Path, env: dict[str, str], runnable: int = 0) -> None:
    result = subprocess.run(
        ["bash", str(READINESS), "publish", str(batch), str(runnable)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_status_counts_only_the_exact_replacement_session(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    env = env_for(tmp_path, make_prefix_matching_fakes(tmp_path))
    publish(batch, env)

    result = subprocess.run(
        ["bash", str(STATUS), str(batch), "2"],
        capture_output=True,
        text=True,
        env={**env, "HIGHWAY_JSON": "1"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["live"] == 1
    assert report["ended"] == 1


def test_watchdog_wakes_for_old_lane_ended_beside_live_replacement(
    tmp_path: Path,
) -> None:
    batch = make_batch(tmp_path)
    env = env_for(tmp_path, make_prefix_matching_fakes(tmp_path))
    publish(batch, env)

    result = subprocess.run(
        ["bash", str(WATCHDOG), str(batch), "2", "manager-session", "0", "1"],
        capture_output=True,
        text=True,
        env={**env, "HIGHWAY_WATCHDOG_MAX_POLLS": "1"},
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"lane(s) ended: {OLD_LANE}" in (batch / "wake-needed").read_text(
        encoding="utf-8"
    )
    assert "poll live=1" in (batch / "watchdog.log").read_text(encoding="utf-8")


def make_git_repo(tmp_path: Path) -> Path:
    """Make the disposable repository/worktree fixture needed by launch_lane."""
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repo)],
        capture_output=True,
        text=True,
        check=True,
    )
    (repo / "README").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Highway Test",
            "-c",
            "user.email=highway-test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return repo


def test_launcher_and_verify_do_not_treat_replacement_as_old_lane(
    tmp_path: Path,
) -> None:
    """An old target must launch and verify as ended despite a live old-r2."""
    batch = tmp_path / "batch"
    repo = make_git_repo(tmp_path)
    goal = tmp_path / "goal.md"
    goal.write_text("temporary fixture goal\n", encoding="utf-8")
    env = env_for(tmp_path, make_prefix_matching_fakes(tmp_path))

    launch = subprocess.run(
        [
            "bash",
            str(LAUNCH),
            str(batch),
            OLD_LANE,
            str(repo),
            str(goal),
            "HEAD",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert launch.returncode == 0, launch.stderr
    assert f"LAUNCHED: {OLD_SESSION}" in launch.stdout
    tmux_calls = Path(env["TMUX_CALLS"]).read_text(encoding="utf-8")
    assert f"has-session -t ={OLD_SESSION}" in tmux_calls
    assert f"new-session -d -s {OLD_SESSION}" in tmux_calls

    verify = subprocess.run(
        ["bash", str(VERIFY), str(batch), OLD_LANE],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert verify.returncode == 0, verify.stderr
    assert "TMUX: ended" in verify.stdout