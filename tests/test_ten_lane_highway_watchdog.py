"""Guards on the shipped ten-lane-highway ``highway_watchdog.sh`` runtime cap.

model_performance-6c3y. The supervisor that exists to catch stalled lanes
stopped itself and nothing caught THAT. Measured, from ``watchdog.log`` of the
run that filed the item:

    2026-09-02T14:04:27Z watchdog start ... max=12h
    2026-09-03T02:10:29Z watchdog start ... max=12h      (second generation)
    2026-09-03T14:14:05Z WAKE: watchdog max runtime (12h) reached - restart me
    2026-09-06T00:26:06Z watchdog start ...              (~34h later)

Between those last two lines the highway sat at live=0 with four lanes ENDED and
unverified. The final wake DID reach the manager -- the resumed session's own
reasoning is in the log ("restart the watchdog before doing anything else") --
and then it ran ``tmux kill-session -t hw-watchdog__<batch>``. It was running
*inside* that session, as a child of the very watchdog it was killing, so it
killed itself mid-restart. That is why the log carries neither ``wake delivered``
nor ``exit: deadline reached`` for that generation: the process was killed, not
exited. Three properties follow, and each has a test below:

1. supervision CONTINUES past the cap while the batch is open (re-exec), so the
   restart never depends on a human or a model doing anything at all;
2. an abandoned batch STILL winds down -- the cap's original purpose, an orphan
   never outliving its batch, is preserved rather than traded away;
3. the death notice is forced (never eaten by WAKE_GAP) and dispatched DETACHED,
   so whoever answers it is not living inside the process group they are being
   asked to replace.

Every case runs the real script as a subprocess and asserts a real outcome. The
``amplifier`` on PATH is a stub that records its argv, so "no wake was sent" is
proven by an absent record rather than inferred from an exit code -- the same
standard ``test_ten_lane_highway_infra_ledger.py`` set for the sweep guards.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

# POSIX shell (exec, setsid, stat -c, tab-delimited read loops), only ever run on
# the POSIX hosts that run a highway -- same module-level skip as the ledger tests.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason=(
        "highway_watchdog.sh is a GNU/Linux shell script: it uses `stat -c %Y`, a GNU "
        "coreutils flag with no BSD/macOS equivalent -- the script documents "
        "this itself at highway_status.sh:53. Requires Linux + bash."
    ),
)

SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / "amplifier_app_cli"
    / "data"
    / "skills"
    / "ten-lane-highway"
    / "scripts"
)
WATCHDOG = SCRIPTS / "highway_watchdog.sh"
STATUS = SCRIPTS / "highway_status.sh"

MANIFEST_HEADER = "lane\twt\tbranch\tbase\ttmux\tgoal\tlog\tts\n"


def write_manifest(batch: Path, *lanes: str) -> None:
    """Manifest rows whose tmux sessions deliberately do not exist (= ENDED)."""
    rows = [MANIFEST_HEADER]
    for lane in lanes:
        rows.append(
            f"{lane}\t{batch}/lanes/{lane}/repo\tlane/{lane}\torigin/main\t"
            f"hw__test__{lane}\t{batch}/goals/{lane}.md\t{batch}/lanes/{lane}/lane.log\t2026-09-05T00:00:00Z\n"
        )
    (batch / "manifest.tsv").write_text("".join(rows), encoding="utf-8")


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """A PATH shim whose `amplifier` records every invocation to amplifier-calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "amplifier-calls"
    stub = bin_dir / "amplifier"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {record}\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


def calls(tmp_path: Path) -> list[str]:
    record = tmp_path / "amplifier-calls"
    if not record.exists():
        return []
    return [line for line in record.read_text(encoding="utf-8").splitlines() if line.strip()]


