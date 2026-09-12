"""Regression coverage for update-reporting labels and summaries."""

from __future__ import annotations

from contextlib import ExitStack
import io
from pathlib import PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
import pytest
from rich.console import Console

from amplifier_app_cli.commands import update as update_module
from amplifier_app_cli.commands.update import _classify_source_status
from amplifier_app_cli.commands.update import _check_failure_reason
from amplifier_app_cli.commands.update import _revision_report_state
from amplifier_app_cli.commands.update import TransitiveBundleStatus
from amplifier_app_cli.commands.update import update
from amplifier_app_cli.utils.source_status import CachedGitStatus
from amplifier_app_cli.utils.source_status import LocalFileStatus
from amplifier_app_cli.utils.source_status import UpdateReport
from amplifier_app_cli.utils.update_executor import ExecutionResult
from amplifier_foundation.sources.protocol import SourceStatus
from amplifier_foundation.updates import BundleStatus


def _source(
    uri: str,
    *,
    is_cached: bool,
    has_update: bool | None,
    cached_commit: str | None = None,
    remote_commit: str | None = None,
    cached_ref: str | None = "main",
    error: str | None = None,
) -> SourceStatus:
    return SourceStatus(
        source_uri=uri,
        is_cached=is_cached,
        cached_ref=cached_ref,
        has_update=has_update,
        cached_commit=cached_commit,
        remote_commit=remote_commit,
        error=error,
    )


def _bundle(name: str, source: SourceStatus) -> BundleStatus:
    return BundleStatus(bundle_name=name, bundle_source=source.source_uri, sources=[source])


@pytest.mark.parametrize(
    ("has_update", "is_cached", "cached_commit", "remote_commit", "expected"),
    [
        (False, True, "a" * 40, "a" * 40, "current"),
        (False, True, "a" * 40, "b" * 40, "not_checked"),
        (False, True, None, "a" * 40, "not_checked"),
        (False, False, None, "a" * 40, "not_checked"),
        (None, True, "a" * 40, "a" * 40, "not_checked"),
        (None, True, "a" * 40, None, "not_checked"),
    ],
)
def test_source_status_current_requires_a_confirmed_equal_comparison(
    has_update, is_cached, cached_commit, remote_commit, expected
):
    source = _source(
        "git+https://example.invalid/source@main",
        is_cached=is_cached,
        has_update=has_update,
        cached_commit=cached_commit,
        remote_commit=remote_commit,
    )

    assert _classify_source_status(source) == expected


def test_file_sources_distinguish_local_packaged_missing_and_confirmed_remote(
    tmp_path, monkeypatch
):
    """Filesystem facts are visible without changing successful remote comparisons."""

    local_path = tmp_path / "local"
    local_path.mkdir()
    wheel_root = tmp_path / "site-packages" / "amplifier_app_cli" / "_bundle"
    wheel_root.mkdir(parents=True)
    packaged_path = wheel_root / "behaviors"
    packaged_path.mkdir()
    monkeypatch.setattr(update_module, "_app_cli_packaged_bundle_root", lambda: wheel_root)

    assert _classify_source_status(
        _source(f"file://{local_path}", is_cached=True, has_update=None)
    ) == "local"
    assert _classify_source_status(
        _source(f"file://{packaged_path}", is_cached=True, has_update=None)
    ) == "packaged"
    assert _classify_source_status(
        _source(f"file://{tmp_path / 'missing'}", is_cached=True, has_update=None)
    ) == "missing_path"
    assert _classify_source_status(
        _source(
            f"file://{local_path}",
            is_cached=True,
            has_update=False,
            cached_commit="a" * 40,
            remote_commit="a" * 40,
        )
    ) == "current"
    assert _classify_source_status(
        _source(
            f"file://{local_path}",
            is_cached=True,
            has_update=True,
            cached_commit="a" * 40,
            remote_commit="b" * 40,
        )
    ) == "update"


@pytest.mark.parametrize(
    ("uri", "windows_path", "posix_path"),
    [
        (
            "file:///C:/Program%20Files/Amplifier",
            PureWindowsPath("C:/Program Files/Amplifier"),
            "/C:/Program Files/Amplifier",
        ),
        (
            r"file://C:\Program%20Files\Amplifier",
            PureWindowsPath(r"C:\Program Files\Amplifier"),
            None,
        ),
        ("file://server/share/Amplifier", PureWindowsPath("//server/share/Amplifier"), None),
    ],
)
def test_file_uri_parser_accepts_standard_legacy_and_unc_windows_forms(
    uri, windows_path, posix_path
):
    """Windows file URI forms stay local only when interpreted as Windows paths."""

    assert PureWindowsPath(update_module._file_uri_path_value(uri, windows=True)) == windows_path
    assert update_module._file_uri_path_value(uri, windows=False) == posix_path


