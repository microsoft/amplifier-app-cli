"""Regression coverage for Foundation-keyed module cache refreshes."""

from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from amplifier_app_cli.utils import module_cache
from amplifier_app_cli.utils.module_cache import CachedModuleInfo
from amplifier_app_cli.utils.source_status import CachedGitStatus
from amplifier_app_cli.utils.update_executor import execute_selective_module_update


def _git(*args: str, cwd: Path) -> str:
    """Run bounded fixture-only git commands without global configuration."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _make_remote(
    tmp_path: Path,
    repo_name: str,
    *,
    suffix: str,
    bundle_name: str | None = None,
    entry_point: str | None = None,
    marker: str = "initial",
) -> tuple[Path, Path]:
    """Create a local bare remote and a main worktree with independent identity."""
    worktree = tmp_path / f"work-{repo_name}-{suffix or 'no-suffix'}"
    remote = tmp_path / f"{repo_name}{suffix}"
    worktree.mkdir()
    remote.mkdir()

    _git("init", "-q", "-b", "main", cwd=worktree)
    _git("config", "user.name", "Module Cache Test", cwd=worktree)
    _git("config", "user.email", "module-cache@example.invalid", cwd=worktree)
    _git("init", "--bare", "-q", cwd=remote)

    if bundle_name:
        (worktree / "bundle.md").write_text(
            "---\n"
            "bundle:\n"
            f"  name: {bundle_name}\n"
            "  version: 1.0.0\n"
            "---\n",
            encoding="utf-8",
        )
    if entry_point:
        (worktree / "pyproject.toml").write_text(
            "[project]\n"
            f'name = "{repo_name}"\n'
            'version = "0.0.1"\n'
            '\n[project.entry-points."amplifier.modules"]\n'
            f'"{entry_point}" = "example:mount"\n',
            encoding="utf-8",
        )

    (worktree / "marker.txt").write_text(marker, encoding="utf-8")
    _commit(worktree, "initial")
    _git("remote", "add", "origin", remote.as_uri(), cwd=worktree)
    _git("push", "-q", "-u", "origin", "main", cwd=worktree)
    return worktree, remote


def _push_marker(worktree: Path, marker: str, *, branch: str = "main") -> None:
    (worktree / "marker.txt").write_text(marker, encoding="utf-8")
    _commit(worktree, marker)
    _git("push", "-q", "origin", branch, cwd=worktree)


async def _seed_foundation_cache(uri: str, cache_dir: Path) -> Path:
    """Populate an isolated cache through Foundation's real git resolver."""
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    handler = GitSourceHandler()
    parsed = parse_uri(uri)
    resolved = await handler.resolve(parsed, cache_dir)
    return resolved.source_root


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    path = tmp_path / "cache"
    path.mkdir()
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", ".git"])
async def test_update_module_refreshes_bundle_by_source_identity(
    tmp_path: Path, cache_dir: Path, suffix: str, monkeypatch: pytest.MonkeyPatch
):
    """A declared bundle name need not match its repository name."""
    worktree, remote = _make_remote(
        tmp_path,
        "repository-name",
        suffix=suffix,
        bundle_name="declared-bundle-name",
    )
    url = remote.as_uri()
    cached_path = await _seed_foundation_cache(f"git+{url}@main", cache_dir)
    assert (cached_path / "marker.txt").read_text(encoding="utf-8") == "initial"

    _push_marker(worktree, "updated")
    monkeypatch.setattr(module_cache, "get_cache_dir", lambda: cache_dir)

    progress: list[tuple[str, str]] = []
    active_path = await module_cache.update_module(
        url,
        "main",
        progress_callback=lambda label, phase: progress.append((label, phase)),
    )

    assert (active_path / "marker.txt").read_text(encoding="utf-8") == "updated"
    assert progress == [
        ("repository-name", "clearing"),
        ("repository-name", "downloading"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repo_name", "entry_point"),
    [
        ("repository-name", "provider-declared-id"),
        ("amplifier-module-provider-example", "provider-example"),
    ],
)
async def test_update_module_refreshes_module_when_entry_point_differs_from_repo(
    tmp_path: Path,
    cache_dir: Path,
    repo_name: str,
    entry_point: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Module entry-point IDs, including legacy repository paths, are not cache IDs."""
    worktree, remote = _make_remote(
        tmp_path,
        repo_name,
        suffix=".git",
        entry_point=entry_point,
    )
    url = remote.as_uri()
    await _seed_foundation_cache(f"git+{url}@main", cache_dir)
    _push_marker(worktree, "updated")
    monkeypatch.setattr(module_cache, "get_cache_dir", lambda: cache_dir)

    active_path = await module_cache.update_module(url, "main")

    assert (active_path / "marker.txt").read_text(encoding="utf-8") == "updated"


@pytest.mark.asyncio
async def test_update_module_keeps_other_source_and_ref_caches(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Refreshing URL@main cannot remove same-name entries for other identities."""
    target_worktree, target_remote = _make_remote(
        tmp_path,
        "shared-bundle",
        suffix=".git",
        bundle_name="shared-bundle",
        marker="target-old",
    )
    _git("checkout", "-q", "-b", "other", cwd=target_worktree)
    _push_marker(target_worktree, "other-old", branch="other")
    _git("checkout", "-q", "main", cwd=target_worktree)

    _, other_remote = _make_remote(
        tmp_path,
        "different-repository",
        suffix=".git",
        bundle_name="shared-bundle",
        marker="other-repository-old",
    )
    target_url = target_remote.as_uri()
    other_ref_path = await _seed_foundation_cache(f"git+{target_url}@other", cache_dir)
    other_repo_path = await _seed_foundation_cache(
        f"git+{other_remote.as_uri()}@main", cache_dir
    )
    await _seed_foundation_cache(f"git+{target_url}@main", cache_dir)

    _push_marker(target_worktree, "target-new")
    monkeypatch.setattr(module_cache, "get_cache_dir", lambda: cache_dir)

    active_path = await module_cache.update_module(target_url, "main")

    assert (active_path / "marker.txt").read_text(encoding="utf-8") == "target-new"
    assert (other_ref_path / "marker.txt").read_text(encoding="utf-8") == "other-old"
    assert (other_repo_path / "marker.txt").read_text(
        encoding="utf-8"
    ) == "other-repository-old"


class _FailingHandler:
    async def update(self, parsed, cache_dir):
        raise RuntimeError("refresh failed")


@pytest.mark.asyncio
async def test_update_module_propagates_foundation_refresh_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The helper preserves the caller's existing failure-handling boundary."""
    monkeypatch.setattr("amplifier_foundation.sources.git.GitSourceHandler", _FailingHandler)
    monkeypatch.setattr(module_cache, "get_cache_dir", lambda: tmp_path / "cache")

    with pytest.raises(RuntimeError, match="refresh failed"):
        await module_cache.update_module("file:///missing", "main")


@pytest.mark.asyncio
async def test_selective_update_reports_handler_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Selective execution records a real refresh failure rather than success."""
    monkeypatch.setattr("amplifier_foundation.sources.git.GitSourceHandler", _FailingHandler)
    monkeypatch.setattr(module_cache, "get_cache_dir", lambda: tmp_path / "cache")
    status = CachedGitStatus(name="provider-display", url="file:///missing", ref="main")

    result = await execute_selective_module_update([status])

    assert not result.success
    assert result.failed == ["provider-display"]
    assert result.updated == []
    assert result.errors["provider-display"] == "refresh failed"


@pytest.mark.asyncio
async def test_selective_update_preserves_status_source_identity(
    monkeypatch: pytest.MonkeyPatch,
):
    """The selective executor passes the cached URL and ref to the refresh boundary."""
    seen: list[tuple[str, str]] = []

    async def fake_update_module(url: str, ref: str, progress_callback=None) -> Path:
        seen.append((url, ref))
        return Path("/isolated/cache")

    monkeypatch.setattr(module_cache, "update_module", fake_update_module)
    status = CachedGitStatus(
        name="provider-display",
        url="file:///repository",
        ref="release-branch",
    )

    result = await execute_selective_module_update([status])

    assert result.success
    assert result.updated == ["provider-display@release-branch"]
    assert seen == [("file:///repository", "release-branch")]


def test_module_update_shows_failure_not_success_when_handler_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The Click command reports a refresh error from the real update helper."""
    from importlib import import_module

    module_commands = import_module("amplifier_app_cli.commands.module")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    output = StringIO()
    cached = CachedModuleInfo(
        module_id="provider-display",
        module_type="provider",
        ref="main",
        sha="",
        url="file:///missing",
        is_mutable=True,
        cached_at="",
        cache_path=cache_dir,
    )
    monkeypatch.setattr(module_cache, "get_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(module_cache, "find_cached_module", lambda module_id: cached)
    monkeypatch.setattr("amplifier_foundation.sources.git.GitSourceHandler", _FailingHandler)
    monkeypatch.setattr(
        module_commands,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )

    result = CliRunner().invoke(module_commands.module, ["update", cached.module_id])

    rendered = output.getvalue()
    assert result.exit_code == 0
    assert "Failed to update provider-display" in rendered
    assert "Updated provider-display@main" not in rendered