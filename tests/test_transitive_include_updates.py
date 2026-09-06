"""Transitively-included bundles must be checked, listed, and refreshed.

The defect these cover: `amplifier update` and `amplifier bundle update`
enumerated update targets from *registered* sources only -- registry roots
plus app-bundle settings. A bundle that reaches the active closure solely
through another bundle's `includes:` was in neither place, so its cache was
never checked. Combined with foundation's loader never refreshing a cached
`@<branch>` source on its own (`sources/git.py:697-709`, cache hit returns
verbatim, no TTL, no fetch), that cache could sit arbitrarily far behind
upstream while both commands printed a green "All sources up to date".

Fixtures build REAL git repos and use `git+file://` URIs so the whole path --
cache-key derivation, `git ls-remote` comparison, re-clone -- runs for real
rather than against a mock that cannot reproduce the bug.
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "git+file:// URIs built from Windows drive-letter paths take a "
        "different resolution path; the behaviour under test is "
        "platform-independent and is covered on POSIX runners."
    ),
)


# ---------------------------------------------------------------------------
# Git fixtures
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git("add", "-A", cwd=repo)
    _git(
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "user.name=Test",
        "commit",
        "-q",
        "-m",
        message,
        cwd=repo,
    )
    return _git("rev-parse", "HEAD", cwd=repo)


def _make_repo(path: Path, bundle_text: str) -> str:
    """Create a git repo on branch ``main`` holding a one-file bundle."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "bundle.md").write_text(bundle_text, encoding="utf-8")
    _git("init", "-q", cwd=path)
    _git("checkout", "-q", "-b", "main", cwd=path)
    return _commit(path, "initial")


def _bundle_md(name: str, includes: list[str] | None = None) -> str:
    lines = ["---", "bundle:", f"  name: {name}", "  version: 1.0.0"]
    if includes:
        lines.append("includes:")
        lines.extend(f"  - bundle: {uri}" for uri in includes)
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _git_uri(path: Path, ref: str) -> str:
    return f"git+{path.as_uri()}@{ref}"


@pytest.fixture
def amplifier_home(tmp_path, monkeypatch) -> Path:
    """Isolate BOTH `AMPLIFIER_HOME` and `Path.home()`.

    `get_amplifier_home()` reads AMPLIFIER_HOME, but parts of discovery still
    read `Path.home() / ".amplifier"` directly. Setting only one leaves the
    other pointed at the developer's real installation.
    """
    home = tmp_path / "home"
    amp_home = home / ".amplifier"
    amp_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(amp_home))
    return amp_home


async def _prime_cache(uri: str, cache_dir: Path) -> Path:
    """Clone *uri* into the cache exactly the way a real session would."""
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    handler = GitSourceHandler()
    parsed = parse_uri(uri)
    await handler.resolve(parsed, cache_dir)
    return handler._get_cache_path(parsed, cache_dir)


def _cached_head(cache_path: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=cache_path)


def _direct_status(name: str, uri: str):
    from amplifier_foundation.updates import BundleStatus

    return BundleStatus(bundle_name=name, bundle_source=uri, sources=[])


# ---------------------------------------------------------------------------
# The core defect: an @main include that only a walk can find
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transitive_include_is_checked_and_names_its_parent(
    tmp_path, amplifier_home
):
    """B is registered, C is not -- C must still be checked, attributed to B."""
    from amplifier_app_cli.commands import update as update_module

    repo_c = tmp_path / "repo-c"
    _make_repo(repo_c, _bundle_md("c"))
    c_uri = _git_uri(repo_c, "main")

    repo_b = tmp_path / "repo-b"
    _make_repo(repo_b, _bundle_md("b", includes=[c_uri]))
    b_uri = _git_uri(repo_b, "main")

    cache_dir = amplifier_home / "cache"
    await _prime_cache(b_uri, cache_dir)
    c_cache = await _prime_cache(c_uri, cache_dir)
    stale_sha = _cached_head(c_cache)

    # Advance C's remote -- the cache is now behind and nothing says so.
    (repo_c / "NEW.md").write_text("moved on\n", encoding="utf-8")
    new_sha = _commit(repo_c, "advance main")
    assert new_sha != stale_sha

    results = await update_module._check_transitive_bundle_status(
        {b_uri}, {"b": _direct_status("b", b_uri)}
    )

    assert c_uri in results, (
        "C reaches the closure only through B's includes: -- it must not be "
        f"silently omitted. Got: {sorted(results)}"
    )
    entry = results[c_uri]
    assert entry.included_by == "b"
    assert entry.included_under == "b"
    assert entry.has_updates is True
    source = entry.sources[0]
    assert source.cached_commit == stale_sha
    assert source.remote_commit == new_sha