def test_file_uri_parser_accepts_localhost_and_rejects_remote_posix_authority():
    assert update_module._file_uri_path_value(
        "file://localhost/tmp/space%20name", windows=False
    ) == "/tmp/space name"
    assert update_module._file_uri_path_value(
        "file://remote-host/tmp/space%20name", windows=False
    ) is None


@pytest.mark.parametrize(
    "uri",
    [
        "file://user:token@server/share/bundle",
        "file://server:8443/share/bundle",
    ],
)
def test_file_uri_parser_rejects_windows_authorities_that_are_not_unc_hosts(uri):
    assert update_module._file_uri_path_value(uri, windows=True) is None


def test_file_uri_parser_rejects_windows_conversion_errors():
    assert update_module._file_uri_path_value(
        "file:///work/|malformed", windows=True
    ) is None


@pytest.mark.parametrize(
    "uri",
    [
        "file://",
        "http://",
        "https://",
        "git+https://",
        "zip+file://",
        "file://user:token@server/share/bundle",
        "file:///tmp/%00invalid",
    ],
)
def test_app_sources_require_usable_locations(uri):
    assert not update_module._is_valid_app_source(uri)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), "timeout"),
        (RuntimeError("HTTP 401 credential rejected"), "authentication"),
        (RuntimeError("HTTP 403 forbidden"), "permission"),
        (RuntimeError("HTTP 404 not found"), "not found"),
        (RuntimeError("HTTP 429 too many requests"), "rate limit"),
        (RuntimeError("DNS connection failed"), "network"),
        (RuntimeError("unexpected response"), "status check failed"),
    ],
)
def test_check_failure_reason_uses_only_safe_vocabulary(error, expected):
    assert _check_failure_reason(error) == expected


@pytest.mark.parametrize(
    ("local_sha", "remote_sha", "has_local_changes", "checked", "expected"),
    [
        ("a", "a", False, True, "current"),
        ("a", "b", False, True, "update"),
        ("a", "", False, True, "not_checked"),
        ("a", "unknown", False, True, "not_checked"),
        ("a", "b", False, False, "not_checked"),
        ("a", "b", True, False, "local_changes"),
    ],
)
def test_revision_report_state_requires_a_known_checked_comparison(
    local_sha, remote_sha, has_local_changes, checked, expected
):
    assert (
        _revision_report_state(
            local_sha,
            remote_sha,
            has_local_changes=has_local_changes,
            checked=checked,
        )
        == expected
    )


def _command_patches(report, bundle_results, *, details=None, execute_calls=None):
    """Patch only status/update boundaries around the real Click command."""

    async def fake_check_all_sources(**kwargs):
        return report

    async def fake_check_all_bundle_status():
        return bundle_results

    async def fake_pypi_check():
        return False

    async def fake_details(info):
        return details or []

    async def fake_execute_updates(*args, **kwargs):
        if execute_calls is not None:
            execute_calls.append((args, kwargs))
        return ExecutionResult(success=True, updated=[], messages=[])

    return (
        patch(
            "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
            return_value=SimpleNamespace(),
        ),
        patch(
            "amplifier_app_cli.utils.update_executor.check_pypi_packages_for_updates",
            side_effect=fake_pypi_check,
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
            "amplifier_app_cli.commands.update._get_umbrella_dependency_details",
            side_effect=fake_details,
        ),
        patch(
            "amplifier_app_cli.commands.update.execute_updates",
            side_effect=fake_execute_updates,
        ),
        patch("amplifier_app_cli.commands.update._refresh_skills_cache"),
        patch("amplifier_app_cli.commands.update.save_update_last_check"),
    )


