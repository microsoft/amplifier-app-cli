"""``highway_status.sh`` must report infra-ledger rows no live lane owns.

model_performance-ye80, second sub-finding. On 2026-09-05 a tmux server restart
killed three lanes at once and left SIX DTU containers RUNNING with open ledger
rows. Every component worked in isolation: the ledger recorded all six
correctly, and ``highway_status.sh`` reported the lanes ENDED. Nothing joined
the two, so "infrastructure with nothing driving it" (Rule 14) was invisible --
the manager found the six containers only by running ``incus list`` by hand.

The join is REPORTING ONLY. Nothing here destroys a row, and
``test_reporting_destroys_nothing`` proves that observably rather than by
inspection.

Follows tests/test_ten_lane_highway_infra_ledger.py's standard: run the real
script via subprocess, assert real exit codes and real output, and prove "ran
nothing" with an observable ``touch <sentinel>`` destroy_cmd -- never a
grep-for-a-marker, because the buggy path prints a success message.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# These are GNU/Linux shell scripts (`stat -c %Y`, tmux, bash); Windows runners
# have no equivalent. Same precedent as the infra_ledger and socket-default
# suites.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason=(
        "highway_status.sh is a GNU/Linux shell script: it uses `stat -c %Y`, a GNU "
        "coreutils flag with no BSD/macOS equivalent -- the script documents "
        "this itself at highway_status.sh:53. Requires Linux + bash."
    ),
)

SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "amplifier_app_cli/data/skills/ten-lane-highway/scripts"
)
STATUS = SCRIPTS / "highway_status.sh"
LEDGER = SCRIPTS / "infra_ledger.sh"


# ---------------------------------------------------------------------------
# Scenario builder
#
# tmux is the liveness SENSOR. It is substituted with a stub on PATH for the
# same reason lane_teardown.sh indirects the DTU CLI through AMPLIFIER_DT_CLI:
# the subject under test is the JOIN, and a test that needed a real terminal
# multiplexer to assert an arithmetic result would be both slower and less
# deterministic. `test_real_tmux_agrees_with_the_stub` then runs the whole
# thing against a REAL tmux server so the stub cannot hide an integration
# break.
# ---------------------------------------------------------------------------


def _make_batch(tmp_path: Path, lanes: list[str]) -> Path:
    """A batch dir with a manifest whose tmux session name is `hw__b__<lane>`."""
    batch = tmp_path / "batch"
    (batch / "wt").mkdir(parents=True)
    (batch / "log").write_text("", encoding="utf-8")
    rows = [
        "\t".join(
            [
                "lane",
                "worktree",
                "branch",
                "base_sha",
                "tmux",
                "goal",
                "log",
                "launched_at",
            ]
        )
    ]
    for lane in lanes:
        rows.append(
            "\t".join(
                [
                    lane,
                    str(batch / "wt"),
                    f"lane/{lane}",
                    "HEAD",
                    f"hw__b__{lane}",
                    "goal.md",
                    str(batch / "log"),
                    "0",
                ]
            )
        )
    (batch / "manifest.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return batch


def _stub_tmux(tmp_path: Path) -> Path:
    """A `tmux` that answers has-session only for names in $LIVE_SESSIONS."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "tmux"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do [ "$a" = "has-session" ] && want=1; done\n'
        'last="${@: -1}"\n'
        'if [ "${want:-0}" = 1 ]; then\n'
        '  case " ${LIVE_SESSIONS:-} " in *" $last "*) exit 0 ;; *) exit 1 ;; esac\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def _add_row(
    batch: Path, ident: str, destroy: str = "true", owner: str | None = None
) -> None:
    proc = subprocess.run(
        ["bash", str(LEDGER), str(batch), "add", "dtu", ident, *destroy.split()],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    if owner is not None:
        with (batch / "infra.owners.tsv").open("a", encoding="utf-8") as fh:
            fh.write(f"2026-09-06T00:00:00Z\t{ident}\t{owner}\n")


def _status(
    batch: Path,
    *,
    bindir: Path | None = None,
    live: tuple[str, ...] = (),
    socket: str | None = None,
    as_json: bool = True,
) -> subprocess.CompletedProcess:
    """Run the real script with an EXPLICITLY built environment.

    model_performance-etuz: a highway lane exports HIGHWAY_TMUX_SOCKET, so an
    inherited environment makes these tests pass in CI and fail inside every
    lane -- the worst possible direction for a test to be wrong in.
    """
    env = {k: v for k, v in os.environ.items() if k != "HIGHWAY_TMUX_SOCKET"}
    if bindir is not None:
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
        env["LIVE_SESSIONS"] = " ".join(f"hw__b__{lane}" for lane in live)
    if socket is not None:
        env["HIGHWAY_TMUX_SOCKET"] = socket
    if as_json:
        env["HIGHWAY_JSON"] = "1"
    else:
        env.pop("HIGHWAY_JSON", None)
    return subprocess.run(
        ["bash", str(STATUS), str(batch), "4", "0"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _report(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# The incident itself.
# ---------------------------------------------------------------------------


def test_the_ye80_incident_is_now_visible(tmp_path: Path) -> None:
    """Six rows, owning lane dead: the exact 2026-09-05 condition.

    Before this join the same batch reported `ended=1` and said nothing at all
    about the six containers still running and still billing.
    """
    batch = _make_batch(tmp_path, ["drbf-compaction-notice-ab"])
    bindir = _stub_tmux(tmp_path)
    for suffix in ("a", "b", "c", "a2", "b2", "c2"):
        _add_row(batch, f"val-drbf-{suffix}", owner="drbf-compaction-notice-ab")

    got = _report(_status(batch, bindir=bindir, live=()))

    assert got["orphan_rows"] == 6
    assert "drbf-compaction-notice-ab(6)" in got["orphan_owners"]


def test_a_live_lanes_rows_are_never_reported_as_orphaned(tmp_path: Path) -> None:
    """The deliverable that can be faked by making everything pass.

    A live lane and a dead lane hold rows SIMULTANEOUSLY. Counting every open
    row would also make the test above pass, so this is the case that
    distinguishes a real join from a row count.
    """
    batch = _make_batch(tmp_path, ["alive-lane-long", "drbf-compaction-notice-ab"])
    bindir = _stub_tmux(tmp_path)
    for suffix in ("a", "b"):
        _add_row(batch, f"val-alive-{suffix}", owner="alive-lane-long")
    for suffix in ("a", "b", "c", "a2", "b2", "c2"):
        _add_row(batch, f"val-drbf-{suffix}", owner="drbf-compaction-notice-ab")

    got = _report(_status(batch, bindir=bindir, live=("alive-lane-long",)))

    assert got["live"] == 1 and got["ended"] == 1
    assert got["orphan_rows"] == 6, "the live lane's two rows were counted as orphans"
    assert "alive-lane-long" not in got["orphan_owners"]
    assert "drbf-compaction-notice-ab(6)" in got["orphan_owners"]


def test_every_lane_live_means_no_orphans(tmp_path: Path) -> None:
    batch = _make_batch(tmp_path, ["alive-lane-long", "drbf-compaction-notice-ab"])
    bindir = _stub_tmux(tmp_path)
    _add_row(batch, "val-alive-a", owner="alive-lane-long")
    _add_row(batch, "val-drbf-a", owner="drbf-compaction-notice-ab")

    got = _report(
        _status(
            batch, bindir=bindir, live=("alive-lane-long", "drbf-compaction-notice-ab")
        )
    )

    assert got["orphan_rows"] == 0
    assert got["orphan_owners"] == ""


# ---------------------------------------------------------------------------
# Owner-name resolution. The two sides of this join disagree about what a lane
# is called, measured in the live batch's own files: infra.owners.tsv holds
# SHORT work-item ids for 5 of 13 owners and LONG manifest names for the other
# 8. An exact-match join would false-alarm on ~40% of owners, and a report that
# cries wolf is a report nobody reads.
# ---------------------------------------------------------------------------


def test_a_short_owner_id_resolves_to_its_live_lane_by_prefix(tmp_path: Path) -> None:
    """`vbs` owning a row while lane `vbs-cache-residency` is LIVE is not an orphan."""
    batch = _make_batch(tmp_path, ["vbs-cache-residency"])
    bindir = _stub_tmux(tmp_path)
    _add_row(batch, "val-vbs-a", owner="vbs")

    got = _report(_status(batch, bindir=bindir, live=("vbs-cache-residency",)))

    assert got["orphan_rows"] == 0, (
        "a short-id owner was not resolved to its live lane; every lane that "
        "claimed with a work-item id would be reported as orphaned"
    )


def test_a_short_owner_id_still_orphans_when_its_lane_is_dead(tmp_path: Path) -> None:
    """Prefix resolution must not become a blanket amnesty."""
    batch = _make_batch(tmp_path, ["vbs-cache-residency"])
    bindir = _stub_tmux(tmp_path)
    _add_row(batch, "val-vbs-a", owner="vbs")

    got = _report(_status(batch, bindir=bindir, live=()))

    assert got["orphan_rows"] == 1
    assert "vbs-cache-residency(1)" in got["orphan_owners"]


def test_an_ambiguous_owner_prefix_is_reported_not_guessed(tmp_path: Path) -> None:
    """`l1` against lanes `l1-alpha` and `l1-beta` cannot be attributed.

    Reporting is non-destructive, so the safe direction is to surface the row
    and say WHY it could not be attributed -- never to silently pick a lane.
    """
    batch = _make_batch(tmp_path, ["l1-alpha", "l1-beta"])
    bindir = _stub_tmux(tmp_path)
    _add_row(batch, "val-l1-a", owner="l1")

    got = _report(_status(batch, bindir=bindir, live=("l1-alpha", "l1-beta")))

    assert got["orphan_rows"] == 1
    assert "l1(ambiguous-lane)(1)" in got["orphan_owners"]


def test_an_owner_naming_no_lane_is_flagged_distinctly(tmp_path: Path) -> None:
    batch = _make_batch(tmp_path, ["real-lane"])
    bindir = _stub_tmux(tmp_path)
    _add_row(batch, "val-ghost-a", owner="zzz-ghost")

    got = _report(_status(batch, bindir=bindir, live=("real-lane",)))

    assert got["orphan_rows"] == 1
    assert "zzz-ghost(no-such-lane)(1)" in got["orphan_owners"]


def test_an_unclaimed_row_is_an_orphan(tmp_path: Path) -> None:
    """No claim means nothing demonstrably drives it -- the Rule 14 condition."""
    batch = _make_batch(tmp_path, ["real-lane"])
    bindir = _stub_tmux(tmp_path)
    _add_row(batch, "val-nobody-a", owner=None)

    got = _report(_status(batch, bindir=bindir, live=("real-lane",)))

    assert got["orphan_rows"] == 1
    assert "unclaimed" in got["orphan_owners"]


# ---------------------------------------------------------------------------
# Scope: only OPEN rows, and only reporting.
# ---------------------------------------------------------------------------


def test_closed_rows_are_never_counted(tmp_path: Path) -> None:
    """A swept row is reclaimed infrastructure, not an orphan."""
    batch = _make_batch(tmp_path, ["dead-lane"])
    bindir = _stub_tmux(tmp_path)
    _add_row(batch, "val-open-a", owner="dead-lane")
    _add_row(batch, "val-closed-a", owner="dead-lane")
    ledger = batch / "infra.tsv"
    ledger.write_text(
        ledger.read_text(encoding="utf-8").replace(
            "val-closed-a\topen", "val-closed-a\tswept"
        ),
        encoding="utf-8",
    )

    got = _report(_status(batch, bindir=bindir, live=()))

    assert got["orphan_rows"] == 1


def test_reporting_destroys_nothing(tmp_path: Path) -> None:
    """The observable proof, in the 2nz style.

    An exit code cannot distinguish "reported and left alone" from "reported
    and reaped". The sentinel can: if any destroy_cmd ran, the file exists.
    Automatic reaping was deliberately not implemented -- a false positive that
    prints is a nuisance, a false positive that destroys is another 0rg.
    """
    batch = _make_batch(tmp_path, ["dead-lane"])
    bindir = _stub_tmux(tmp_path)
    sentinel = tmp_path / "DESTROYED"
    _add_row(batch, "val-dead-a", destroy=f"touch {sentinel}", owner="dead-lane")
    before = (batch / "infra.tsv").read_bytes()

    got = _report(_status(batch, bindir=bindir, live=()))

    assert got["orphan_rows"] == 1
    assert not sentinel.exists(), "highway_status.sh RAN a destroy_cmd"
    assert (batch / "infra.tsv").read_bytes() == before, "the ledger was rewritten"


def test_a_batch_with_no_ledger_reports_zero(tmp_path: Path) -> None:
    """The common case -- most batches ledger nothing -- must not error."""
    batch = _make_batch(tmp_path, ["some-lane"])
    bindir = _stub_tmux(tmp_path)

    got = _report(_status(batch, bindir=bindir, live=()))

    assert got["orphan_rows"] == 0


def test_the_human_summary_names_the_owning_lane(tmp_path: Path) -> None:
    """A count alone is not actionable: teardown needs the lane NAME."""
    batch = _make_batch(tmp_path, ["drbf-compaction-notice-ab"])
    bindir = _stub_tmux(tmp_path)
    for suffix in ("a", "b", "c"):
        _add_row(batch, f"val-drbf-{suffix}", owner="drbf-compaction-notice-ab")

    proc = _status(batch, bindir=bindir, live=(), as_json=False)

    assert proc.returncode == 0, proc.stderr
    assert "orphan_rows=3" in proc.stdout
    assert "drbf-compaction-notice-ab" in proc.stdout
    assert "lane_teardown.sh" in proc.stdout, "the report must name the reclaim path"


# ---------------------------------------------------------------------------
# The stub must not be hiding an integration break.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("tmux") is None, reason="requires a real tmux")
def test_real_tmux_agrees_with_the_stub(tmp_path: Path) -> None:
    """Same scenario, real tmux server, on a socket private to this test.

    Kills the session mid-test: the SAME batch must flip from 0 orphans to 2
    with nothing else changing. That transition is the whole feature.
    """
    batch = _make_batch(tmp_path, ["live-lane", "dead-lane"])
    _add_row(batch, "val-live-a", owner="live-lane")
    _add_row(batch, "val-dead-a", owner="dead-lane")
    socket = f"hw-test-{os.getpid()}"

    def tmux(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux", "-L", socket, *args], capture_output=True, text=True, timeout=60
        )

    try:
        started = tmux("new-session", "-d", "-s", "hw__b__live-lane", "sleep 300")
        if started.returncode != 0:
            pytest.skip(f"tmux could not start a server here: {started.stderr.strip()}")

        got = _report(_status(batch, socket=socket))
        assert got["live"] == 1
        assert got["orphan_rows"] == 1, "a REAL live lane's row was reported orphaned"
        assert "dead-lane(1)" in got["orphan_owners"]

        tmux("kill-session", "-t", "hw__b__live-lane")

        after = _report(_status(batch, socket=socket))
        assert after["live"] == 0
        assert after["orphan_rows"] == 2, "killing the lane did not orphan its row"
    finally:
        tmux("kill-server")