@pytest.mark.asyncio
async def test_refreshing_a_transitive_source_advances_its_cache(
    tmp_path, amplifier_home
):
    """Reporting is not enough -- the refresh must actually move the cache."""
    from amplifier_app_cli.utils.include_graph import refresh_transitive_source

    repo_c = tmp_path / "repo-c"
    _make_repo(repo_c, _bundle_md("c"))
    c_uri = _git_uri(repo_c, "main")

    cache_dir = amplifier_home / "cache"
    c_cache = await _prime_cache(c_uri, cache_dir)
    stale_sha = _cached_head(c_cache)

    (repo_c / "NEW.md").write_text("moved on\n", encoding="utf-8")
    new_sha = _commit(repo_c, "advance main")
    assert _cached_head(c_cache) == stale_sha, "cache must not move on its own"

    await refresh_transitive_source(c_uri, cache_dir=cache_dir)

    assert _cached_head(c_cache) == new_sha


@pytest.mark.asyncio
async def test_a_source_already_registered_directly_is_not_duplicated(
    tmp_path, amplifier_home
):
    """A repo the user registered is a direct row, never also a transitive one."""
    from amplifier_app_cli.commands import update as update_module

    repo_c = tmp_path / "repo-c"
    _make_repo(repo_c, _bundle_md("c"))
    c_uri = _git_uri(repo_c, "main")

    repo_b = tmp_path / "repo-b"
    _make_repo(repo_b, _bundle_md("b", includes=[c_uri]))
    b_uri = _git_uri(repo_b, "main")

    cache_dir = amplifier_home / "cache"
    await _prime_cache(b_uri, cache_dir)
    await _prime_cache(c_uri, cache_dir)

    results = await update_module._check_transitive_bundle_status(
        {b_uri, c_uri},
        {"b": _direct_status("b", b_uri), "c": _direct_status("c", c_uri)},
    )

    assert results == {}


# ---------------------------------------------------------------------------
# Pinned includes: reported, never moved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_sha_include_is_reported_pinned_and_not_moved(
    tmp_path, amplifier_home
):
    from amplifier_app_cli.commands import update as update_module

    repo_e = tmp_path / "repo-e"
    pinned_sha = _make_repo(repo_e, _bundle_md("e"))
    e_uri = _git_uri(repo_e, pinned_sha)

    repo_d = tmp_path / "repo-d"
    _make_repo(repo_d, _bundle_md("d", includes=[e_uri]))
    d_uri = _git_uri(repo_d, "main")

    cache_dir = amplifier_home / "cache"
    await _prime_cache(d_uri, cache_dir)
    e_cache = await _prime_cache(e_uri, cache_dir)

    # Upstream moves. A pinned ref must be unmoved by that, and must not be
    # dressed up as an available update.
    (repo_e / "NEW.md").write_text("moved on\n", encoding="utf-8")
    _commit(repo_e, "advance main")

    results = await update_module._check_transitive_bundle_status(
        {d_uri}, {"d": _direct_status("d", d_uri)}
    )

    assert e_uri in results, "a pinned include is still reported, just not refreshed"
    entry = results[e_uri]
    assert entry.is_pinned is True
    assert entry.has_updates is False
    assert _cached_head(e_cache) == pinned_sha