def test_missing_bundle_caches_are_downloads_not_updates(monkeypatch):
    """A cache reset keeps lazy downloads, but calls the first fetch a download."""

    direct_uri = "git+https://example.invalid/direct@main"
    direct = _bundle(
        "direct",
        _source(
            direct_uri,
            is_cached=False,
            has_update=True,
            remote_commit="a" * 40,
        ),
    )
    transitives = {
        f"git+https://example.invalid/transitive-{number}@main": TransitiveBundleStatus(
            bundle_name=f"transitive-{number}",
            bundle_source=f"git+https://example.invalid/transitive-{number}@main",
            sources=[
                _source(
                    f"git+https://example.invalid/transitive-{number}@main",
                    is_cached=False,
                    has_update=True,
                    remote_commit=f"{number:x}" * 40,
                )
            ],
            included_by="direct",
            included_under="direct",
        )
        for number in range(1, 9)
    }
    current = _bundle(
        "cached-filesystem",
        _source(
            "git+https://example.invalid/current@main",
            is_cached=True,
            has_update=False,
            cached_commit="b" * 40,
            remote_commit="b" * 40,
        ),
    )
    unchecked = {
        f"local-{number}": _bundle(
            f"local-{number}",
            _source(
                f"file:///workspace/local-{number}",
                is_cached=True,
                has_update=None,
                cached_ref=None,
            ),
        )
        for number in range(1, 4)
    }
    bundle_results = {"direct": direct, **transitives, "cached-filesystem": current, **unchecked}
    report = UpdateReport(
        local_file_sources=[],
        cached_git_sources=[
            CachedGitStatus(
                name="current-module",
                cached_sha="c" * 7,
                remote_sha="c" * 7,
                has_update=False,
            )
        ],
    )
    package_details = [
        {
            "name": "amplifier-core (PyPI)",
            "local_sha": "1.0.0",
            "remote_sha": "1.0.0",
            "source_url": "https://pypi.org/project/amplifier-core/",
            "has_update": False,
            "is_local": False,
            "path": None,
            "has_changes": False,
            "display_type": "version",
        }
    ]

    direct_loads: list[str] = []
    direct_updates: list[str] = []
    refreshed: list[str] = []

    async def fake_load_bundle(uri, *, auto_include):
        direct_loads.append(uri)
        return SimpleNamespace(uri=uri)

    async def fake_update_bundle(bundle):
        direct_updates.append(bundle.uri)

    async def fake_refresh(uri, *, cache_dir):
        refreshed.append(uri)

    patches = _command_patches(report, bundle_results, details=package_details)
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        stack.enter_context(
            patch("amplifier_foundation.load_bundle", side_effect=fake_load_bundle)
        )
        stack.enter_context(
            patch("amplifier_foundation.update_bundle", side_effect=fake_update_bundle)
        )
        stack.enter_context(
            patch(
                "amplifier_app_cli.utils.include_graph.refresh_transitive_source",
                side_effect=fake_refresh,
            )
        )
        runner = CliRunner()
        checked = runner.invoke(update, ["--check-only"])
        declined = runner.invoke(update, input="n\n")
        assert not direct_loads
        assert not direct_updates
        assert not refreshed
        accepted = runner.invoke(update, ["--yes"])

    assert checked.exit_code == 0, checked.output
    assert "Download 9 bundles" in checked.output
    assert "Update 9 bundles" not in checked.output
    assert "Available actions:" in checked.output
    assert "Updates available:" not in checked.output
    assert "Download" in checked.output
    assert "Missing path" in checked.output
    assert "Current" in checked.output
    assert direct_loads == [direct_uri]
    assert direct_updates == [direct_uri]
    assert refreshed == list(transitives)
    assert "Downloaded bundle: direct" in accepted.output
    assert accepted.output.count("Downloaded bundle:") == 9
    assert declined.exit_code == 0, declined.output
    assert "Download 9 bundles" in declined.output
    assert "Update 9 bundles" not in declined.output
    assert accepted.exit_code == 0, accepted.output


def test_current_caches_with_unchecked_local_sources_are_not_all_current():
    """Unknown local sources prevent a green all-current summary without an action."""

    current = _bundle(
        "current",
        _source(
            "git+https://example.invalid/current@main",
            is_cached=True,
            has_update=False,
            cached_commit="a" * 40,
            remote_commit="a" * 40,
        ),
    )
    unchecked = {
        f"local-{number}": _bundle(
            f"local-{number}",
            _source(
                f"file:///workspace/local-{number}",
                is_cached=True,
                has_update=None,
                cached_ref=None,
            ),
        )
        for number in range(1, 4)
    }
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    execute_calls: list[tuple[tuple, dict]] = []
    patches = _command_patches(
        report, {"current": current, **unchecked}, execute_calls=execute_calls
    )
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        result = CliRunner().invoke(update, ["--check-only"])

    assert result.exit_code == 0, result.output
    assert "No confirmed updates; 3 sources could not be checked" in result.output
    assert "All sources up to date" not in result.output
    assert not execute_calls


