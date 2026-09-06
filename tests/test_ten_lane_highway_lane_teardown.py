"""Guards on the shipped ten-lane-highway ``lane_teardown.sh``.

WHY THIS FILE EXISTS (item model_performance-giwq)

``lane_teardown.sh`` is the ONLY lane-scoped teardown path -- the tool an
operator reaches for in an emergency. The alternative, ``infra_ledger.sh
sweep``, is the manager's batch-close verb that destroys EVERY lane's
infrastructure. Yet until this change the skill did not ship the tool at all:
``infra_ledger.sh``'s own refusal message sent operators to
``.amplifier/evaluation/tools/lane_teardown.sh`` -- a path in a DIFFERENT repo,
where the file was untracked (``git ls-files --error-unmatch`` -> "did not match
any file(s) known to git"). On 2026-09-05 six DTU containers were left running
with open ledger rows after a tmux-server restart killed three lanes, and
recovering them required exactly this tool.

The script is now adopted into ``scripts/`` beside its siblings, carrying lane
ye80b's near-miss fix. This file is the reason both stay true.

THE CASES

1-7   ye80b's 13-assertion shell harness
      (``docs/lanes/ye80b-teardown-orphan-rows/test_lane_teardown_near_miss.sh``),
      converted. It reproduces the 2026-09-05 ledger exactly: six rows owned by
      the DEAD lane under its LONG name ``drbf-compaction-notice-ab``, four
      owned by two LIVE lanes. Against the UNPATCHED script that harness
      reported ``RESULT: FAIL -- 3 expectation(s) unmet``.
8-13  Adoption must not widen what teardown can destroy. ``lane_teardown.sh``
      still has no ``sweep`` verb and no ``--all-owners`` escape hatch; 0rg's
      multi-owner sweep refusal still refuses; ``protected-untouched`` still
      protects; and the paths the shipped scripts PRINT now resolve to a file
      that exists.

TWO STANDARDS THIS FILE FOLLOWS DELIBERATELY

* Every "ran nothing" claim is proven by an OBSERVABLE side effect -- a state
  file that still exists, or a ``touch`` sentinel that does not -- never
  inferred from an exit code. The buggy path *prints a success message*, so an
  exit code alone cannot tell "refused" from "ran and failed"
  (``test_ten_lane_highway_infra_ledger.py``'s standard).
* The subprocess environment is built EXPLICITLY. ``model_performance-etuz`` was
  this same mistake in this same directory: a test that inherited the caller's
  environment was green in CI and red inside every highway lane (see 569c9b8).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# These are GNU/Linux shell scripts. lane_teardown.sh uses `flock` (util-linux;
# absent on stock macOS) and `chmod --reference` (a GNU coreutils flag with no
# BSD equivalent), and its sibling highway_status.sh documents the same
# constraint about `stat -c %Y` at highway_status.sh:53. Same precedent as the
# infra_ledger, orphan-rows and socket-default suites.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason=(
        "lane_teardown.sh is a GNU/Linux shell script: it uses `flock` "
        "(util-linux, absent on stock macOS) and `chmod --reference` (a GNU "
        "coreutils flag with no BSD equivalent). Requires Linux + bash."
    ),
)

SCRIPTS = Path(__file__).resolve().parents[1] / "amplifier_app_cli/data/skills/ten-lane-highway/scripts"
TEARDOWN = SCRIPTS / "lane_teardown.sh"
LEDGER = SCRIPTS / "infra_ledger.sh"
SKILL_MD = SCRIPTS.parent / "SKILL.md"

# The 2026-09-05 ledger. Six rows under the DEAD lane's LONG name, four under
# two LIVE lanes -- a live lane and a dead lane holding rows simultaneously is
# the shape that proves protection was not widened.
DEAD_LANE = "drbf-compaction-notice-ab"
DEAD_IDS = [f"val-drbf-{s}" for s in ("a", "b", "c", "a2", "b2", "c2")]
LIVE_ROWS = {
    "val-vbs-a": "vbs-cache-residency",
    "val-vbs-b": "vbs-cache-residency",
    "val-otr-a": "otr-armb-medium-root",
    "val-otr-b": "otr-armb-medium-root",
}

_STUB = """#!/usr/bin/env bash
# A container exists iff $DT_STATE/<id> exists. Every destroy is OBSERVABLE.
verb=${1:-}; id=${2:-}
case "$verb" in
  status)  [ -f "$DT_STATE/$id" ] && { echo "running"; exit 0; }
           echo "Environment '$id' not found" >&2; exit 1 ;;
  destroy) rm -f "$DT_STATE/$id"; echo "destroyed $id"; exit 0 ;;
  *) echo "unknown verb $verb" >&2; exit 2 ;;