@pytest.mark.asyncio
async def test_pinned_tag_include_is_reported_pinned(tmp_path, amplifier_home):
    from amplifier_app_cli.commands import update as update_module

    repo_g = tmp_path / "repo-g"
    tagged_sha = _make_repo(repo_g, _bundle_md("g"))
    _git("tag", "v1.0.0", cwd=repo_g)
    g_uri = _git_uri(repo_g, "v1.0.0")

    repo_f = tmp_path / "repo-f"
    _make_repo(repo_f, _bundle_md("f", includes=[g_uri]))
    f_uri = _git_uri(repo_f, "main")

    cache_dir = amplifier_home / "cache"
    await _prime_cache(f_uri, cache_dir)
    g_cache = await _prime_cache(g_uri, cache_dir)

    (repo_g / "NEW.md").write_text("moved on\n", encoding="utf-8")
    _commit(repo_g, "advance main")

    results = await update_module._check_transitive_bundle_status(
        {f_uri}, {"f": _direct_status("f", f_uri)}
    )

    assert g_uri in results
    assert results[g_uri].is_pinned is True
    assert results[g_uri].has_updates is False
    assert _cached_head(g_cache) == tagged_sha


# ---------------------------------------------------------------------------
# Cycle safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_include_cycle_terminates(tmp_path, amplifier_home):
    """B includes C, C includes B. The walk must finish, not spin."""
    from amplifier_app_cli.commands import update as update_module

    repo_b = tmp_path / "repo-b"
    repo_c = tmp_path / "repo-c"
    b_uri = _git_uri(repo_b, "main")
    c_uri = _git_uri(repo_c, "main")

    _make_repo(repo_c, _bundle_md("c", includes=[b_uri]))
    _make_repo(repo_b, _bundle_md("b", includes=[c_uri]))

    cache_dir = amplifier_home / "cache"
    await _prime_cache(b_uri, cache_dir)
    await _prime_cache(c_uri, cache_dir)

    results = await update_module._check_transitive_bundle_status(
        {b_uri}, {"b": _direct_status("b", b_uri)}
    )

    # C is found once; B is the already-known root and never re-added.
    assert set(results) == {c_uri}


# ---------------------------------------------------------------------------
# The table has to SAY so
# ---------------------------------------------------------------------------


def test_report_lists_transitive_source_under_its_parent(monkeypatch):
    """A transitive row is indented and names its includer -- never bare."""
    import io

    from rich.console import Console

    from amplifier_app_cli.commands import update as update_module
    from amplifier_app_cli.commands.update import TransitiveBundleStatus
    from amplifier_app_cli.utils.source_status import UpdateReport
    from amplifier_foundation.sources.protocol import SourceStatus
    from amplifier_foundation.updates import BundleStatus

    parent_uri = "git+https://example.invalid/parent@main"
    child_uri = "git+https://example.invalid/child@main"

    parent = BundleStatus(
        bundle_name="parent",
        bundle_source=parent_uri,
        sources=[
            SourceStatus(
                source_uri=parent_uri,
                is_cached=True,
                has_update=False,
                cached_commit="aaaaaaaa11111111",
                remote_commit="aaaaaaaa11111111",
            )
        ],
    )
    child = TransitiveBundleStatus(
        bundle_name=child_uri,
        bundle_source=child_uri,
        sources=[
            SourceStatus(
                source_uri=child_uri,
                is_cached=True,
                has_update=True,
                cached_commit="c03a88b0000000",
                remote_commit="28588b90000000",
            )
        ],
        included_by="parent",
        included_under="parent",
    )

    buffer = io.StringIO()
    monkeypatch.setattr(update_module, "console", Console(file=buffer, width=200))
    monkeypatch.setattr(update_module, "_get_active_bundle_name", lambda: None)

    update_module._show_concise_report(
        UpdateReport(local_file_sources=[], cached_git_sources=[]),
        check_only=True,
        has_umbrella_updates=False,
        umbrella_deps=[],
        bundle_results={parent_uri: parent, child_uri: child},
    )

    output = buffer.getvalue()
    assert "↳" in output, output
    assert "(via parent)" in output, output
    assert "c03a88b" in output and "28588b9" in output, output
    # Indented child follows its parent row, not sorted away from it.
    assert output.index("parent") < output.index("(via parent)")