def test_all_confirmed_current_sources_keep_the_green_summary():
    """The previous all-current summary remains for actual equal comparisons."""

    current = _bundle(
        "current",
        _source(
            "git+https://example.invalid/current@main",
            is_cached=True,
            has_update=False,
            cached_commit="a" * 40,
            remote_commit="a" * 40,
        ),
    )
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    patches = _command_patches(report, {"current": current})
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        result = CliRunner().invoke(update, ["--check-only"])

    assert result.exit_code == 0, result.output
    assert "All sources up to date" in result.output
    assert "No confirmed updates" not in result.output


def test_check_only_presentation_preserves_existing_registry_settings_and_cache(
    tmp_path, monkeypatch
):
    """The real checker renders configured sources without presentation writes.

    Foundation validates stale registry cache paths while constructing a
    registry.  This fixture deliberately provides an empty, valid registry,
    so the assertion covers this CLI's presentation and routing boundary
    rather than claiming all historical ``--check-only`` paths are write-free.
    """

    home = tmp_path / "amplifier-home"
    cache = home / "cache"
    configured_path = tmp_path / "configured bundle"
    configured_path.mkdir()
    home.mkdir()
    cache.mkdir()
    (home / "registry.json").write_bytes(b'{"bundles": {}}\n')
    (home / "settings.yaml").write_text(
        "\n".join(
            [
                "bundle:",
                "  app:",
                f"    - {configured_path.as_uri()}",
                "    - 'git+https://token@[broken/source'",
                "    - 7",
                "    - {private: value}",
                "    - 'file://'",
                "    - 'http://'",
                "    - 'https://'",
                "    - 'git+https://'",
                "    - 'zip+file://'",
                "    - 'file://userinfo:token@server/share/bundle'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (cache / "sentinel.bin").write_bytes(b"\x00unchanged-cache\xff")
    before = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "decoy-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "decoy-home"))
    output = io.StringIO()
    monkeypatch.setattr(update_module, "console", Console(file=output, width=200))

    async def fake_check_all_sources(**kwargs):
        return UpdateReport(local_file_sources=[], cached_git_sources=[])

    async def no_transitive_discovery(*args, **kwargs):
        return {}

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "amplifier_app_cli.commands.update.check_all_sources",
                side_effect=fake_check_all_sources,
            )
        )
        stack.enter_context(
            patch(
                "amplifier_app_cli.commands.update._check_transitive_bundle_status",
                side_effect=no_transitive_discovery,
            )
        )
        stack.enter_context(
            patch(
                "amplifier_app_cli.commands.update.AppBundleDiscovery",
                return_value=SimpleNamespace(list_cached_root_bundles=lambda: []),
            )
        )
        stack.enter_context(
            patch(
                "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
                return_value=None,
            )
        )
        result = CliRunner().invoke(update, ["--check-only"])

    after = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    assert result.exit_code == 0, result.output
    assert after == before
    assert "Check failed: invalid source" in output.getvalue()
    assert "token" not in output.getvalue()
    assert "private" not in output.getvalue()


def test_local_umbrella_dependency_prevents_a_false_all_current_summary():
    """A local umbrella dependency is neutral, not confirmation of freshness."""

    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    details = [
        {
            "name": "local-dependency",
            "local_sha": "a" * 40,
            "remote_sha": None,
            "source_url": "file:///workspace/local-dependency",
            "has_update": False,
            "is_local": True,
            "path": "/workspace/local-dependency",
            "has_changes": False,
        }
    ]
    patches = _command_patches(report, {}, details=details)
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        result = CliRunner().invoke(update, ["--check-only"])

    assert result.exit_code == 0, result.output
    assert "No confirmed updates; 1 source could not be checked" in result.output
    assert "All sources up to date" not in result.output


def test_empty_bundle_is_unchecked_and_does_not_run_or_claim_all_current():
    """An empty status has no confirmed comparison, nor an executable update."""

    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    execute_calls: list[tuple[tuple, dict]] = []
    empty = BundleStatus(bundle_name="empty", bundle_source="file:///workspace/empty")
    patches = _command_patches(report, {"empty": empty}, execute_calls=execute_calls)
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        result = CliRunner().invoke(update, ["--check-only"])
        verbose = CliRunner().invoke(update, ["--check-only", "--verbose"])

    assert result.exit_code == 0, result.output
    assert verbose.exit_code == 0, verbose.output
    assert "Not checked" in result.output
    assert "Bundle: empty" in verbose.output
    assert "Not checked" in verbose.output
    assert "No confirmed updates; 1 source could not be checked" in result.output
    assert "All sources up to date" not in result.output
    assert not execute_calls


@pytest.mark.parametrize("verbose", [False, True])
def test_local_umbrella_dependencies_use_plain_language_statuses(monkeypatch, verbose):
    """Clean local dependencies are unchecked; dirty ones report local changes."""

    details = [
        {
            "name": "clean-local",
            "local_sha": "a" * 40,
            "remote_sha": None,
            "source_url": "file:///workspace/clean-local",
            "has_update": False,
            "is_local": True,
            "path": "/workspace/clean-local",
            "has_changes": False,
        },
        {
            "name": "dirty-local",
            "local_sha": "b" * 40,
            "remote_sha": None,
            "source_url": "file:///workspace/dirty-local",
            "has_update": False,
            "is_local": True,
            "path": "/workspace/dirty-local",
            "has_changes": True,
        },
    ]
    buffer = io.StringIO()
    monkeypatch.setattr(
        update_module,
        "console",
        Console(file=buffer, force_terminal=True, color_system="standard", width=200),
    )
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])

    if verbose:
        update_module._show_verbose_report(report, True, umbrella_deps=details)
    else:
        update_module._show_concise_report(
            report, True, False, umbrella_deps=details
        )

    output = buffer.getvalue()
    assert "clean-local" in output
    assert "Not checked" in output
    assert "Local changes" in output
    assert "Legend:" not in output


