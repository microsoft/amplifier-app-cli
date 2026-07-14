"""Bounded Git snapshots for measuring per-turn file and diff yield."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_MAX_FILES = 10_000
_MAX_UNTRACKED_READ_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class GitFileStat:
    path: str
    additions: int
    deletions: int


@dataclass(frozen=True, slots=True)
class GitTurnDelta:
    files: int
    additions: int
    deletions: int

    @property
    def diff_label(self) -> str:
        return f"+{self.additions}/−{self.deletions}"


@dataclass(frozen=True, slots=True)
class GitDiffSnapshot:
    available: bool
    files: tuple[GitFileStat, ...] = ()

    def delta_from(self, previous: GitDiffSnapshot) -> GitTurnDelta | None:
        if not self.available or not previous.available:
            return None
        before = {item.path: item for item in previous.files}
        after = {item.path: item for item in self.files}
        paths = {
            path
            for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        }
        additions = 0
        deletions = 0
        for path in paths:
            old = before.get(path, GitFileStat(path, 0, 0))
            new = after.get(path, GitFileStat(path, 0, 0))
            added_delta = new.additions - old.additions
            deleted_delta = new.deletions - old.deletions
            additions += max(0, added_delta) + max(0, -deleted_delta)
            deletions += max(0, deleted_delta) + max(0, -added_delta)
        return GitTurnDelta(len(paths), additions, deletions)


async def capture_git_diff(
    cwd: Path, *, timeout_seconds: float = 5.0
) -> GitDiffSnapshot:
    """Capture tracked and untracked line statistics without invoking a shell."""
    root = cwd.resolve()
    tracked = await _git_output(
        root,
        ("diff", "--numstat", "HEAD", "--", "."),
        timeout_seconds,
    )
    if tracked is None:
        return GitDiffSnapshot(False)
    untracked = await _git_output(
        root,
        ("ls-files", "--others", "--exclude-standard", "-z"),
        timeout_seconds,
    )
    if untracked is None:
        return GitDiffSnapshot(False)
    stats: dict[str, GitFileStat] = {}
    for line in tracked.decode("utf-8", errors="replace").splitlines()[:_MAX_FILES]:
        additions, separator, remainder = line.partition("\t")
        deletions, second_separator, path = remainder.partition("\t")
        if not separator or not second_separator or not path:
            continue
        stats[path] = GitFileStat(
            path,
            int(additions) if additions.isdigit() else 0,
            int(deletions) if deletions.isdigit() else 0,
        )
    for raw_path in untracked.split(b"\0")[:_MAX_FILES]:
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="replace")
        if path in stats:
            continue
        stats[path] = GitFileStat(path, _line_count(root, path), 0)
    return GitDiffSnapshot(
        True, tuple(sorted(stats.values(), key=lambda item: item.path))
    )


async def _git_output(
    cwd: Path, args: tuple[str, ...], timeout_seconds: float
) -> bytes | None:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except asyncio.TimeoutError:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        return None
    except OSError:
        return None
    if process.returncode != 0 or len(stdout) > _MAX_OUTPUT_BYTES:
        return None
    return stdout


def _line_count(root: Path, relative_path: str) -> int:
    try:
        candidate = (root / relative_path).resolve()
        candidate.relative_to(root)
        data = candidate.read_bytes()[: _MAX_UNTRACKED_READ_BYTES + 1]
    except (OSError, ValueError):
        return 0
    if len(data) > _MAX_UNTRACKED_READ_BYTES or b"\0" in data:
        return 0
    return data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))


__all__ = [
    "GitDiffSnapshot",
    "GitFileStat",
    "GitTurnDelta",
    "capture_git_diff",
]