def test_pinned_transitive_row_shows_pinned_not_a_remote_sha(monkeypatch):
    import io

    from rich.console import Console

    from amplifier_app_cli.commands import update as update_module
    from amplifier_app_cli.commands.update import TransitiveBundleStatus
    from amplifier_app_cli.utils.source_status import UpdateReport
    from amplifier_foundation.sources.protocol import SourceStatus

    child_uri = "git+https://example.invalid/child@v1.2.3"
    child = TransitiveBundleStatus(
        bundle_name=child_uri,
        bundle_source=child_uri,
        sources=[
            SourceStatus(
                source_uri=child_uri,
                is_cached=True,
                has_update=False,
                cached_ref="v1.2.3",
                cached_commit="deadbeef00000000",
            )
        ],
        included_by="parent",
        included_under="parent",
        is_pinned=True,
    )

    buffer = io.StringIO()
    monkeypatch.setattr(update_module, "console", Console(file=buffer, width=200))
    monkeypatch.setattr(update_module, "_get_active_bundle_name", lambda: None)

    update_module._show_concise_report(
        UpdateReport(local_file_sources=[], cached_git_sources=[]),
        check_only=True,
        has_umbrella_updates=False,
        umbrella_deps=[],
        bundle_results={child_uri: child},
    )

    assert "pinned" in buffer.getvalue()


# ---------------------------------------------------------------------------
# The executor has to ACT on it
# ---------------------------------------------------------------------------


def test_update_refreshes_transitive_rows_via_the_git_handler(monkeypatch):
    """`amplifier update` must refresh a transitive row, not skip or reload it."""
    from unittest.mock import patch

    from click.testing import CliRunner

    from amplifier_app_cli.commands.update import TransitiveBundleStatus
    from amplifier_app_cli.commands.update import update
    from amplifier_app_cli.utils.source_status import UpdateReport
    from amplifier_app_cli.utils.update_executor import ExecutionResult
    from amplifier_foundation.sources.protocol import SourceStatus

    child_uri = "git+https://example.invalid/child@main"
    child = TransitiveBundleStatus(
        bundle_name=child_uri,
        bundle_source=child_uri,
        sources=[
            SourceStatus(
                source_uri=child_uri,
                is_cached=True,
                has_update=True,
                cached_commit="c03a88b0000000",
                remote_commit="28588b90000000",
            )
        ],
        included_by="foundation",
        included_under="foundation",
    )

    refreshed: list[str] = []

    async def fake_refresh(uri, *, cache_dir):
        refreshed.append(uri)

    async def fake_check_all_sources(**kwargs):
        return UpdateReport(local_file_sources=[], cached_git_sources=[])

    async def fake_check_all_bundle_status():
        return {child_uri: child}

    async def fake_execute_updates(
        report, umbrella_info=None, progress_callback=None, force=False
    ):
        return ExecutionResult(success=True, updated=[], messages=[])

    with (
        patch(
            "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
            return_value=None,
        ),
        patch(
            "amplifier_app_cli.commands.update.check_all_sources",
            side_effect=fake_check_all_sources,
        ),
        patch(
            "amplifier_app_cli.commands.update._check_all_bundle_status",
            side_effect=fake_check_all_bundle_status,
        ),
        patch(
            "amplifier_app_cli.utils.include_graph.refresh_transitive_source",
            side_effect=fake_refresh,
        ),
        patch(
            "amplifier_app_cli.commands.update._refresh_skills_cache",
            return_value=None,
        ),
        patch(
            "amplifier_app_cli.commands.update.save_update_last_check",
            return_value=None,
        ),
    ):
        result = CliRunner().invoke(update, ["--yes"])

    assert result.exit_code == 0, result.output
    assert refreshed == [child_uri], (
        "a transitive row with an available update must be refreshed; "
        f"refreshed={refreshed} output={result.output}"
    )