@pytest.fixture
def _status_consistency_fixture():
    report = UpdateReport(
        local_file_sources=[
            LocalFileStatus(name="clean-local", local_sha="a" * 7),
            LocalFileStatus(
                name="dirty-local",
                local_sha="b" * 7,
                uncommitted_changes=True,
            ),
            LocalFileStatus(
                name="stale-local",
                local_sha="c" * 7,
                remote_sha="d" * 7,
                has_remote=True,
            ),
        ],
        cached_git_sources=[
            CachedGitStatus(name="current-module", cached_sha="e" * 7, remote_sha="e" * 7),
            CachedGitStatus(
                name="stale-module",
                cached_sha="f" * 7,
                remote_sha="0" * 7,
                has_update=False,
            ),
            CachedGitStatus(name="unknown-module", cached_sha="1" * 7, remote_sha="unknown"),
        ],
    )
    dependencies = [
        {
            "name": "current-package",
            "local_sha": "2" * 7,
            "remote_sha": "2" * 7,
            "source_url": "https://example.invalid/current",
            "has_update": False,
            "is_local": False,
            "path": None,
            "has_changes": False,
        },
        {
            "name": "stale-package",
            "local_sha": "3" * 7,
            "remote_sha": "4" * 7,
            "source_url": "https://example.invalid/stale",
            "has_update": False,
            "is_local": False,
            "path": None,
            "has_changes": False,
        },
        {
            "name": "unknown-package",
            "local_sha": "5" * 7,
            "remote_sha": "",
            "source_url": "https://example.invalid/unknown",
            "has_update": False,
            "is_local": False,
            "path": None,
            "has_changes": False,
        },
    ]
    bundles = {
        "current-bundle": _bundle(
            "current-bundle",
            _source(
                "git+https://example.invalid/current-bundle@main",
                is_cached=True,
                has_update=False,
                cached_commit="6" * 40,
                remote_commit="6" * 40,
            ),
        ),
        "missing-bundle": _bundle(
            "missing-bundle",
            _source(
                "git+https://example.invalid/missing-bundle@main",
                is_cached=False,
                has_update=True,
                remote_commit="7" * 40,
            ),
        ),
        "pinned-bundle": _bundle(
            "pinned-bundle",
            _source(
                "git+https://example.invalid/pinned-bundle@v1.0.0",
                is_cached=True,
                has_update=False,
                cached_ref="v1.0.0",
            ),
        ),
        "unknown-bundle": _bundle(
            "unknown-bundle",
            _source("file:///synthetic/unknown-bundle", is_cached=True, has_update=None),
        ),
    }
    return report, dependencies, bundles