def watchdog_env(stub_bin: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"
    # NEVER the default `hw` socket: a test must not read (or race) the tmux
    # server of a highway that is actually running on this host.
    env["HIGHWAY_TMUX_SOCKET"] = "hw-test-6c3y"
    env.update(overrides)
    return env


def spawn_watchdog(
    batch: Path,
    stub_bin: Path,
    *,
    interval: str = "1",
    argv0: Path = WATCHDOG,
    via_bash: bool = True,
    **env_overrides: str,
) -> subprocess.Popen:
    cmd = ["bash", str(argv0)] if via_bash else [str(argv0)]
    return subprocess.Popen(
        [*cmd, str(batch), "3", "sess-6c3y", interval],
        env=watchdog_env(stub_bin, **env_overrides),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


@pytest.fixture
def reaper() -> Iterator[list[subprocess.Popen]]:
    """Guarantees no watchdog survives a failing assertion."""
    procs: list[subprocess.Popen] = []
    yield procs
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def log_of(batch: Path) -> str:
    logf = batch / "watchdog.log"
    return logf.read_text(encoding="utf-8") if logf.exists() else ""


def wait_for(predicate, timeout: float = 20.0, poll: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


# ---------------------------------------------------------------------------
# The guards must remain FINDABLE by marker, not merely present in spirit.
#
# The manager's drift check (BATCH_DIR/check_skill_guards.sh) greps the
# INSTALLED tree for named strings and reports which fix is missing. A renamed
# marker makes that check go quietly blind -- the 2nz failure mode.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "marker"),
    [
        (WATCHDOG, "CAP RE-ARM GUARD"),
        (WATCHDOG, "SUPERVISION HEARTBEAT"),
        (STATUS, "SUPERVISION HEARTBEAT"),
    ],
)
def test_guard_markers_are_greppable(script: Path, marker: str) -> None:
    text = script.read_text(encoding="utf-8")
    assert marker in text, f"drift-check marker '{marker}' (model_performance-6c3y) missing from {script.name}"


# ---------------------------------------------------------------------------
# BRANCH 1 -- batch still open: supervision continues past the cap.
# ---------------------------------------------------------------------------


def test_cap_rearms_while_batch_is_open(tmp_path: Path, stub_bin: Path, reaper: list) -> None:
    """The fix. At the cap, with the batch open, the process re-execs itself.

    Asserted on the log's own generation counter: >= 2 `watchdog start` lines
    from ONE spawned process is only possible through the re-exec path.
    """
    batch = tmp_path
    (batch / "lanes").mkdir()
    (batch / ".manager-heartbeat").touch()
    write_manifest(batch, "aaa")

    proc = spawn_watchdog(batch, stub_bin, HIGHWAY_MAX_SECONDS="0")
    reaper.append(proc)

    assert wait_for(lambda: "generation=3" in log_of(batch)), (
        f"watchdog did not re-arm past its cap; log was:\n{log_of(batch)}"
    )
    assert proc.poll() is None, "watchdog exited at the cap despite the batch being open"

    log = log_of(batch)
    assert "CAP RE-ARM:" in log
    assert "lanes dir present, manager heartbeat" in log
    # A re-arm needs NOTHING from the manager: no wake, no wake-needed entry.
    assert not (batch / "wake-needed").exists(), "re-arm cost the manager an interrupt"
    assert calls(tmp_path) == [], f"re-arm woke the session: {calls(tmp_path)}"


def test_cap_rearms_even_when_the_exec_bit_is_stripped(tmp_path: Path, stub_bin: Path, reaper: list) -> None:
    """`exec "$0"` is not the only path; a non-executable copy still re-arms.

    A packaging step that drops the exec bit would otherwise turn the re-arm
    into a silent exit -- the exact class of failure this item closes.
    """
    batch = tmp_path
    (batch / "lanes").mkdir()
    (batch / ".manager-heartbeat").touch()
    write_manifest(batch, "aaa")
    copy = tmp_path / "no-exec-watchdog.sh"
    shutil.copyfile(WATCHDOG, copy)
    copy.chmod(0o644)

    proc = spawn_watchdog(batch, stub_bin, argv0=copy, HIGHWAY_MAX_SECONDS="0")
    reaper.append(proc)

    assert wait_for(lambda: "generation=2" in log_of(batch)), (
        f"non-executable watchdog did not re-arm; log was:\n{log_of(batch)}"
    )
    assert "retrying through" in log_of(batch)
    assert proc.poll() is None


def test_watchdog_heartbeat_is_refreshed_every_poll(tmp_path: Path, stub_bin: Path, reaper: list) -> None:
    """Proof-of-life the MANAGER can read: .watchdog-heartbeat, touched per poll."""
    batch = tmp_path
    (batch / "lanes").mkdir()
    (batch / ".manager-heartbeat").touch()
    write_manifest(batch, "aaa")
    hb = batch / ".watchdog-heartbeat"

    proc = spawn_watchdog(batch, stub_bin, HIGHWAY_MAX_SECONDS="3600")
    reaper.append(proc)

    assert wait_for(hb.exists), "watchdog never wrote .watchdog-heartbeat"
    first = hb.stat().st_mtime
    assert wait_for(lambda: hb.stat().st_mtime > first), "heartbeat written once at start but never refreshed"


# ---------------------------------------------------------------------------
# BRANCH 2 -- batch NOT open: the cap still winds the watchdog down.
#
# This is the trade the cap was added to make. Removing the cap would have been
# a far smaller change than re-arming; it would also have reversed the trade.
# ---------------------------------------------------------------------------


def test_cap_winds_down_when_the_batch_is_gone(tmp_path: Path, stub_bin: Path, reaper: list) -> None:
    batch = tmp_path  # deliberately no lanes/ directory
    (batch / ".manager-heartbeat").touch()
    write_manifest(batch, "aaa")

    proc = spawn_watchdog(batch, stub_bin, HIGHWAY_MAX_SECONDS="0")
    reaper.append(proc)

    assert proc.wait(timeout=30) == 0, "abandoned batch: watchdog did not wind down at its cap"
    log = log_of(batch)
    assert "exit: deadline reached, batch not open (no lanes dir" in log, log
    assert "CAP RE-ARM:" not in log, "re-armed on an abandoned batch -- the orphan the cap exists to prevent"


def test_cap_winds_down_when_the_manager_has_gone_home(tmp_path: Path, stub_bin: Path, reaper: list) -> None:
    """Lanes dir present, but the manager has not checked in for hours."""
    batch = tmp_path
    (batch / "lanes").mkdir()
    hb = batch / ".manager-heartbeat"
    hb.touch()
    ancient = time.time() - 100_000
    os.utime(hb, (ancient, ancient))
    write_manifest(batch, "aaa")

    proc = spawn_watchdog(batch, stub_bin, HIGHWAY_MAX_SECONDS="0")
    reaper.append(proc)

    assert proc.wait(timeout=30) == 0, "ancient heartbeat: watchdog did not wind down at its cap"
    assert "manager heartbeat ancient" in log_of(batch)


def test_abandon_window_is_configurable_and_reopens_the_rearm(
    tmp_path: Path, stub_bin: Path, reaper: list
) -> None:
    """The same ancient heartbeat re-arms once HIGHWAY_ABANDON_MAX allows it.

    Pins the documented escape hatch: the wind-down above is the window's doing,
    not an unrelated refusal.
    """
    batch = tmp_path
    (batch / "lanes").mkdir()
    hb = batch / ".manager-heartbeat"
    hb.touch()
    ancient = time.time() - 100_000
    os.utime(hb, (ancient, ancient))
    write_manifest(batch, "aaa")

    proc = spawn_watchdog(batch, stub_bin, HIGHWAY_MAX_SECONDS="0", HIGHWAY_ABANDON_MAX="200000")
    reaper.append(proc)

    assert wait_for(lambda: "CAP RE-ARM:" in log_of(batch)), (
        f"widened abandon window did not restore the re-arm; log was:\n{log_of(batch)}"
    )
    assert proc.poll() is None


# ---------------------------------------------------------------------------
# BRANCH 2, continued -- the death notice must actually reach someone.
# ---------------------------------------------------------------------------


def test_final_notice_is_dispatched_and_names_the_suicide_trap(
    tmp_path: Path, stub_bin: Path, reaper: list
) -> None:
    """On wind-down: durable file AND a resume, dispatched detached.

    The prompt has to warn the responder off `tmux kill-session`, because the
    responder is reading it from a session that a naive restart would kill --
    that is exactly how the measured ~34h outage happened.
    """
    batch = tmp_path
    (batch / ".manager-heartbeat").touch()
    write_manifest(batch, "aaa")

    proc = spawn_watchdog(batch, stub_bin, HIGHWAY_MAX_SECONDS="0")
    reaper.append(proc)

    assert proc.wait(timeout=30) == 0
    assert "final notice dispatched detached" in log_of(batch)

    wake_needed = batch / "wake-needed"
    assert wake_needed.exists(), "no durable wake-needed entry on wind-down"
    assert "watchdog max runtime" in wake_needed.read_text(encoding="utf-8")

    assert wait_for(lambda: len(calls(tmp_path)) >= 1), "final notice never reached `amplifier run --resume`"
    notice = calls(tmp_path)[-1]
    assert "run --resume sess-6c3y" in notice
    assert "tmux kill-session" in notice and "do NOT run" in notice
    assert "start a fresh watchdog" in notice


def test_final_notice_is_not_eaten_by_the_wake_gap(tmp_path: Path, stub_bin: Path, reaper: list) -> None:
    """A wake inside WAKE_GAP is suppressed; the death notice never is.

    Ordinary wake suppression is what makes the gap useful, and it is also what
    would silently discard the one message with no next poll behind it.
    """
    batch = tmp_path  # abandoned: no lanes/ dir
    hb = batch / ".manager-heartbeat"
    hb.touch()
    # Older than ACTIVE_WINDOW so wakes are not deferred, younger than HB_MAX so
    # the only pre-cap trigger is the ENDED lane below.
    stale = time.time() - 300
    os.utime(hb, (stale, stale))
    write_manifest(batch, "aaa")

    proc = spawn_watchdog(
        batch,
        stub_bin,
        HIGHWAY_MAX_SECONDS="4",
        HIGHWAY_WAKE_GAP="99999",
    )
    reaper.append(proc)

    assert proc.wait(timeout=40) == 0
    log = log_of(batch)
    assert "WAKE: lane(s) ended" in log, log
    assert "suppress wake (within 99999s gap)" in log, "the gap was never actually in force"

    entries = [ln for ln in (batch / "wake-needed").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(entries) == 2, f"expected the lane-end wake AND the forced death notice, got: {entries}"
    assert "watchdog max runtime" in entries[-1]
    assert any("do NOT run" in c for c in calls(tmp_path)), "death notice suppressed by the wake gap"


# ---------------------------------------------------------------------------
# The manager-facing half: a lapse is reported with a DURATION.
# ---------------------------------------------------------------------------


def status_json(batch: Path, stub_bin: Path) -> dict:
    proc = subprocess.run(
        ["bash", str(STATUS), str(batch), "3", "0"],
        env=watchdog_env(stub_bin, HIGHWAY_JSON="1"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_status_reports_how_long_supervision_has_been_down(tmp_path: Path, stub_bin: Path) -> None:
    batch = tmp_path
    write_manifest(batch, "aaa")
    hb = batch / ".watchdog-heartbeat"
    hb.touch()
    lapsed = time.time() - 4000
    os.utime(hb, (lapsed, lapsed))

    payload = status_json(batch, stub_bin)
    assert payload["watchdog"] == "DEAD"
    assert payload["watchdog_hb_age"] >= 3990, payload

    text = subprocess.run(
        ["bash", str(STATUS), str(batch), "3", "0"],
        env=watchdog_env(stub_bin),
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "SUPERVISION LAPSED" in text
    assert "watchdog_hb_age=" in text


def test_status_says_unknown_rather_than_zero_without_a_heartbeat(tmp_path: Path, stub_bin: Path) -> None:
    """No heartbeat file must never read as "lapsed 0s ago" (i.e. healthy)."""
    batch = tmp_path
    write_manifest(batch, "aaa")

    payload = status_json(batch, stub_bin)
    assert payload["watchdog_hb_age"] == -1, payload

    text = subprocess.run(
        ["bash", str(STATUS), str(batch), "3", "0"],
        env=watchdog_env(stub_bin),
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "lapse duration unknown" in text