def test_all_sources_up_to_date_is_not_printed_over_a_stale_transitive_cache(
    monkeypatch,
):
    """The green line is a claim about the whole closure, includes and all."""
    from unittest.mock import patch

    from click.testing import CliRunner

    from amplifier_app_cli.commands.update import TransitiveBundleStatus
    from amplifier_app_cli.commands.update import update
    from amplifier_app_cli.utils.source_status import UpdateReport
    from amplifier_app_cli.utils.update_executor import ExecutionResult
    from amplifier_foundation.sources.protocol import SourceStatus

    child_uri = "git+https://example.invalid/child@main"
    child = TransitiveBundleStatus(
        bundle_name=child_uri,
        bundle_source=child_uri,
        sources=[
            SourceStatus(
                source_uri=child_uri,
                is_cached=True,
                has_update=True,
                cached_commit="c03a88b0000000",
                remote_commit="28588b90000000",
            )
        ],
        included_by="foundation",
        included_under="foundation",
    )

    async def fake_check_all_sources(**kwargs):
        return UpdateReport(local_file_sources=[], cached_git_sources=[])

    async def fake_check_all_bundle_status():
        return {child_uri: child}

    async def fake_execute_updates(
        report, umbrella_info=None, progress_callback=None, force=False
    ):
        return ExecutionResult(success=True, updated=[], messages=[])

    async def fake_refresh(uri, *, cache_dir):
        return None

    with (
        patch(
            "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
            return_value=None,
        ),
        patch(
            "amplifier_app_cli.commands.update.check_all_sources",
            side_effect=fake_check_all_sources,
        ),
        patch(
            "amplifier_app_cli.commands.update._check_all_bundle_status",
            side_effect=fake_check_all_bundle_status,
        ),
        patch(
            "amplifier_app_cli.utils.include_graph.refresh_transitive_source",
            side_effect=fake_refresh,
        ),
        patch(
            "amplifier_app_cli.commands.update._refresh_skills_cache",
            return_value=None,
        ),
        patch(
            "amplifier_app_cli.commands.update.save_update_last_check",
            return_value=None,
        ),
    ):
        result = CliRunner().invoke(update, ["--check-only"])

    assert "All sources up to date" not in result.output, result.output


# ---------------------------------------------------------------------------
# End-to-end wiring: the walk must actually be reachable from the real
# enumeration, not just callable on its own.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_all_bundle_status_includes_transitive_sources(
    tmp_path, amplifier_home, monkeypatch
):
    """`_check_all_bundle_status()` -- the enumeration `amplifier update` calls."""
    import json
    from types import SimpleNamespace

    from amplifier_app_cli.commands import update as update_module

    repo_c = tmp_path / "repo-c"
    _make_repo(repo_c, _bundle_md("c"))
    c_uri = _git_uri(repo_c, "main")

    repo_b = tmp_path / "repo-b"
    _make_repo(repo_b, _bundle_md("b", includes=[c_uri]))
    b_uri = _git_uri(repo_b, "main")

    cache_dir = amplifier_home / "cache"
    await _prime_cache(b_uri, cache_dir)
    c_cache = await _prime_cache(c_uri, cache_dir)
    stale_sha = _cached_head(c_cache)

    (repo_c / "NEW.md").write_text("moved on\n", encoding="utf-8")
    new_sha = _commit(repo_c, "advance main")

    (amplifier_home / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "bundles": {
                    "b": {
                        "uri": b_uri,
                        "name": "b",
                        "version": "1.0.0",
                        "is_root": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # Only "b" is registered. Left unstubbed, discovery would also fold in
    # every WELL_KNOWN_BUNDLE and hit the network from a unit test.
    monkeypatch.setattr(
        update_module,
        "AppBundleDiscovery",
        lambda *a, **k: SimpleNamespace(list_cached_root_bundles=lambda: ["b"]),
    )
    monkeypatch.setattr(
        update_module, "AppSettings", lambda: SimpleNamespace(get_app_bundles=list)
    )

    results = await update_module._check_all_bundle_status()

    assert "b" in results
    assert c_uri in results, (
        "the include walk is not wired into the enumeration -- this is the "
        f"exact silent omission the fix exists for. Got: {sorted(results)}"
    )
    assert results[c_uri].included_by == "b"
    assert results[c_uri].sources[0].cached_commit == stale_sha
    assert results[c_uri].sources[0].remote_commit == new_sha