@pytest.mark.parametrize("verbose", [False, True])
def test_update_reports_consistent_word_statuses_without_a_legend(
    _status_consistency_fixture, verbose
):
    """The real command renders source statuses as words without changing execution."""

    report, dependencies, bundles = _status_consistency_fixture
    execute_calls: list[tuple[tuple, dict]] = []
    patches = _command_patches(
        report,
        bundles,
        details=dependencies,
        execute_calls=execute_calls,
    )
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        runner = CliRunner()
        result = runner.invoke(
            update,
            ["--check-only", *(["--verbose"] if verbose else [])],
        )
        declined = runner.invoke(update, input="n\n")

    assert result.exit_code == 0, result.output
    assert declined.exit_code == 0, declined.output
    assert not execute_calls
    for label in ("Current", "Update", "Not checked", "Local changes", "Download", "Pinned"):
        assert label in result.output
    assert "Legend:" not in result.output
    if not verbose:
        assert result.output.count("Status") == 4
    for symbol in ("✓", "●", "◦", "?"):
        assert symbol not in result.output


def test_verbose_file_bundle_source_is_local_not_remote(monkeypatch):
    """A file URI is a local path in a verbose bundle source entry."""

    source = _source(
        "file:///workspace/local-bundle",
        is_cached=True,
        has_update=None,
        cached_commit="a" * 40,
        cached_ref=None,
    )
    bundle = _bundle("local-bundle", source)
    buffer = io.StringIO()
    monkeypatch.setattr(update_module, "console", Console(file=buffer, width=200))

    update_module._show_verbose_report(
        UpdateReport(local_file_sources=[], cached_git_sources=[]),
        True,
        bundle_results={"local-bundle": bundle},
    )

    output = buffer.getvalue()
    assert "Local:  aaaaaaa  file:///workspace/local-bundle" in output
    assert "Remote: file:///workspace/local-bundle" not in output


def test_mixed_bundle_states_render_the_same_plan_in_both_reports():
    """Classification is independent of foundation summaries and has no side effects."""

    dirty = _source(
        "file:///workspace/dirty",
        is_cached=True,
        has_update=True,
        cached_commit="1" * 40,
        remote_commit="2" * 40,
    )
    dirty._has_local_changes = True
    states = {
        "stale": _bundle(
            "stale",
            _source(
                "git+https://example.invalid/stale@main",
                is_cached=True,
                has_update=True,
                cached_commit="3" * 40,
                remote_commit="4" * 40,
            ),
        ),
        "missing": _bundle(
            "missing",
            _source(
                "git+https://example.invalid/missing@main",
                is_cached=False,
                has_update=True,
                remote_commit="5" * 40,
            ),
        ),
        "unreadable": _bundle(
            "unreadable",
            _source(
                "git+https://example.invalid/unreadable@main",
                is_cached=True,
                has_update=True,
                remote_commit="6" * 40,
            ),
        ),
        "unknown": _bundle(
            "unknown",
            _source("file:///workspace/unknown", is_cached=True, has_update=None),
        ),
        "dirty": _bundle("dirty", dirty),
        "pinned-cached": _bundle(
            "pinned-cached",
            _source(
                "git+https://example.invalid/pinned@v1.0.0",
                is_cached=True,
                has_update=False,
                cached_ref="v1.0.0",
            ),
        ),
        "pinned-missing": _bundle(
            "pinned-missing",
            _source(
                "git+https://example.invalid/pinned-missing@v1.0.0",
                is_cached=False,
                has_update=False,
                cached_ref="v1.0.0",
            ),
        ),
        "error": _bundle(
            "error",
            _source(
                "git+https://example.invalid/error@main",
                is_cached=True,
                has_update=True,
                cached_commit="7" * 40,
                remote_commit="8" * 40,
                error="remote failed",
            ),
        ),
    }
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    before = [name for name, status in states.items() if status.has_updates]
    patches = _command_patches(report, states)
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        concise = CliRunner().invoke(update, ["--check-only"])
        verbose = CliRunner().invoke(update, ["--check-only", "--verbose"])
    after = [name for name, status in states.items() if status.has_updates]

    assert concise.exit_code == 0, concise.output
    assert verbose.exit_code == 0, verbose.output
    assert before == after == ["stale", "missing", "unreadable", "dirty", "error"]
    for label in (
        "Update",
        "Download",
        "Refresh cache",
        "Missing path",
        "Pinned",
        "Check failed: status check failed",
    ):
        assert label in concise.output
        assert label in verbose.output
    assert "Pinned; not cached" in concise.output
    assert "Pinned; not cached" in verbose.output
    assert "Download 1 bundle" in concise.output
    assert "Refresh cache 1 bundle" in concise.output
    assert "Update 1 bundle" in concise.output


