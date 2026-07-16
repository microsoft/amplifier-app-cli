"""Regenerate the readable snapshot goldens under ``tests/goldens/``.

Any change to user-visible rendering must add or update a golden in the same
commit (alongside ``docs/designs/tui-v3-cohesive.md`` for intentional
presentation changes); review golden diffs as UI diffs.

Usage:
    uv run python tests/regen_goldens.py          # dry run: show pending golden changes (exit 1 if any)
    uv run python tests/regen_goldens.py --write  # rewrite tests/goldens/**/*.txt

The script renders through the exact code paths the golden tests use (it
imports the test modules and calls their ``render_*`` helpers), normalizes via
``tests/helpers.normalize_for_golden``, and writes plain ``.txt`` files — no
source files are rewritten. Stale ``.txt`` files that no longer correspond to
a golden case are pruned on ``--write``.

After ``--write``, re-run the golden tests and eyeball the diff — a golden
change you did not intend is a regression, not a regen.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers import GOLDENS_DIR  # noqa: E402
from helpers import normalize_for_golden  # noqa: E402


def _load_test_module(path: Path) -> ModuleType:
    """Import a test module by path so we reuse its render helpers verbatim."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compute_goldens() -> dict[Path, str]:
    """Render every golden case; returns {golden path: normalized text}."""
    transcript = _load_test_module(TESTS_DIR / "test_transcript_golden_widths.py")
    footer = _load_test_module(TESTS_DIR / "test_footer_golden_widths.py")

    goldens: dict[Path, str] = {}
    for width in transcript.GOLDEN_WIDTHS:
        for name in transcript.GOLDEN_MARKERS:
            path = transcript.GOLDEN_DIR / f"{name}_{width}.txt"
            goldens[path] = normalize_for_golden(transcript.render_block(name, width))
    for width in transcript.GALLERY_WIDTHS:
        path = transcript.GOLDEN_DIR / f"gallery_{width}.txt"
        goldens[path] = normalize_for_golden(transcript.render_gallery(width))
    for width in footer.IDLE_WIDTHS:
        path = footer.GOLDEN_DIR / f"idle_{width}.txt"
        goldens[path] = normalize_for_golden(footer.render_idle_footer(width))
    return goldens


def _stale_paths(goldens: dict[Path, str]) -> list[Path]:
    if not GOLDENS_DIR.exists():
        return []
    return sorted(p for p in GOLDENS_DIR.rglob("*.txt") if p not in goldens)


def _rel(path: Path) -> str:
    return str(path.relative_to(TESTS_DIR.parent))


def _print_pending(goldens: dict[Path, str], stale: list[Path]) -> int:
    """Pending-snapshot report: list goldens that would change, with diffs."""
    pending = 0
    for path in sorted(goldens):
        text = goldens[path]
        if not path.exists():
            print(f"new: {_rel(path)}")
            pending += 1
            continue
        on_disk = path.read_text(encoding="utf-8")
        if on_disk == text:
            continue
        pending += 1
        print(f"changed: {_rel(path)}")
        diff = difflib.unified_diff(
            on_disk.splitlines(),
            text.splitlines(),
            fromfile=f"a/{_rel(path)}",
            tofile=f"b/{_rel(path)}",
            lineterm="",
        )
        for line in diff:
            print(f"  {line}")
    for path in stale:
        print(f"stale (no matching case): {_rel(path)}")
        pending += 1
    if pending:
        print(f"\n{pending} golden(s) would change — review as a UI diff, then:")
        print("  uv run python tests/regen_goldens.py --write")
    else:
        print("all goldens up to date")
    return pending


def _write(goldens: dict[Path, str], stale: list[Path]) -> None:
    written = 0
    for path in sorted(goldens):
        text = goldens[path]
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {_rel(path)}")
        written += 1
    for path in stale:
        path.unlink()
        print(f"removed stale {_rel(path)}")
    print(f"{written} golden(s) written, {len(stale)} pruned, {len(goldens)} total")
    print(
        "re-run: uv run pytest tests/test_transcript_golden_widths.py"
        " tests/test_footer_golden_widths.py -q"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite the golden .txt files under tests/goldens/",
    )
    args = parser.parse_args(argv)

    goldens = compute_goldens()
    stale = _stale_paths(goldens)

    if args.write:
        _write(goldens, stale)
        return 0
    return 1 if _print_pending(goldens, stale) else 0


if __name__ == "__main__":
    sys.exit(main())
