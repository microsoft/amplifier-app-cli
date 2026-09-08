"""Observable readiness and watchdog behavior for the shipped Highway scripts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
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


def make_batch(tmp_path: Path, lanes: tuple[str, ...] = ()) -> Path:
    batch = tmp_path / "batch"
    batch.mkdir()
    rows = ["lane\tworktree\tbranch\tbase_sha\ttmux\tgoal\tlog\tlaunched_at"]
    for lane in lanes:
        worktree = batch / f"worktree-{lane}"
        worktree.mkdir()
        log = batch / f"{lane}.log"
        log.write_text("", encoding="utf-8")
        rows.append(
            f"{lane}\t{worktree}\tlane/{lane}\tHEAD\thw__batch__{lane}\tgoal\t{log}\t0"
        )
    (batch / "manifest.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return batch


def make_fakes(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "tmux").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[ "$1" = -L ] && [ "$2" = "$EXPECTED_SOCKET" ] || exit 97
last="${@: -1}"
last="${last#=}"
for arg in "$@"; do
  if [ "$arg" = has-session ]; then
    case " ${LIVE_SESSIONS:-} " in *" $last "*) exit 0 ;; *) exit 1 ;; esac
  fi
done
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


def env_for(tmp_path: Path, bindir: Path, *, live: str = "") -> dict[str, str]:
    calls = tmp_path / "amplifier-calls"
    return {
        **{key: value for key, value in os.environ.items() if key != "BASH_ENV"},
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "EXPECTED_SOCKET": f"readiness-test-{tmp_path.name}",
        "HIGHWAY_TMUX_SOCKET": f"readiness-test-{tmp_path.name}",
        "LIVE_SESSIONS": live,
        "AMPLIFIER_CALLS": str(calls),
        "HIGHWAY_HB_GRACE": "0",
        "HIGHWAY_ACTIVE_WINDOW": "0",
        "HIGHWAY_WAKE_GAP": "0",
    }


def publish(batch: Path, runnable: int, env: dict[str, str]) -> None:
    result = subprocess.run(
        ["bash", str(READINESS), "publish", str(batch), str(runnable)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def run_watchdog(batch: Path, env: dict[str, str], polls: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(WATCHDOG), str(batch), "10", "manager-session", "0", "1"],
        capture_output=True,
        text=True,
        env={**env, "HIGHWAY_WATCHDOG_MAX_POLLS": str(polls)},
        timeout=10,
        check=False,
    )


def call_count(env: dict[str, str]) -> int:
    calls = Path(env["AMPLIFIER_CALLS"])
    return len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0


def test_drained_batch_does_not_wake_or_escalate(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    env = env_for(tmp_path, make_fakes(tmp_path))
    publish(batch, 0, env)

    result = run_watchdog(batch, env, polls=4)

    assert result.returncode == 0, result.stderr
    assert call_count(env) == 0
    assert not (batch / "wake-needed").exists()
    assert not (batch / "escalation-needed").exists()


def test_refilled_work_wakes_and_status_reports_shared_readiness(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    env = env_for(tmp_path, make_fakes(tmp_path))
    publish(batch, 0, env)
    assert run_watchdog(batch, env, polls=2).returncode == 0

    publish(batch, 1, env)
    result = run_watchdog(batch, env, polls=1)
    status = subprocess.run(
        ["bash", str(STATUS), str(batch), "10"],
        capture_output=True,
        text=True,
        env={**env, "HIGHWAY_JSON": "1"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert call_count(env) == 1
    report = json.loads(status.stdout)
    assert report["readiness"] == "runnable"
    assert report["ready"] == 1
    assert report["deficit"] == 1


def test_persisting_runnable_work_escalates_after_launch_failure(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    env = env_for(tmp_path, make_fakes(tmp_path))
    publish(batch, 1, env)

    result = run_watchdog(
        batch, {**env, "HIGHWAY_ESCALATE_AFTER": "3"}, polls=4
    )

    assert result.returncode == 0, result.stderr
    assert call_count(env) == 4
    assert (batch / "escalation-needed").exists()
    assert "ESCALATION WAKE" in (batch / "watchdog.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ("missing", "stale", "malformed", "extra-field"))
def test_unknown_or_stale_readiness_is_explicit_not_zero(tmp_path: Path, kind: str) -> None:
    batch = make_batch(tmp_path)
    env = env_for(tmp_path, make_fakes(tmp_path))
    if kind == "stale":
        publish(batch, 1, env)
        old = time.time() - 3601
        os.utime(batch / ".runnable-work", (old, old))
    elif kind == "malformed":
        (batch / ".runnable-work").write_text("v0\t1\n", encoding="utf-8")
    elif kind == "extra-field":
        (batch / ".runnable-work").write_text("v1\t1\tunexpected\n", encoding="utf-8")

    status = subprocess.run(
        ["bash", str(STATUS), str(batch), "10"],
        capture_output=True,
        text=True,
        env={**env, "HIGHWAY_JSON": "1", "HIGHWAY_READINESS_MAX": "60"},
        check=False,
    )
    result = run_watchdog(
        batch, {**env, "HIGHWAY_READINESS_MAX": "60"}, polls=3
    )

    assert status.returncode == 0, status.stderr
    report = json.loads(status.stdout)
    assert report["readiness"] == ("stale" if kind == "stale" else "unknown")
    assert report["ready"] is None
    assert report["deficit"] is None
    assert result.returncode == 0, result.stderr
    assert call_count(env) == 1
    assert not (batch / "escalation-needed").exists()


def test_ended_lane_wakes_even_when_batch_is_drained(tmp_path: Path) -> None:
    batch = make_batch(tmp_path, ("ended",))
    env = env_for(tmp_path, make_fakes(tmp_path))
    publish(batch, 0, env)

    result = run_watchdog(batch, env, polls=1)

    assert result.returncode == 0, result.stderr
    assert call_count(env) == 1
    assert "lane(s) ended: ended" in (batch / "wake-needed").read_text(encoding="utf-8")


def test_stale_heartbeat_wakes_while_a_drained_batch_has_live_lanes(tmp_path: Path) -> None:
    batch = make_batch(tmp_path, ("live",))
    env = env_for(
        tmp_path, make_fakes(tmp_path), live="hw__batch__live"
    )
    publish(batch, 0, env)
    heartbeat = batch / ".manager-heartbeat"
    heartbeat.touch()
    old = time.time() - 1900
    os.utime(heartbeat, (old, old))

    result = run_watchdog(batch, env, polls=1)

    assert result.returncode == 0, result.stderr
    assert call_count(env) == 1
    assert "heartbeat stale" in (batch / "wake-needed").read_text(encoding="utf-8")