def test_mixed_actions_process_one_bundle_once_and_report_its_composition():
    """A selected bundle with multiple actions is not counted in exclusive buckets."""

    uri = "git+https://example.invalid/mixed@main"
    mixed = BundleStatus(
        bundle_name="mixed",
        bundle_source=uri,
        sources=[
            _source(
                uri,
                is_cached=True,
                has_update=False,
                cached_commit="a" * 40,
                remote_commit="a" * 40,
            ),
            _source(
                "git+https://example.invalid/missing-child@main",
                is_cached=False,
                has_update=True,
                remote_commit="b" * 40,
            ),
            _source(
                "git+https://example.invalid/stale-child@main",
                is_cached=True,
                has_update=True,
                cached_commit="c" * 40,
                remote_commit="d" * 40,
            ),
            _source(
                "file:///workspace/unchecked-child",
                is_cached=True,
                has_update=None,
                cached_ref=None,
            ),
        ],
    )
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    loads: list[str] = []
    updates: list[str] = []

    async def fake_load_bundle(source_uri, *, auto_include):
        loads.append(source_uri)
        return SimpleNamespace()

    async def fake_update_bundle(bundle):
        updates.append("called")

    patches = _command_patches(report, {"mixed": mixed})
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        stack.enter_context(
            patch("amplifier_foundation.load_bundle", side_effect=fake_load_bundle)
        )
        stack.enter_context(
            patch("amplifier_foundation.update_bundle", side_effect=fake_update_bundle)
        )
        checked = CliRunner().invoke(update, ["--check-only"])
        accepted = CliRunner().invoke(update, ["--yes", "--verbose"])

    assert checked.exit_code == 0, checked.output
    assert "Mixed actions (download + update)" in checked.output
    assert "Process 1 bundle with downloads and updates" in checked.output
    assert "Download 1 bundle" not in checked.output
    assert "Update 1 bundle" not in checked.output
    assert accepted.exit_code == 0, accepted.output
    assert "Missing path" in accepted.output
    assert "Processed bundle: mixed" in accepted.output
    assert loads == [uri]
    assert updates == ["called"]


def test_selected_neutral_bundle_states_are_confirmed_and_processed_once():
    """Legacy-selected local/error statuses are named honestly instead of omitted."""

    mixed_uri = "git+https://example.invalid/mixed@main"
    dirty_uri = "git+https://example.invalid/dirty@main"
    error_uri = "git+https://example.invalid/error@main"
    dirty_source = _source(
        dirty_uri,
        is_cached=True,
        has_update=True,
        cached_commit="a" * 40,
        remote_commit="b" * 40,
    )
    dirty_source._has_local_changes = True
    bundle_results = {
        "mixed": BundleStatus(
            bundle_name="mixed",
            bundle_source=mixed_uri,
            sources=[
                _source(
                    "git+https://example.invalid/missing@main",
                    is_cached=False,
                    has_update=True,
                    remote_commit="c" * 40,
                ),
                _source(
                    "git+https://example.invalid/stale@main",
                    is_cached=True,
                    has_update=True,
                    cached_commit="d" * 40,
                    remote_commit="e" * 40,
                ),
            ],
        ),
        "dirty": _bundle("dirty", dirty_source),
        "error": _bundle(
            "error",
            _source(
                error_uri,
                is_cached=True,
                has_update=True,
                cached_commit="f" * 40,
                remote_commit="0" * 40,
                error="remote unavailable",
            ),
        ),
    }
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    loads: list[str] = []

    async def fake_load_bundle(uri, *, auto_include):
        loads.append(uri)
        return SimpleNamespace()

    async def fake_update_bundle(bundle):
        return None

    patches = _command_patches(report, bundle_results)
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        stack.enter_context(
            patch("amplifier_foundation.load_bundle", side_effect=fake_load_bundle)
        )
        stack.enter_context(
            patch("amplifier_foundation.update_bundle", side_effect=fake_update_bundle)
        )
        result = CliRunner().invoke(update, input="\n")

    assert result.exit_code == 0, result.output
    assert "Process 1 bundle with downloads and updates" in result.output
    assert "Process 1 bundle with local changes" in result.output
    assert "Process 1 bundle with failed checks" in result.output
    assert loads == [mixed_uri, dirty_uri, error_uri]
    assert "Processed bundle: dirty" in result.output
    assert "Processed bundle: error" in result.output


