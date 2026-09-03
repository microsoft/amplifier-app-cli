#!/usr/bin/env python3
"""Fail-before / pass-after probe for `model_performance-aof`.

Runs ONE experiment against whichever copy of `check_lane_artifact_paths.py`
you point it at, so the pre-fix and post-fix answers are produced by the same
script on the same synthetic repo and can be diffed line for line:

    python3 probe_artifact_root.py <path-to-evals-repo-root>

The synthetic repo reproduces amplifier-app-cli's shape at this lane's base ref
(35ab604): a checkout directory named `amplifier-app-cli`, `origin` pointing at
microsoft/amplifier-app-cli, and `ai_working/` tracked because an earlier lane's
DONE-NOTE put it there. Built from nothing rather than cloned, so the answer
does not depend on what any live repo happens to contain when this runs.

It prints three lines:

  RESOLVED     what a NEW app-cli lane is told its artifact root is
  GOAL-PATH    how a lane that followed its goal (docs/lanes/<lane>/) is graded
  LEGACY-PATH  how a lane that landed under ai_working/<lane>/ is graded

Pre-fix, RESOLVED is `ai_working/` and GOAL-PATH is VIOLATION -- a lane is
graded against a rule its own goal contradicts. Post-fix, RESOLVED is
`docs/lanes/`, GOAL-PATH is COMPLIANT, and LEGACY-PATH is still COMPLIANT
because landed artifacts are left exactly where they are.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

LANE_GOAL_PATH = "docs/lanes"
LANE_LEGACY_PATH = "ai_working"


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr}")
    return out.stdout.strip()


def build_repo(td: Path) -> Path:
    repo = td / "amplifier-app-cli"
    repo.mkdir(parents=True)
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "config", "user.email", "lane@example.invalid")
    git(repo, "config", "user.name", "lane")
    git(repo, "remote", "add", "origin", "https://github.com/microsoft/amplifier-app-cli.git")
    (repo / "README.md").write_text("scratch\n", encoding="utf-8")
    # The one fact that makes R2 fire in the real repo: `ai_working/` is tracked
    # at the base ref, and the only thing tracked there is a lane's own note.
    prior = repo / LANE_LEGACY_PATH / "3yc-timedout-session-resumable"
    prior.mkdir(parents=True)
    (prior / "DONE-NOTE.md").write_text("# DONE-NOTE\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "base")
    return repo


def lane_branch(repo: Path, lane: str, path: str) -> str:
    base = git(repo, "rev-parse", "main")
    git(repo, "checkout", "--quiet", "main")
    git(repo, "checkout", "--quiet", "-B", f"lane/{lane}")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# DONE-NOTE\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", f"{lane} note")
    return base


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    evals_root = Path(argv[1]).resolve()
    sys.path.insert(0, str(evals_root / "tools"))
    from check_lane_artifact_paths import evaluate, resolve_artifact_dir  # noqa: PLC0415

    print(f"checker      {evals_root / 'tools' / 'check_lane_artifact_paths.py'}")
    with tempfile.TemporaryDirectory() as td:
        repo = build_repo(Path(td))
        res = resolve_artifact_dir(repo, "main")
        print(f"RESOLVED     {res.dir_for('<lane>')}/   [{res.rule}]")

        for label, root in (("GOAL-PATH   ", LANE_GOAL_PATH), ("LEGACY-PATH ", LANE_LEGACY_PATH)):
            lane = "aof-example"
            base = lane_branch(repo, lane, f"{root}/{lane}/DONE-NOTE.md")
            r = evaluate(lane, repo, base, f"lane/{lane}")
            print(f"{label} {root}/{lane}/DONE-NOTE.md -> {r.status} (expected {r.expected})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