esac
"""


class Batch:
    """A throwaway batch dir plus the stubbed DTU CLI that backs its rows."""

    def __init__(self, root: Path) -> None:
        self.dir = root / "batch"
        self.dir.mkdir(parents=True)
        self.state = root / "state"
        self.state.mkdir()
        stub = root / "bin" / "dt-stub"
        stub.parent.mkdir()
        stub.write_text(_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.stub = stub
        (self.dir / "infra.tsv").write_text("", encoding="utf-8")
        (self.dir / "infra.owners.tsv").write_text("", encoding="utf-8")

    def add(self, ident: str, owner: str, *, destroy: str | None = None, alive: bool = True) -> None:
        destroy = destroy if destroy is not None else f"amplifier-digital-twin destroy {ident}"
        with (self.dir / "infra.tsv").open("a", encoding="utf-8") as fh:
            fh.write(f"2026-09-06T02:18:49Z\tdtu\t{ident}\topen\t{destroy}\n")
        with (self.dir / "infra.owners.tsv").open("a", encoding="utf-8") as fh:
            fh.write(f"2026-09-06T02:18:49Z\t{ident}\t{owner}\n")
        if alive:
            (self.state / ident).write_text("", encoding="utf-8")

    def incident(self) -> "Batch":
        """The exact 2026-09-05 state: one dead lane's six rows, two live lanes' four."""
        for ident in DEAD_IDS:
            self.add(ident, DEAD_LANE)
        for ident, owner in LIVE_ROWS.items():
            self.add(ident, owner)
        return self

    # -- observation ------------------------------------------------------
    def statuses(self) -> dict[str, str]:
        out = {}
        for line in (self.dir / "infra.tsv").read_text(encoding="utf-8").splitlines():
            if line.strip():
                fields = line.split("\t")
                out[fields[2]] = fields[3]
        return out

    def open_ids(self) -> set[str]:
        return {k for k, v in self.statuses().items() if v == "open"}

    def containers(self) -> set[str]:
        return {p.name for p in self.state.iterdir()}

    def run(self, lane: str, *args: str) -> subprocess.CompletedProcess:
        """Run the real script with an EXPLICITLY built environment (etuz)."""
        env = {k: v for k, v in os.environ.items() if k not in ("AMPLIFIER_DT_CLI", "DT_STATE")}
        env["AMPLIFIER_DT_CLI"] = str(self.stub)
        env["DT_STATE"] = str(self.state)
        return subprocess.run(
            ["bash", str(TEARDOWN), str(self.dir), lane, *args],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )


@pytest.fixture
def batch(tmp_path: Path) -> Batch:
    return Batch(tmp_path).incident()


# ---------------------------------------------------------------------------
# 1-3. The incident: a NEAR-MISS lane name must fail loudly.
# ---------------------------------------------------------------------------


def test_near_miss_lane_name_refuses_instead_of_reporting_success(batch: Batch) -> None:
    """2026-09-05, mid-recovery, with six containers still billing.

    ``lane_teardown.sh <batch> drbf teardown --yes`` printed
    "TEARDOWN: lane 'drbf' owns no open rows - nothing to do" and exited 0 while
    SIX rows were open under ``drbf-compaction-notice-ab``. The ids are exactly
    what inference WOULD have selected for lane ``drbf``, but classify()
    consults claims first and those rows carry a claim under the LONG name, so
    every candidate landed in PROTECTED and an empty selection printed success.
    """
    proc = batch.run("drbf", "teardown", "--yes")

    assert proc.returncode == 4, f"expected the near-miss refusal (exit 4), got {proc.returncode}: {proc.stdout}"


def test_near_miss_refusal_names_the_candidate_lane_and_its_open_row_count(batch: Batch) -> None:
    """A refusal an operator cannot act on is barely better than the bug.

    The candidate must appear on its own ``candidate:`` line WITH the count.
    The UNPATCHED script also prints ``drbf-compaction-notice-ab`` -- in its
    PROTECTED listing, immediately before exiting 0 -- so a bare name match
    would pass against the very bug under test.
    """
    proc = batch.run("drbf", "teardown", "--yes")

    line = next(
        (ln for ln in (proc.stdout + proc.stderr).splitlines() if "candidate:" in ln and DEAD_LANE in ln),
        None,
    )
    assert line is not None, f"no candidate line naming {DEAD_LANE}:\n{proc.stdout}\n{proc.stderr}"
    assert "6" in line, f"candidate line omits the open-row count: {line!r}"


def test_the_near_miss_refusal_is_inert(batch: Batch) -> None:
    """Refusing must destroy nothing and flip no row -- observed, not inferred."""
    batch.run("drbf", "teardown", "--yes")

    assert len(batch.open_ids()) == 10, "the refusal flipped a row"
    assert len(batch.containers()) == 10, "the refusal destroyed a container"


# ---------------------------------------------------------------------------
# 4-6. The EXACT lane name still works, and only on its own rows.
# ---------------------------------------------------------------------------


def test_the_exact_lane_name_tears_down_exactly_its_own_six_rows(batch: Batch) -> None:
    """The transcript recorded that day: verified-gone=6, rows-flipped=6."""
    proc = batch.run(DEAD_LANE, "teardown", "--yes")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "verified-gone=6" in proc.stdout, proc.stdout
    assert "rows-flipped=6" in proc.stdout, proc.stdout
    assert batch.statuses() == {**{i: "swept" for i in DEAD_IDS}, **{i: "open" for i in LIVE_ROWS}}


def test_a_live_lanes_rows_are_never_touched(batch: Batch) -> None:
    """The shape that proves protection: a live lane and a dead lane, together.

    ``protected-untouched`` is the guarantee that adoption must not weaken. A
    teardown that reaches another lane's rows is far worse than the near-miss
    footgun this change fixes.
    """
    proc = batch.run(DEAD_LANE, "teardown", "--yes")

    assert "protected-untouched=4" in proc.stdout, proc.stdout
    assert batch.open_ids() == set(LIVE_ROWS), "wrong rows left open"
    # Observable, not inferred: the live lanes' containers are still there.
    assert batch.containers() == set(LIVE_ROWS)


def test_a_genuinely_idle_lane_still_exits_zero(batch: Batch) -> None:
    """The guard is a near-miss detector, not "any protected row is an error".

    A lane that provisioned nothing runs teardown as a matter of course. Failing
    it merely because some unrelated lane holds rows would make every clean lane
    look broken -- a fix that manufactures false failures is not a fix.
    """
    proc = batch.run("zzz", "teardown", "--yes")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert len(batch.open_ids()) == 10
    assert len(batch.containers()) == 10


# ---------------------------------------------------------------------------
# 7. The mirror near miss.
# ---------------------------------------------------------------------------


def test_the_mirror_near_miss_long_name_typed_rows_claimed_short(tmp_path: Path) -> None:
    """This batch's ledger genuinely carries both conventions.

    Short work-item ids AND long manifest names appear as owners, so the guard
    has to fire in both directions or it only half-works.
    """
    b = Batch(tmp_path)
    b.add("val-vbs-a", "vbs")
    b.add("val-vbs-b", "vbs")

    proc = b.run("vbs-cache-residency", "teardown", "--yes")

    assert proc.returncode == 4, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "candidate:" in proc.stderr, proc.stderr
    assert len(b.containers()) == 2, "the mirror refusal destroyed something"


# ---------------------------------------------------------------------------
# 8-10. Adoption must not widen what teardown can destroy.
# ---------------------------------------------------------------------------


def test_lane_teardown_has_no_sweep_verb(tmp_path: Path) -> None:
    """`sweep` is the MANAGER's batch-close verb and must stay unreachable here.

    Proven observably: the row's destroy_cmd is a ``touch``, so "ran nothing" is
    the absent sentinel rather than an exit code.
    """
    b = Batch(tmp_path)
    sentinel = tmp_path / "DESTROYED"
    b.add("val-x-a", "x", destroy=f"touch {sentinel}")

    proc = b.run("x", "sweep", "--yes")

    assert proc.returncode != 0, f"`sweep` was accepted: {proc.stdout}"
    assert "unknown command" in proc.stderr.lower(), proc.stderr
    assert not sentinel.exists(), "a destroy_cmd RAN under a `sweep` verb"
    assert b.open_ids() == {"val-x-a"}


def test_lane_teardown_offers_no_all_owners_escape_hatch(tmp_path: Path) -> None:
    """There is deliberately NO --all / --all-owners / --everything here.

    That is `sweep`, and `sweep` is the bug this script exists to avoid. An
    unknown option must be refused, not silently ignored -- a silently-ignored
    ``--all-owners`` would read to an operator as "it swept everything".
    """
    b = Batch(tmp_path)
    sentinel = tmp_path / "DESTROYED"
    b.add("val-x-a", "x", destroy=f"touch {sentinel}")

    for flag in ("--all-owners", "--all", "--everything"):
        proc = b.run("x", "teardown", "--yes", flag)
        assert proc.returncode != 0, f"{flag} was accepted: {proc.stdout}"
        assert "unknown option" in proc.stderr.lower(), proc.stderr
        assert not sentinel.exists(), f"{flag} ran a destroy_cmd"

    assert TEARDOWN.read_text(encoding="utf-8").count("--all-owners") == 0


def test_the_multi_owner_sweep_refusal_still_refuses(tmp_path: Path) -> None:
    """0rg's guard, re-proven after adoption.

    Nothing about shipping the lane-scoped tool may make `sweep` easier to
    reach. Observable destroys again: both sentinels must be absent.
    """
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    sentinels = [tmp_path / "DESTROYED_A", tmp_path / "DESTROYED_B"]
    for ident, owner, sentinel in zip(("a-1", "b-1"), ("lane-a", "lane-b"), sentinels, strict=True):
        proc = subprocess.run(
            ["bash", str(LEDGER), str(batch_dir), "add", "dtu", ident, "touch", str(sentinel)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        with (batch_dir / "infra.owners.tsv").open("a", encoding="utf-8") as fh:
            fh.write(f"2026-09-06T00:00:00Z\t{ident}\t{owner}\n")

    proc = subprocess.run(
        ["bash", str(LEDGER), str(batch_dir), "sweep"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 3, f"expected refusal (exit 3), got {proc.returncode}: {proc.stdout}"
    for sentinel in sentinels:
        assert not sentinel.exists(), "a destroy_cmd RAN despite the refusal"
    assert "--all-owners" in proc.stderr


# ---------------------------------------------------------------------------
# 11-13. The documented recovery path must resolve to a file that exists.
# ---------------------------------------------------------------------------


def test_the_sweep_refusal_names_a_lane_teardown_path_that_actually_exists(tmp_path: Path) -> None:
    """THE defect, mechanized.

    The refusal used to print ``.amplifier/evaluation/tools/lane_teardown.sh``:
    another repo's path, to a file untracked even there. An operator hitting
    this refusal mid-incident followed it to nothing. So do not assert on the
    string -- take the path the script actually prints and stat it.
    """
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    for ident, owner in (("a-1", "lane-a"), ("b-1", "lane-b")):
        subprocess.run(
            ["bash", str(LEDGER), str(batch_dir), "add", "dtu", ident, "true"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        with (batch_dir / "infra.owners.tsv").open("a", encoding="utf-8") as fh:
            fh.write(f"2026-09-06T00:00:00Z\t{ident}\t{owner}\n")

    proc = subprocess.run(
        ["bash", str(LEDGER), str(batch_dir), "sweep"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    printed = [tok for line in proc.stderr.splitlines() for tok in line.split() if tok.endswith("lane_teardown.sh")]
    assert printed, f"the refusal no longer names the lane-scoped tool:\n{proc.stderr}"
    for token in printed:
        assert Path(token).is_file(), f"the refusal points at a file that does not exist: {token}"
        assert Path(token).resolve() == TEARDOWN.resolve(), f"the refusal points outside this repo: {token}"


def test_no_shipped_instruction_sends_an_operator_into_another_repo(tmp_path: Path) -> None:
    """The cross-repo path may survive as history, never as an instruction.

    A shell comment recording WHY the path changed is fine. A line the operator
    is meant to run, or a SKILL.md instruction, is the defect itself.
    """
    stale = ".amplifier/evaluation/tools/lane_teardown.sh"

    assert stale not in SKILL_MD.read_text(encoding="utf-8"), "SKILL.md still sends operators to the evals repo"

    for script in sorted(SCRIPTS.glob("*.sh")):
        for lineno, line in enumerate(script.read_text(encoding="utf-8").splitlines(), start=1):
            if stale in line:
                assert line.lstrip().startswith("#"), f"{script.name}:{lineno} is a live instruction: {line.strip()}"


def test_skill_md_and_status_report_name_the_shipped_script(tmp_path: Path) -> None:
    """A bare `lane_teardown.sh` is on no PATH; it must carry a resolvable path.

    SKILL.md resolves companion scripts as ``<skill_directory>/scripts/<name>``,
    and highway_status.sh's orphan-row reclaim hint derives its path from its own
    location so the printed command is copy-pasteable.
    """
    assert TEARDOWN.is_file(), "the skill still does not ship its own recovery path"
    assert os.access(TEARDOWN, os.X_OK), "the adopted script is not executable"

    text = SKILL_MD.read_text(encoding="utf-8")
    assert "<skill_directory>/scripts/lane_teardown.sh" in text
    assert "lane_teardown.sh BATCH_DIR LANE" in text, "SKILL.md's instrument table omits the tool"

    status = (SCRIPTS / "highway_status.sh").read_text(encoding="utf-8")
    hint = next(ln for ln in status.splitlines() if "lane_teardown.sh" in ln and "echo" in ln)
    assert "BASH_SOURCE" in hint, f"the reclaim hint prints a bare, unrunnable name: {hint.strip()}"


def test_reconcile_still_refuses_to_reclaim_a_live_row(batch: Batch) -> None:
    """reconcile runs NO destroy command at all, and never touches a live row.

    It is the repair path for rows whose container is already gone. If adoption
    had turned it into a second way to destroy things, that would widen exactly
    what this item must not widen.
    """
    proc = batch.run(DEAD_LANE, "reconcile", "--yes")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "REFUSES to reclaim a live row" in proc.stdout, proc.stdout
    assert len(batch.containers()) == 10, "reconcile destroyed a container"
    assert len(batch.open_ids()) == 10, "reconcile flipped a live row"
