"""Guards on the shipped ten-lane-highway ``infra_ledger.sh``.

Both behaviours asserted here were fixed by hand in an *installed* copy of this
skill (``~/.local/share/uv/tools/amplifier/.../data/skills/ten-lane-highway``)
and a routine ``amplifier`` tool update re-installed that directory and silently
reverted them (mtimes 2026-09-03 05:54-06:13). Nothing warned; the next symptom
would have been DTUs vanishing mid-measurement. The fixes only stay fixed if
they live in the shipped source -- which is this file's subject -- and only stay
*alive* if something re-runs them, which is this file's job.

The five cases below are the ones measured by hand before the revert:

1. a destroy for infrastructure already gone closes the row, exit 0,
   status ``swept:already-absent``
2. a REAL destroy failure exits non-zero and leaves the row ``open``
3. a genuine teardown still records plain ``swept``
4. a ledger whose open rows span >1 owner refuses with exit 3 **having run no
   destroy command at all**
5. ``--all-owners`` (the manager's batch-close override) proceeds

Case 4 uses an *observable* destroy command (``touch <sentinel>``) so "ran
nothing" is proven by the absent sentinel rather than inferred from an exit
code -- an exit code alone cannot distinguish "refused" from "ran and failed".
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# The script is POSIX shell (mktemp/awk/sed/grep, tab-delimited read loops) and
# is only ever invoked on the POSIX hosts that run a highway. Skipping at module
# level on Windows follows the precedent already set by the pty tests, which
# likewise exercise a mechanism Windows has no equivalent of.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="infra_ledger.sh is a POSIX shell script; requires bash",
)

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "amplifier_app_cli"
    / "data"
    / "skills"
    / "ten-lane-highway"
    / "scripts"
    / "infra_ledger.sh"
)


def run_ledger(batch_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke the shipped script exactly as a highway would."""
    return subprocess.run(
        ["bash", str(SCRIPT), str(batch_dir), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def add_row(batch_dir: Path, kind: str, ident: str, destroy: str) -> None:
    proc = run_ledger(batch_dir, "add", kind, ident, *destroy.split())
    assert proc.returncode == 0, proc.stderr


def own(batch_dir: Path, ident: str, lane: str) -> None:
    """Attribute a ledger row to a lane, as the lane-scoped teardown tool does."""
    with (batch_dir / "infra.owners.tsv").open("a", encoding="utf-8") as fh:
        fh.write(f"2026-09-03T00:00:00Z\t{ident}\t{lane}\n")


def rows(batch_dir: Path) -> dict[str, str]:
    """Map ledger id -> status."""
    out = {}
    for line in (batch_dir / "infra.tsv").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        out[fields[2]] = fields[3]
    return out


# ---------------------------------------------------------------------------
# The two guards must remain FINDABLE by marker, not merely present in spirit.
#
# The manager's stopgap drift check greps the installed tree for exactly these
# two strings and names which fix is missing when one is absent. If a later
# refactor renames them, that check goes quietly blind -- the precise failure
# mode this whole item exists to close.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("marker", "item"),
    [
        ("MULTI-LANE GUARD", "model_performance-0rg"),
        ("ALREADY_GONE_RE", "model_performance-bqu"),
    ],
)
def test_guard_markers_are_greppable(marker: str, item: str) -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert marker in text, f"drift-check marker '{marker}' ({item}) missing from shipped source"


# ---------------------------------------------------------------------------
# CASE 1-3: what a destroy_cmd's outcome does to its row.
# ---------------------------------------------------------------------------


def test_case1_already_gone_closes_row_as_already_absent(tmp_path: Path) -> None:
    """Already-gone is the DESIRED end state of a destroy, not a failure.

    Before this, infrastructure torn down by any other path left a row that
    could never be closed: the destroy_cmd failed forever, so `sweep` never
    exited clean and "do not treat the highway as closed until sweep exits
    clean" became unsatisfiable.
    """
    add_row(tmp_path, "dtu", "gone-1", "bash -c 'echo environment not found >&2; exit 1'")
    own(tmp_path, "gone-1", "lane-a")

    proc = run_ledger(tmp_path, "sweep")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    # Distinct from a teardown this sweep actually performed.
    assert rows(tmp_path)["gone-1"] == "swept:already-absent"


def test_case2_real_failure_exits_nonzero_and_leaves_row_open(tmp_path: Path) -> None:
    """A REAL failure must stay loud.

    "Already gone" is deliberately NOT a blanket exit-code amnesty: that would
    destroy the signal that a teardown genuinely failed, which is the entire
    reason sweep checks rc at all (Rule 14 -- nothing the highway stands up
    should outlive it).
    """
    add_row(tmp_path, "dtu", "broken-1", "bash -c 'echo permission denied >&2; exit 1'")
    own(tmp_path, "broken-1", "lane-a")

    proc = run_ledger(tmp_path, "sweep")

    assert proc.returncode != 0, f"a real teardown failure exited 0: {proc.stdout}"
    assert rows(tmp_path)["broken-1"] == "open"


def test_case3_genuine_teardown_records_swept(tmp_path: Path) -> None:
    sentinel = tmp_path / "DESTROYED"
    add_row(tmp_path, "dtu", "live-1", f"touch {sentinel}")
    own(tmp_path, "live-1", "lane-a")

    proc = run_ledger(tmp_path, "sweep")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert sentinel.exists(), "the destroy_cmd did not run"
    assert rows(tmp_path)["live-1"] == "swept"


def test_case3_mixed_outcomes_are_recorded_distinctly(tmp_path: Path) -> None:
    """The two closed statuses must never collapse into one another."""
    sentinel = tmp_path / "DESTROYED"
    add_row(tmp_path, "dtu", "live-1", f"touch {sentinel}")
    add_row(tmp_path, "dtu", "gone-1", "bash -c 'echo no such container >&2; exit 1'")
    own(tmp_path, "live-1", "lane-a")
    own(tmp_path, "gone-1", "lane-a")

    proc = run_ledger(tmp_path, "sweep")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert rows(tmp_path) == {"live-1": "swept", "gone-1": "swept:already-absent"}


# ---------------------------------------------------------------------------
# CASE 4-5: who is allowed to run the batch-close verb.
# ---------------------------------------------------------------------------


def test_case4_multi_owner_sweep_refuses_and_runs_nothing(tmp_path: Path) -> None:
    """The DTU-protection guard.

    `sweep` runs EVERY open row's destroy command, so one lane calling it
    destroys every other lane's live infrastructure. On 2026-09-02 a single
    foreign sweep took lane l1's three DTUs and lane 161's three, 35 minutes
    into their measurements.

    The sentinels are the point: an exit code alone cannot tell "refused before
    doing anything" apart from "ran the destroys and then failed".
    """
    sentinel_a = tmp_path / "DESTROYED_A"
    sentinel_b = tmp_path / "DESTROYED_B"
    add_row(tmp_path, "dtu", "a-1", f"touch {sentinel_a}")
    add_row(tmp_path, "dtu", "b-1", f"touch {sentinel_b}")
    own(tmp_path, "a-1", "lane-a")
    own(tmp_path, "b-1", "lane-b")

    proc = run_ledger(tmp_path, "sweep")

    assert proc.returncode == 3, f"expected refusal (exit 3), got {proc.returncode}: {proc.stdout}"
    assert not sentinel_a.exists(), "a destroy_cmd RAN despite the refusal"
    assert not sentinel_b.exists(), "a destroy_cmd RAN despite the refusal"
    assert rows(tmp_path) == {"a-1": "open", "b-1": "open"}
    # A bare refusal is not actionable at the moment it fires; it must name the
    # override and the lane-scoped alternative.
    assert "--all-owners" in proc.stderr
    assert "lane_teardown.sh" in proc.stderr


def test_case4_unattributed_rows_also_refuse(tmp_path: Path) -> None:
    """A row nobody claimed cannot be shown to be safe to destroy."""
    sentinel = tmp_path / "DESTROYED"
    add_row(tmp_path, "dtu", "orphan-1", f"touch {sentinel}")
    # No infra.owners.tsv entry at all.

    proc = run_ledger(tmp_path, "sweep")

    assert proc.returncode == 3
    assert not sentinel.exists(), "an unattributed row was destroyed"
    assert rows(tmp_path)["orphan-1"] == "open"


def test_case5_all_owners_proceeds(tmp_path: Path) -> None:
    """The manager's batch-close override.

    This case is load-bearing in the other direction: a guard that deadlocks
    the documented close is a regression, not a fix. Phase 7 cannot close until
    sweep exits clean, so the override has to work.
    """
    sentinel_a = tmp_path / "DESTROYED_A"
    sentinel_b = tmp_path / "DESTROYED_B"
    add_row(tmp_path, "dtu", "a-1", f"touch {sentinel_a}")
    add_row(tmp_path, "dtu", "b-1", f"touch {sentinel_b}")
    own(tmp_path, "a-1", "lane-a")
    own(tmp_path, "b-1", "lane-b")

    proc = run_ledger(tmp_path, "sweep", "--all-owners")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert sentinel_a.exists() and sentinel_b.exists()
    assert rows(tmp_path) == {"a-1": "swept", "b-1": "swept"}


def test_single_owner_sweep_is_allowed_without_the_flag(tmp_path: Path) -> None:
    """The guard fires on ambiguity, not on sweeping as such."""
    sentinel = tmp_path / "DESTROYED"
    add_row(tmp_path, "dtu", "a-1", f"touch {sentinel}")
    own(tmp_path, "a-1", "lane-a")

    proc = run_ledger(tmp_path, "sweep")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert sentinel.exists()


def test_sweep_is_idempotent(tmp_path: Path) -> None:
    """Re-sweeping a fully-swept ledger runs nothing and exits 0.

    Closed rows -- including ``swept:already-absent`` ones -- must not be
    re-attempted, or the already-absent fix would merely move the deadlock.
    """
    sentinel = tmp_path / "DESTROYED"
    add_row(tmp_path, "dtu", "live-1", f"touch {sentinel}")
    add_row(tmp_path, "dtu", "gone-1", "bash -c 'echo does not exist >&2; exit 1'")
    own(tmp_path, "live-1", "lane-a")
    own(tmp_path, "gone-1", "lane-a")

    assert run_ledger(tmp_path, "sweep").returncode == 0
    sentinel.unlink()

    proc = run_ledger(tmp_path, "sweep")

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert not sentinel.exists(), "a already-closed row's destroy_cmd was re-run"
    assert rows(tmp_path) == {"live-1": "swept", "gone-1": "swept:already-absent"}


def test_skill_md_documents_the_manager_override(tmp_path: Path) -> None:
    """0rg's own acceptance required the guard and its docs to land together.

    Without ``--all-owners`` in the close instructions, the guard the manager
    must pass through is undocumented at exactly the moment it fires.
    """
    skill_md = SCRIPT.parent.parent / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    assert "--all-owners" in text
    assert "lane_teardown.sh" in text
    # Every close instruction names the override, not a bare `sweep`.
    close_lines = [
        line
        for line in text.splitlines()
        if "infra_ledger.sh" in line and "sweep" in line and "add " not in line
    ]
    assert close_lines, "SKILL.md no longer documents sweep at all"
    for line in close_lines:
        assert "--all-owners" in line, f"close instruction omits the override: {line!r}"