def test_bundle_completion_reports_failed_operation_without_a_success_verb():
    """Failures say failed to <operation>; past-tense verbs are success-only."""

    download_uri = "git+https://example.invalid/download@main"
    update_uri = "git+https://example.invalid/update@main"
    bundle_results = {
        "download": _bundle(
            "download",
            _source(download_uri, is_cached=False, has_update=True, remote_commit="a" * 40),
        ),
        "update": _bundle(
            "update",
            _source(
                update_uri,
                is_cached=True,
                has_update=True,
                cached_commit="b" * 40,
                remote_commit="c" * 40,
            ),
        ),
    }
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])

    async def fake_load_bundle(uri, *, auto_include):
        if uri == download_uri:
            raise RuntimeError("cannot load download")
        if uri == update_uri:
            raise RuntimeError("cannot load update")
        return SimpleNamespace(uri=uri)

    async def fake_update_bundle(bundle):
        return None

    patches = _command_patches(report, bundle_results)
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        stack.enter_context(
            patch("amplifier_foundation.load_bundle", side_effect=fake_load_bundle)
        )
        stack.enter_context(
            patch("amplifier_foundation.update_bundle", side_effect=fake_update_bundle)
        )
        result = CliRunner().invoke(update, ["--yes"])

    assert result.exit_code == 0, result.output
    assert "Failed to download bundle: download: cannot load download" in result.output
    assert "Failed to update bundle: update: cannot load update" in result.output
    assert "Downloaded bundle: download" not in result.output
    assert "Updated bundle: update" not in result.output


def test_mixed_bundle_failure_reports_processing_not_a_successful_action():
    """A composite operation has no one success verb to reuse after failure."""

    uri = "git+https://example.invalid/mixed-failure@main"
    mixed = BundleStatus(
        bundle_name="mixed-failure",
        bundle_source=uri,
        sources=[
            _source(
                "git+https://example.invalid/missing@main",
                is_cached=False,
                has_update=True,
                remote_commit="a" * 40,
            ),
            _source(
                "git+https://example.invalid/stale@main",
                is_cached=True,
                has_update=True,
                cached_commit="b" * 40,
                remote_commit="c" * 40,
            ),
        ],
    )
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])

    async def fake_load_bundle(source_uri, *, auto_include):
        raise RuntimeError("cannot process mixed")

    patches = _command_patches(report, {"mixed-failure": mixed})
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        stack.enter_context(
            patch("amplifier_foundation.load_bundle", side_effect=fake_load_bundle)
        )
        result = CliRunner().invoke(update, ["--yes"])

    assert result.exit_code == 0, result.output
    assert "Failed to process bundle: mixed-failure: cannot process mixed" in result.output
    assert "Processed bundle: mixed-failure" not in result.output


@pytest.mark.parametrize("verbose", [False, True])
def test_failed_check_is_visible_but_never_prints_credentials(verbose):
    """A failure reports a classified reason in every renderer, never exception text."""

    secret = "https://person:secret-token@example.invalid/private"
    failed = _bundle(
        "failed",
        _source(
            "git+https://example.invalid/failed@main",
            is_cached=True,
            has_update=False,
            error=f"authentication failed for {secret}",
        ),
    )
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    patches = _command_patches(report, {"failed": failed})
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        result = CliRunner().invoke(
            update, ["--check-only", *(["--verbose"] if verbose else [])]
        )

    assert result.exit_code == 0, result.output
    assert "Check failed: authentication" in result.output
    assert secret not in result.output


@pytest.mark.parametrize("verbose", [False, True])
def test_bundle_update_keeps_a_sibling_check_failure_visible(verbose):
    """An action stays actionable while an independent failed check stays visible."""

    updating = _source(
        "git+https://example.invalid/updating@main",
        is_cached=True,
        has_update=True,
        cached_commit="a" * 40,
        remote_commit="b" * 40,
    )
    failed = _source(
        "git+https://example.invalid/failed@main",
        is_cached=True,
        has_update=None,
        error="network connection refused",
    )
    bundle = BundleStatus(
        bundle_name="mixed-outcome",
        bundle_source=updating.source_uri,
        sources=[updating, failed],
    )
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    patches = _command_patches(report, {"mixed-outcome": bundle})
    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        result = CliRunner().invoke(
            update, ["--check-only", *(["--verbose"] if verbose else [])]
        )

    assert result.exit_code == 0, result.output
    assert "Update; check failed: network" in result.output
    assert "Update 1 bundle" in result.output