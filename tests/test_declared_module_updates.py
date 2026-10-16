"""Ordinary update discovers cached declarations and installs exact module sources."""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import amplifier_foundation
import pytest
import yaml
from amplifier_foundation import Bundle, BundleRegistry
from amplifier_foundation.modules.activator import ModuleActivator
from amplifier_foundation.paths.resolution import parse_uri
from amplifier_foundation.sources.git import GitSourceHandler
from amplifier_foundation.sources.protocol import SourceStatus
from click.testing import CliRunner
from rich.console import Console

from amplifier_app_cli.commands import update as updater
from amplifier_app_cli.lib.bundle_loader import discovery as discovery_module
from amplifier_app_cli.lib.settings import AppSettings
from amplifier_app_cli.utils import module_cache, source_status, update_executor
from amplifier_app_cli.utils.file_uri import file_uri_path_value
from amplifier_app_cli.utils.include_graph import (
    _cached_source_for,
    collect_declared_module_sources,
    local_source_path,
)
from amplifier_app_cli.utils.source_status import CachedGitStatus, UpdateReport
from amplifier_app_cli.utils.update_executor import ExecutionResult

ROOT = "git+https://example.invalid/team/host.git@main"
APP = ROOT + "#subdirectory=behaviors/app.yaml"
CHILD = "git+https://example.invalid/team/included.git@main"
MODULES = "git+https://example.invalid/team/modules.git@feature/update"
TOOL = MODULES + "#subdirectory=tools/worker"
HOOK = MODULES + "#subdirectory=hooks/watch"


def _uri(parsed) -> str:
    result = f"{parsed.scheme}://{parsed.host}{parsed.path}"
    if parsed.ref:
        result += f"@{parsed.ref}"
    if parsed.subpath:
        result += f"#subdirectory={parsed.subpath}"
    return result


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    """Keep real manifest parsing and Foundation updating, fake all external edges."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.delenv("AMPLIFIER_SOURCE_STORE", raising=False)
    cache = home / "cache"
    registry = BundleRegistry(home=home)
    registry.register({"root": ROOT, "child": CHILD})
    registry.get_state("child").is_root = False
    handler = GitSourceHandler()
    real_get_status = GitSourceHandler.get_status

    def active_path(uri):
        parsed = parse_uri(uri)
        base = handler._get_cache_path(parsed, cache)
        return base / parsed.subpath if parsed.subpath else base

    def write_bundle(uri, config):
        path = active_path(uri)
        if not parse_uri(uri).subpath:
            path = path / "bundle.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"bundle": {"name": "shared-metadata", "version": "1.0.0"}, **config}
        text = yaml.safe_dump(data, sort_keys=False)
        if path.suffix == ".md":
            text = f"---\n{text}---\n"
        path.write_text(text, encoding="utf-8")
        return path

    write_bundle(ROOT, {"includes": [{"bundle": "child"}]})
    write_bundle(
        APP,
        {
            "includes": [{"bundle": CHILD}, {"bundle": CHILD}],
            "tools": [{"module": "tool-worker", "source": TOOL}],
            "hooks": [{"module": "hooks-watch", "source": HOOK}],
        },
    )
    write_bundle(CHILD, {"includes": [{"bundle": ROOT}]})
    for uri in (TOOL, HOOK):
        path = active_path(uri)
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(
            '[project]\nname = "fixture-module"\nversion = "1.0.0"\n',
            encoding="utf-8",
        )

    settings = MagicMock(spec=AppSettings)
    settings.get_app_bundles.return_value = [APP]
    settings.get_bundle_sources.return_value = {}
    settings.get_module_sources.return_value = {}
    settings.get_source_overrides.return_value = {}
    settings.get_provider_overrides.return_value = []
    settings.get_added_bundles.return_value = {}
    monkeypatch.setattr(updater, "AppSettings", lambda: settings)
    monkeypatch.setattr(updater, "BundleRegistry", lambda **kwargs: registry)
    monkeypatch.setattr(
        updater,
        "AppBundleDiscovery",
        lambda **kwargs: SimpleNamespace(list_cached_root_bundles=lambda: ["root"]),
    )
    monkeypatch.setattr(
        updater,
        "create_config_manager",
        lambda: SimpleNamespace(get_merged_settings=dict),
    )
    behind = {TOOL, HOOK}
    errors = {}
    checked = []

    async def status(self, parsed, cache_dir):
        assert cache_dir == cache
        uri = _uri(parsed)
        checked.append(uri)
        if uri in errors:
            raise errors[uri]
        return SourceStatus(
            source_uri=uri,
            is_cached=active_path(uri).exists(),
            cached_ref=parsed.ref,
            cached_commit="a" * 40,
            remote_commit=("b" if uri in behind else "a") * 40,
            has_update=uri in behind,
        )

    refreshed = []

    async def refresh(self, parsed, cache_dir):
        assert cache_dir == cache
        uri = _uri(parsed)
        refreshed.append(uri)
        return SimpleNamespace(active_path=active_path(uri))

    monkeypatch.setattr(GitSourceHandler, "get_status", status)
    monkeypatch.setattr(GitSourceHandler, "update", refresh)
    forbidden = AsyncMock(side_effect=AssertionError("unexpected resolve/load/prepare"))
    monkeypatch.setattr(GitSourceHandler, "resolve", forbidden)
    monkeypatch.setattr(BundleRegistry, "load", forbidden)
    monkeypatch.setattr(Bundle, "prepare", forbidden)
    monkeypatch.setattr(amplifier_foundation, "load_bundle", forbidden)
    installs = AsyncMock()
    monkeypatch.setattr(ModuleActivator, "_install_dependencies", installs)
    apply = AsyncMock(wraps=amplifier_foundation.update_bundle)
    monkeypatch.setattr(amplifier_foundation, "update_bundle", apply)
    report = UpdateReport(local_file_sources=[], cached_git_sources=[])
    monkeypatch.setattr(updater, "check_all_sources", AsyncMock(return_value=report))
    execute = AsyncMock(
        return_value=ExecutionResult(success=True, updated=[], messages=[])
    )
    monkeypatch.setattr(updater, "execute_updates", execute)
    monkeypatch.setattr(
        "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
        lambda: None,
    )
    skills = MagicMock()
    save = MagicMock()
    clear = MagicMock(side_effect=AssertionError("unexpected cache clear"))
    monkeypatch.setattr(updater, "_refresh_skills_cache", skills)
    monkeypatch.setattr(updater, "save_update_last_check", save)
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.clear_all_regenerable", clear
    )
    output = io.StringIO()
    monkeypatch.setattr(updater, "console", Console(file=output, width=240))

    def invoke(*args, input=None):
        output.seek(0)
        output.truncate()
        result = CliRunner().invoke(updater.update, list(args), input=input)
        assert result.exit_code == 0, (result.exception, output.getvalue())
        return output.getvalue()

    return SimpleNamespace(
        cache=cache,
        registry=registry,
        settings=settings,
        behind=behind,
        errors=errors,
        checked=checked,
        refreshed=refreshed,
        installs=installs,
        apply=apply,
        report=report,
        execute=execute,
        forbidden=forbidden,
        skills=skills,
        save=save,
        clear=clear,
        active_path=active_path,
        write_bundle=write_bundle,
        invoke=invoke,
        real_get_status=real_get_status,
    )


def test_current_root_same_repo_app_updates_and_installs_both_module_subdirectories(
    scenario,
):
    output = scenario.invoke("--check-only")
    assert "Update 2 modules" in output
    assert "Update 2 bundles" not in output
    assert "All sources up to date" not in output
    assert not scenario.refreshed
    declined = scenario.invoke(input="n\n")
    assert "Update cancelled" in declined
    assert not scenario.refreshed

    output = scenario.invoke("--yes")
    assert "Update complete" in output
    assert "Updated module: tool-worker" in output
    assert "Updated module: hooks-watch" in output
    assert scenario.refreshed == [TOOL, HOOK]
    assert [call.args[0] for call in scenario.installs.await_args_list] == [
        scenario.active_path(TOOL),
        scenario.active_path(HOOK),
    ]
    assert scenario.apply.await_count == 2
    for call, uri, module_id in zip(
        scenario.apply.await_args_list,
        (TOOL, HOOK),
        ("tool-worker", "hooks-watch"),
        strict=True,
    ):
        source_only = call.args[0]
        assert not getattr(source_only, "_source_uri", None)
        assert source_only.tools == [{"module": module_id, "source": uri}]
        assert call.kwargs == {
            "cache_dir": scenario.cache,
            "selective": [uri],
            "install_deps": True,
        }
    scenario.forbidden.assert_not_called()


def test_all_five_module_kinds_and_shared_exact_source_have_first_provenance(scenario):
    orchestrator = MODULES + "#subdirectory=orchestrator"
    context = MODULES + "#subdirectory=context"
    provider = MODULES + "#subdirectory=provider"
    scenario.write_bundle(
        ROOT,
        {
            "includes": [{"bundle": "child"}],
            "session": {
                "orchestrator": {"module": "loop-worker", "source": orchestrator},
                "context": {"module": "context-worker", "source": context},
            },
            "providers": [{"module": "provider-worker", "source": provider}],
        },
    )
    scenario.write_bundle(
        CHILD,
        {
            "includes": [{"bundle": ROOT}],
            "tools": [{"module": "tool-second-id", "source": TOOL}],
        },
    )
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"root": ROOT, "app": APP},
            registry=scenario.registry,
            cache_dir=scenario.cache,
        )
    )
    assert not failures
    assert set(found) == {orchestrator, context, provider, TOOL, HOOK}
    assert found[TOOL].module_id == "tool-worker"
    assert found[TOOL].declared_by == "shared-metadata"
    assert found[TOOL].root == "app"
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert APP not in statuses  # Repo row dedup must not suppress this manifest.
    assert {
        uri
        for uri, status in statuses.items()
        if isinstance(status, updater.DeclaredModuleStatus)
    } == set(found)
    scenario.invoke("--yes")
    assert scenario.refreshed == [TOOL, HOOK]
    assert scenario.apply.await_count == 2


def test_module_override_precedence_and_local_source_never_refresh_original(
    scenario, tmp_path
):
    local = tmp_path / "local-module"
    local.mkdir()
    specific = MODULES + "#subdirectory=specific"
    provider = MODULES + "#subdirectory=provider-choice"
    scenario.settings.get_module_sources.return_value = {
        "tool-worker": specific,
        "hooks-watch": specific,
    }
    scenario.settings.get_source_overrides.return_value = {
        "tool-worker": local.as_uri(),
        "hooks-watch": specific,
    }
    scenario.settings.get_provider_overrides.return_value = [
        {"module": "hooks-watch", "source": provider}
    ]
    scenario.behind.add(provider)
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert local.as_uri() in statuses
    assert provider in statuses
    assert TOOL not in statuses and HOOK not in statuses and specific not in statuses
    scenario.invoke("--yes")
    assert scenario.refreshed == [provider]
    assert TOOL not in scenario.checked and HOOK not in scenario.checked


@pytest.mark.parametrize("filename", ["override.yaml", "override space %20.yaml"])
def test_bundle_include_overrides_use_runtime_resolver_and_aliases(
    scenario, tmp_path, filename
):
    local = tmp_path / filename
    local.write_text(
        yaml.safe_dump(
            {
                "bundle": {"name": "override"},
                "tools": [
                    {"module": "tool-extra", "source": MODULES + "#subdirectory=extra"}
                ],
            }
        ),
        encoding="utf-8",
    )
    scenario.settings.get_bundle_sources.return_value = {
        "child": local.as_uri(),
        "included.git": local.as_uri(),
    }
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert MODULES + "#subdirectory=extra" in statuses
    assert CHILD not in statuses
    assert CHILD not in scenario.checked


def test_local_overrides_also_suppress_replaced_legacy_repository_refresh(
    scenario, tmp_path
):
    local = tmp_path / "local-module"
    local.mkdir()
    scenario.settings.get_source_overrides.return_value = {
        "tool-worker": local.as_uri(),
        "hooks-watch": local.as_uri(),
    }
    scenario.report.cached_git_sources = [
        CachedGitStatus(
            name="unrelated-display-name",
            url="https://example.invalid/team/modules.git",
            ref="feature/update",
            has_update=True,
        )
    ]
    output = scenario.invoke("--yes")
    assert scenario.report.cached_git_sources == []
    assert not scenario.refreshed
    assert TOOL not in scenario.checked and HOOK not in scenario.checked
    assert "All sources up to date" not in output
    scenario.apply.assert_not_called()
    scenario.execute.assert_not_called()


@pytest.mark.parametrize("unreadable", [False, True])
def test_missing_or_unreadable_cached_manifest_is_explicit_not_green(
    scenario, unreadable
):
    scenario.behind.clear()
    path = scenario.active_path(APP)
    if unreadable:
        path.write_text("bundle: [broken\n", encoding="utf-8")
    else:
        path.unlink()
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert any(source.error for source in statuses["root"].sources)
    assert not statuses["root"].has_updates
    output = scenario.invoke("--check-only")
    assert "Check failed: incomplete module discovery" in output
    assert "could not be checked" in output
    assert "All sources up to date" not in output
    assert not scenario.refreshed


def test_one_status_failure_keeps_other_action_and_hides_private_credentials(scenario):
    scenario.errors[TOOL] = TimeoutError(
        "https://private-user:private-token@example.invalid/unavailable"
    )
    output = scenario.invoke("--check-only", "--verbose")
    assert "timeout" in output
    assert "private-token" not in output and "private-user" not in output
    assert "Update 1 module" in output
    assert "Module:" in output
    applied = scenario.invoke("--yes")
    assert scenario.refreshed == [HOOK]
    assert "Update completed with errors" in applied
    assert "timeout" in applied and "private-token" not in applied


def test_credential_bearing_module_uri_is_an_exact_action_not_a_display_label(scenario):
    private_uri = TOOL.replace("https://", "https://fixture-user:fixture-token@")
    scenario.write_bundle(
        APP, {"tools": [{"module": "tool-worker", "source": private_uri}]}
    )
    scenario.behind.clear()
    scenario.behind.add(private_uri)
    checked = scenario.invoke("--check-only", "--verbose")
    assert "fixture-user" not in checked and "fixture-token" not in checked
    applied = scenario.invoke("--yes")
    assert scenario.refreshed == [private_uri]
    assert scenario.apply.await_args.kwargs["selective"] == [private_uri]
    assert "fixture-user" not in applied and "fixture-token" not in applied


def test_module_action_identity_keeps_dot_git_and_different_refs(scenario):
    no_suffix = TOOL.replace("modules.git@", "modules@")
    other_ref = TOOL.replace("@feature/update", "@release/next")
    sources = [TOOL, no_suffix, other_ref]
    scenario.write_bundle(
        APP,
        {
            "tools": [
                {"module": f"tool-{index}", "source": uri}
                for index, uri in enumerate(sources)
            ]
        },
    )
    scenario.behind.clear()
    scenario.behind.update(sources)
    scenario.invoke("--yes")
    assert scenario.refreshed == sources
    assert [call.kwargs["selective"] for call in scenario.apply.await_args_list] == [
        [uri] for uri in sources
    ]


def test_module_install_failure_continues_and_has_honest_terminal_summary(scenario):
    async def install(path):
        if path == scenario.active_path(TOOL):
            raise RuntimeError("authentication private-token rejected")

    scenario.installs.side_effect = install
    output = scenario.invoke("--yes")
    assert scenario.refreshed == [TOOL, HOOK]
    assert "Update completed with errors" in output
    assert "Failed to update module" in output
    assert "authentication" in output
    assert "private-token" not in output


@pytest.mark.parametrize("force", [False, True])
def test_check_only_even_with_force_cannot_clear_resolve_update_or_install(
    scenario, force
):
    before = {
        path.relative_to(scenario.cache): path.read_bytes()
        for path in scenario.cache.rglob("*")
        if path.is_file()
    }
    scenario.invoke("--check-only", *(["--force"] if force else []))
    after = {
        path.relative_to(scenario.cache): path.read_bytes()
        for path in scenario.cache.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not scenario.refreshed
    scenario.apply.assert_not_called()
    scenario.installs.assert_not_called()
    scenario.execute.assert_not_called()
    scenario.clear.assert_not_called()
    scenario.skills.assert_not_called()
    scenario.save.assert_not_called()
    scenario.forbidden.assert_not_called()


def test_legacy_cache_dedup_uses_url_ref_and_not_basename(scenario):
    selected = CachedGitStatus(
        name="different-entry-point",
        url=MODULES.split("@")[0].removeprefix("git+"),
        ref="feature/update",
        has_update=True,
    )
    other_ref = CachedGitStatus(
        name="same-basename",
        url=selected.url,
        ref="other",
        has_update=True,
    )
    other_repo = CachedGitStatus(
        name="same-basename",
        url="https://example.invalid/other/modules.git",
        ref="feature/update",
        has_update=True,
    )
    scenario.report.cached_git_sources = [selected, other_ref, other_repo]
    scenario.invoke("--yes")
    assert scenario.report.cached_git_sources == [other_ref, other_repo]
    assert scenario.execute.await_args.args[0].cached_git_sources == [
        other_ref,
        other_repo,
    ]
    assert scenario.refreshed == [TOOL, HOOK]


def test_pinned_module_is_reported_without_action_and_unrelated_source_is_untouched(
    scenario,
):
    pinned = "git+https://example.invalid/team/pinned.git@v1.2.3#subdirectory=module"
    scenario.write_bundle(APP, {"tools": [{"module": "tool-pin", "source": pinned}]})
    scenario.behind.clear()
    # Even a checker returning has_update=True cannot select a pinned module.
    scenario.behind.add(pinned)
    output = scenario.invoke("--check-only")
    assert "Pinned" in output
    assert "All sources up to date" not in output
    scenario.invoke("--yes")
    assert not scenario.refreshed
    scenario.apply.assert_not_called()


def test_root_refresh_selects_only_its_own_uri_not_current_declared_modules(
    scenario, monkeypatch
):
    scenario.behind.clear()
    scenario.behind.add(ROOT)

    async def load_root(uri, *, auto_include):
        assert uri == ROOT and auto_include is False
        bundle = Bundle(name="root", tools=[{"module": "tool-worker", "source": TOOL}])
        bundle._source_uri = uri
        return bundle

    monkeypatch.setattr(amplifier_foundation, "load_bundle", load_root)
    scenario.invoke("--yes")
    assert scenario.refreshed == [ROOT]
    assert scenario.apply.await_args.kwargs == {"selective": [ROOT]}
    scenario.installs.assert_not_called()


def test_real_discovery_walks_all_same_repo_root_manifests_before_row_dedup(
    scenario, monkeypatch
):
    second = ROOT + "#subdirectory=behaviors/second.yaml"
    unreachable = MODULES + "#subdirectory=unreachable"
    scenario.write_bundle(ROOT, {})
    scenario.write_bundle(APP, {"tools": [{"module": "tool-worker", "source": TOOL}]})
    scenario.write_bundle(
        second, {"hooks": [{"module": "hooks-watch", "source": HOOK}]}
    )
    scenario.write_bundle(
        CHILD, {"tools": [{"module": "tool-unreachable", "source": unreachable}]}
    )
    scenario.registry.register({"first-root": APP, "second-root": second})
    scenario.registry.save()
    persisted = json.loads(
        (scenario.cache.parent / "registry.json").read_text(encoding="utf-8")
    )
    assert persisted["bundles"]["first-root"]["is_root"] is True
    assert persisted["bundles"]["second-root"]["is_root"] is True
    assert persisted["bundles"]["child"]["is_root"] is False
    scenario.settings.get_app_bundles.return_value = []
    monkeypatch.setattr(discovery_module, "WELL_KNOWN_BUNDLES", {})
    monkeypatch.setattr(
        "amplifier_app_cli.lib.settings.AppSettings", lambda: scenario.settings
    )
    monkeypatch.setattr(
        updater, "AppBundleDiscovery", discovery_module.AppBundleDiscovery
    )
    monkeypatch.setattr(updater, "BundleRegistry", BundleRegistry)

    discovery = discovery_module.AppBundleDiscovery()
    assert discovery.list_cached_root_bundles() == ["root"]
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert set(statuses) == {"root", TOOL, HOOK}
    assert not statuses["root"].has_updates
    assert statuses[TOOL].module_id == "tool-worker"
    assert statuses[HOOK].module_id == "hooks-watch"
    assert unreachable not in scenario.checked
    output = scenario.invoke("--check-only")
    assert "Update 2 modules" in output
    assert "Update 2 bundles" not in output
    scenario.invoke("--yes")
    assert scenario.refreshed == [TOOL, HOOK]
    assert scenario.apply.await_count == 2


@pytest.mark.parametrize(
    ("remote_commit", "expected"), [("b" * 40, "download"), (None, "check_failed")]
)
def test_missing_module_uses_real_foundation_status_without_green_current(
    scenario, monkeypatch, remote_commit, expected
):
    missing = "git+https://example.invalid/team/absent.git@main"
    scenario.write_bundle(
        APP, {"tools": [{"module": "tool-absent", "source": missing}]}
    )
    scenario.behind.clear()
    assert not scenario.active_path(missing).exists()
    existing_status = GitSourceHandler.get_status

    async def check_status(self, parsed, cache_dir):
        if _uri(parsed) == missing:
            return await scenario.real_get_status(self, parsed, cache_dir)
        return await existing_status(self, parsed, cache_dir)

    remote = AsyncMock(return_value=remote_commit)
    monkeypatch.setattr(GitSourceHandler, "_get_remote_commit", remote)
    monkeypatch.setattr(GitSourceHandler, "get_status", check_status)
    statuses = asyncio.run(updater._check_all_bundle_status())
    source = statuses[missing].sources[0]
    assert source.is_cached is False
    assert source.cached_commit is None
    expected_update = True if remote_commit else None
    assert source.has_update is expected_update
    assert updater._classify_source_status(source) == expected
    output = scenario.invoke("--check-only")
    assert "All sources up to date" not in output
    assert "Downloaded module" not in output
    if remote_commit:
        assert "Download 1 module" in output
    else:
        assert "could not be checked" in output
    remote.assert_awaited_with("https://example.invalid/team/absent.git", "main")
    scenario.apply.assert_not_called()
    scenario.forbidden.assert_not_called()


def test_unknown_missing_module_is_not_current_or_an_update_action(
    scenario, monkeypatch
):
    missing = "git+https://example.invalid/team/unknown.git@main"
    scenario.write_bundle(
        APP, {"tools": [{"module": "tool-unknown", "source": missing}]}
    )
    existing_status = GitSourceHandler.get_status

    async def check_status(self, parsed, cache_dir):
        if _uri(parsed) == missing:
            return SourceStatus(source_uri=missing, is_cached=False, has_update=None)
        return await existing_status(self, parsed, cache_dir)

    monkeypatch.setattr(GitSourceHandler, "get_status", check_status)
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert not statuses[missing].has_updates
    assert (
        updater._classify_source_status(statuses[missing].sources[0]) == "not_checked"
    )
    output = scenario.invoke("--check-only")
    assert "Not checked" in output
    assert "could not be checked" in output
    assert "All sources up to date" not in output
    scenario.apply.assert_not_called()


@pytest.mark.parametrize("fragment", [False, True])
def test_cached_bundle_include_file_uri_round_trip_decodes_once(
    scenario, tmp_path, fragment
):
    local = tmp_path / "manifest space %20.yaml"
    extra = MODULES + "#subdirectory=extra"
    local.write_text(
        yaml.safe_dump(
            {
                "bundle": {"name": "local-manifest"},
                "tools": [{"module": "tool-extra", "source": extra}],
            }
        ),
        encoding="utf-8",
    )
    uri = (
        tmp_path.as_uri() + f"#subdirectory={local.name}"
        if fragment else local.as_uri()
    )
    assert _cached_source_for(uri, scenario.cache, scenario.registry).active_path == local
    scenario.write_bundle(CHILD, {"includes": [{"bundle": uri}]})

    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"root": ROOT},
            registry=scenario.registry,
            cache_dir=scenario.cache,
        )
    )
    assert not failures
    assert set(found) == {extra}
    assert found[extra].declared_by == "local-manifest"
    scenario.forbidden.assert_not_called()


def test_ordinary_manifest_path_preserves_literal_percent_escape(tmp_path):
    local = tmp_path / "manifest space %20.yaml"
    local.write_text("bundle: {name: literal-path}\n", encoding="utf-8")

    registry = BundleRegistry(home=tmp_path, persist=False)
    assert _cached_source_for(str(local), tmp_path / "cache", registry).active_path == local


def test_relative_embedded_module_is_covered_by_exact_owner(scenario):
    embedded = scenario.active_path(ROOT) / "modules" / "tool-embedded"
    embedded.mkdir(parents=True)
    scenario.write_bundle(
        APP, {"tools": [{"module": "tool-embedded", "source": "../modules/tool-embedded"}]}
    )
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"app": APP}, registry=scenario.registry, cache_dir=scenario.cache
        )
    )
    assert not failures
    assert found[str(embedded)].declaring_uri == APP
    assert found[str(embedded)].embedded_in == ROOT
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert str(embedded) not in statuses
    scenario.behind.clear()
    output = scenario.invoke("--check-only")
    assert "tool-embedded" not in output
    assert str(scenario.cache) not in output
    assert "All sources up to date" in output


@pytest.mark.parametrize("source_form", ["native", "file", "file_fragment"])
def test_local_module_effective_path_preserves_literal_percent_and_hash(
    scenario, source_form
):
    root = scenario.active_path(ROOT)
    local = root / "module space %20#literal"
    local.mkdir()
    source = {
        "native": str(local),
        "file": local.as_uri(),
        "file_fragment": root.as_uri() + f"#subdirectory={local.name}",
    }[source_form]
    assert local_source_path(source) == local
    assert updater._classify_source_status(
        SourceStatus(source_uri=source, is_cached=True, has_update=None)
    ) == "local"
    scenario.write_bundle(
        APP, {"tools": [{"module": "tool-embedded", "source": source}]}
    )
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"app": APP}, registry=scenario.registry, cache_dir=scenario.cache
        )
    )
    assert not failures
    assert found[source].embedded_in == ROOT
    assert source not in asyncio.run(updater._check_all_bundle_status())


@pytest.mark.parametrize("as_file_uri", [False, True])
def test_explicit_local_override_inside_cache_stays_visible(scenario, as_file_uri):
    local = scenario.active_path(ROOT) / "modules" / "literal %20"
    local.mkdir(parents=True)
    uri = local.as_uri() if as_file_uri else str(local)
    scenario.write_bundle(APP, {"tools": [{"module": "tool-worker", "source": uri}]})
    scenario.settings.get_source_overrides.return_value = {"tool-worker": uri}
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"app": APP},
            registry=scenario.registry,
            cache_dir=scenario.cache,
            overrides={"tool-worker": uri},
        )
    )
    assert not failures and found[uri].embedded_in is None
    scenario.behind.clear()
    output = scenario.invoke("--check-only")
    assert "tool-worker (local)" in output and "Local" in output
    assert str(scenario.cache) not in output
    assert "Not checked" not in output
    scenario.invoke("--yes")
    assert not scenario.refreshed


@pytest.mark.parametrize("kind", ["missing", "sibling", "escape", "symlink"])
@pytest.mark.parametrize("file_fragment", [False, True])
def test_unproven_local_ownership_is_not_hidden(scenario, kind, file_fragment):
    root = scenario.active_path(ROOT)
    sibling = scenario.active_path(CHILD) / "module"
    sibling.mkdir()
    if kind == "missing":
        source = str(root / "missing")
    elif kind == "sibling":
        source = str(sibling)
    elif kind == "escape":
        source = str(root / ".." / sibling.parent.name / "module")
    else:
        (root / "linked").symlink_to(sibling, target_is_directory=True)
        source = str(root / "linked")
    expected_path = local_source_path(source)
    assert expected_path is not None
    if file_fragment:
        subpath = (
            str(sibling) if kind == "sibling" else str(expected_path.relative_to(root))
        )
        source = root.as_uri() + f"#subdirectory={subpath}"
    assert local_source_path(source) == expected_path
    scenario.write_bundle(APP, {"tools": [{"module": "tool-local", "source": source}]})
    found, _ = asyncio.run(
        collect_declared_module_sources(
            {"app": APP}, registry=scenario.registry, cache_dir=scenario.cache
        )
    )
    assert found[source].embedded_in is None
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert statuses[source].bundle_source == source
    assert updater._classify_source_status(statuses[source].sources[0]) == (
        "missing_path" if kind == "missing" else "local"
    )
    output = scenario.invoke("--check-only")
    assert "tool-local (local)" in output
    assert ("Missing path" if kind == "missing" else "Local") in output


def test_disagreeing_declarations_do_not_hide_shared_local_source(scenario):
    local = scenario.active_path(ROOT) / "module"
    local.mkdir()
    declaration = {"tools": [{"module": "tool-shared", "source": str(local)}]}
    scenario.write_bundle(APP, declaration)
    scenario.write_bundle(CHILD, declaration)
    found, _ = asyncio.run(
        collect_declared_module_sources(
            {"app": APP, "other": CHILD},
            registry=scenario.registry,
            cache_dir=scenario.cache,
        )
    )
    assert found[str(local)].embedded_in is None


@pytest.mark.parametrize("owner", [ROOT.replace("@main", "@next"), ROOT.replace(".git@", "@")])
def test_embedded_owner_missing_after_report_dedup_gets_exact_cache_row(scenario, owner):
    manifest = owner + "#subdirectory=behaviors/extra.yaml"
    local = scenario.active_path(owner) / "module"
    local.mkdir(parents=True)
    scenario.write_bundle(
        manifest, {"tools": [{"module": "tool-embedded", "source": "../module"}]}
    )
    scenario.registry.register({"different-cache": manifest})
    scenario.registry.save()
    # Reproduce repository row dedup omitting this root. Seeds still include it.
    scenario.behind.clear()
    scenario.behind.add(owner)
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert str(local) not in statuses
    assert statuses[owner].bundle_source == owner
    assert isinstance(statuses[owner], updater.TransitiveBundleStatus)
    scenario.invoke("--yes")
    assert scenario.refreshed == [owner]
    scenario.installs.assert_not_called()


@pytest.mark.parametrize("namespace", ["shared-metadata", "modes"])
def test_cached_extensionless_source_bound_includes(scenario, namespace):
    # Containing root supplies only bindings: its own includes are not traversed.
    scenario.write_bundle(ROOT, {"includes": ["not-registered"]})
    if namespace == "modes":
        root_manifest = scenario.active_path(ROOT) / "bundle.md"
        root_manifest.write_text("---\nbundle: {name: modes}\n---\n")
    scenario.write_bundle(
        APP,
        {"bundle": {"name": "shared-metadata", "namespace_root": ".."},
         "includes": [f"{namespace}:behaviors/extra"]},
    )
    extra = ROOT + "#subdirectory=behaviors/extra.yaml"
    scenario.write_bundle(
        extra,
        {"tools": [{"module": "tool-extra", "source": TOOL}],
         "includes": [APP]},
    )
    # A conflicting registration must not replace this manifest's selected ref.
    scenario.registry.register({namespace: CHILD})
    assert scenario.registry.get_state(namespace).local_path is None
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"app": APP}, registry=scenario.registry, cache_dir=scenario.cache
        )
    )
    assert not failures
    assert set(found) == {TOOL}
    assert found[TOOL].declaring_uri == extra
    scenario.forbidden.assert_not_called()


@pytest.mark.parametrize("verbose", [False, True])
def test_incomplete_diagnostic_never_becomes_blank_module_row(scenario, verbose):
    scenario.behind.clear()
    scenario.active_path(APP).unlink()
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert updater._collect_unified_modules(scenario.report, statuses) == {}
    output = scenario.invoke("--check-only", *(["--verbose"] if verbose else []))
    assert "incomplete module discovery" in output
    assert "could not be checked" in output
    assert "Modules" not in output


@pytest.mark.parametrize("force", [False, True])
def test_real_check_registry_does_not_persist_stale_path_cleanup(scenario, monkeypatch, force):
    scenario.registry.get_state("root").local_path = str(scenario.cache / "gone")
    scenario.registry.save()
    home = scenario.cache.parent
    before = {
        p.relative_to(home): p.read_bytes() if p.is_file() else None
        for p in home.rglob("*")
    }
    monkeypatch.setattr(discovery_module, "WELL_KNOWN_BUNDLES", {})
    monkeypatch.setattr(
        "amplifier_app_cli.lib.settings.AppSettings", lambda: scenario.settings
    )
    monkeypatch.setattr(updater, "AppBundleDiscovery", discovery_module.AppBundleDiscovery)
    monkeypatch.setattr(updater, "BundleRegistry", BundleRegistry)
    scenario.invoke("--check-only", *(["--force"] if force else []))
    assert {
        p.relative_to(home): p.read_bytes() if p.is_file() else None
        for p in home.rglob("*")
    } == before
    assert not scenario.refreshed
    for boundary in (
        scenario.apply, scenario.installs, scenario.execute, scenario.clear,
        scenario.skills, scenario.save, scenario.forbidden,
    ):
        boundary.assert_not_called()


@pytest.mark.parametrize("malformed", [False, True])
def test_omitted_owner_discovery_failure_cannot_be_green(scenario, malformed):
    owner = ROOT.replace("@main", "@next")
    manifest = owner + "#subdirectory=behaviors/extra.yaml"
    path = scenario.write_bundle(manifest, {})
    if malformed:
        path.write_text("bundle: [broken\n")
    else:
        path.unlink()
    scenario.registry.register({"omitted": manifest})
    scenario.behind.clear()
    output = scenario.invoke("--check-only")
    assert "incomplete module discovery" in output
    assert "All sources up to date" not in output
    assert owner in scenario.checked


def test_recovered_owner_status_failure_keeps_independent_module_action(scenario):
    owner = ROOT.replace("@main", "@next")
    manifest = owner + "#subdirectory=behaviors/extra.yaml"
    (scenario.active_path(owner) / "module").mkdir(parents=True)
    scenario.write_bundle(
        manifest, {"tools": [{"module": "tool-embedded", "source": "../module"}]}
    )
    scenario.registry.register({"omitted": manifest})
    scenario.errors[owner] = TimeoutError("private-token")
    output = scenario.invoke("--check-only")
    assert "timeout" in output and "private-token" not in output
    assert "Update 2 modules" in output


@pytest.mark.parametrize("link", ["manifest", "cache"])
def test_escaped_declaring_manifest_is_incomplete_not_embedded(scenario, link):
    root = scenario.active_path(ROOT)
    outside = scenario.active_path(CHILD)
    (root / "module").mkdir()
    scenario.write_bundle(
        CHILD, {"tools": [{"module": "tool-embedded", "source": str(root / "module")}]}
    )
    if link == "manifest":
        (root / "bundle.md").unlink()
        (root / "bundle.md").symlink_to(outside / "bundle.md")
    else:
        # Only delete this fixture's own empty files/directories.
        (root / "bundle.md").unlink()
        scenario.active_path(APP).unlink()
        (root / "behaviors").rmdir()
        (root / "module").rmdir()
        root.rmdir()
        root.symlink_to(outside, target_is_directory=True)
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"root": ROOT}, registry=scenario.registry, cache_dir=scenario.cache
        )
    )
    assert not found
    assert failures == {"root": "Incomplete cached module discovery"}


@pytest.mark.parametrize("stale_path", [False, True])
def test_foreign_cached_namespace_uses_selected_uri_and_namespace_root(scenario, stale_path):
    selected = CHILD.replace("@main", "@next") + "#subdirectory=bundles/foreign/bundle.yaml"
    scenario.write_bundle(
        selected, {"bundle": {"name": "foreign", "namespace_root": "../.."},
                   "includes": ["not-traversed"]}
    )
    extra = CHILD.replace("@main", "@next") + "#subdirectory=behaviors/extra.yaml"
    scenario.write_bundle(extra, {"tools": [{"module": "tool-extra", "source": TOOL}]})
    scenario.registry.register({"foreign": selected})
    if stale_path:
        scenario.registry.get_state("foreign").local_path = str(scenario.active_path(ROOT))
    scenario.registry.save()
    before = (scenario.cache.parent / "registry.json").read_bytes()
    scenario.write_bundle(APP, {"includes": ["foreign:behaviors/extra"]})
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"app": APP}, registry=scenario.registry, cache_dir=scenario.cache
        )
    )
    assert not failures and set(found) == {TOOL}
    assert found[TOOL].declaring_uri == extra
    assert (scenario.cache.parent / "registry.json").read_bytes() == before


@pytest.mark.parametrize("override_first", [False, True])
def test_shared_local_source_with_one_explicit_override_never_hidden(scenario, override_first):
    local = scenario.active_path(ROOT) / "module"
    local.mkdir()
    rows = [
        {"module": "tool-embedded", "source": str(local)},
        {"module": "tool-override", "source": str(local)},
    ]
    scenario.write_bundle(APP, {"tools": rows[::-1] if override_first else rows})
    found, _ = asyncio.run(
        collect_declared_module_sources(
            {"app": APP}, registry=scenario.registry, cache_dir=scenario.cache,
            overrides={"tool-override": str(local)},
        )
    )
    assert found[str(local)].embedded_in is None


@pytest.mark.parametrize("malformed", [False, True])
def test_foreign_namespace_requires_its_selected_manifest(scenario, malformed):
    selected = CHILD + "#subdirectory=bundles/foreign/bundle.yaml"
    path = scenario.write_bundle(selected, {"bundle": {"name": "foreign"}})
    if malformed:
        path.write_text("bundle: [broken\n")
    else:
        path.unlink()
    # The resource exists at the guessed fallback path, but missing namespace
    # metadata cannot establish that this is the selected namespace's resource.
    scenario.write_bundle(
        CHILD + "#subdirectory=behaviors/extra.yaml",
        {"tools": [{"module": "tool-extra", "source": TOOL}]},
    )
    scenario.registry.register({"foreign": selected})
    scenario.write_bundle(APP, {"includes": ["foreign:behaviors/extra"]})
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"app": APP}, registry=scenario.registry, cache_dir=scenario.cache
        )
    )
    assert not found
    assert failures == {"app": "Incomplete cached module discovery"}


def test_explicit_extensionless_git_include_keeps_uri_identity(scenario):
    extensionless = CHILD + "#subdirectory=behaviors/extra"
    scenario.write_bundle(
        extensionless + ".yaml", {"tools": [{"module": "tool-extra", "source": TOOL}]}
    )
    scenario.write_bundle(APP, {"includes": [extensionless]})
    found, failures = asyncio.run(
        collect_declared_module_sources(
            {"app": APP}, registry=scenario.registry, cache_dir=scenario.cache
        )
    )
    assert not failures and found[TOOL].declaring_uri == extensionless


@pytest.mark.parametrize("embedded_first", [False, True])
@pytest.mark.parametrize("diagnostic", [False, True])
@pytest.mark.parametrize("subdirectory", [False, True])
def test_exact_git_module_install_survives_embedded_owner_recovery(
    scenario, embedded_first, diagnostic, subdirectory
):
    owner = ROOT.replace("@main", "@next")
    explicit = owner + "#subdirectory=installable" if subdirectory else owner
    manifest = owner + "#subdirectory=behaviors/extra.yaml"
    module = scenario.active_path(owner) / "embedded"
    module.mkdir(parents=True)
    installable = scenario.active_path(explicit)
    installable.mkdir(exist_ok=True)
    (installable / "pyproject.toml").write_text(
        '[project]\nname = "fixture-root-module"\nversion = "1.0.0"\n'
    )
    rows = [
        {"module": "tool-root-git", "source": explicit},
        {"module": "tool-embedded", "source": "../embedded"},
    ]
    scenario.write_bundle(
        manifest,
        {"tools": rows[::-1] if embedded_first else rows,
         "includes": ["missing:absent"] if diagnostic else []},
    )
    scenario.registry.register({"omitted": manifest})
    scenario.behind.clear()
    scenario.behind.add(explicit)
    statuses = asyncio.run(updater._check_all_bundle_status())
    assert isinstance(statuses[explicit], updater.DeclaredModuleStatus)
    assert sum(
        s.bundle_source.split("#")[0] == owner for s in statuses.values()
    ) == 1
    output = scenario.invoke("--yes")
    assert scenario.refreshed == [explicit]
    assert scenario.apply.await_args.kwargs["selective"] == [explicit]
    assert scenario.apply.await_args.kwargs["install_deps"] is True
    scenario.installs.assert_awaited_once_with(installable)
    if diagnostic:
        assert "incomplete module discovery" in output


@pytest.mark.parametrize("install_source", ["none", "root", "subdirectory"])
def test_hybrid_owner_coalesces_real_legacy_refresh_without_losing_module_install(
    scenario, monkeypatch, install_source
):
    owner = ROOT.replace("@main", "@next")
    manifest = owner + "#subdirectory=behaviors/hybrid.yaml"
    controls = [
        owner.replace("@next", "@other"),
        owner.replace(".git@", "@"),
        owner.replace("/team/", "/other/"),
    ]

    def seed_module_cache(uri, module_id):
        path = scenario.active_path(uri)
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(
            f'[project]\nname = "{module_id}"\nversion = "1.0.0"\n'
            '[project.entry-points."amplifier.modules"]\n'
            f'"{module_id}" = "fixture:mount"\n',
            encoding="utf-8",
        )
        parsed = parse_uri(uri)
        (path / ".amplifier_cache_meta.json").write_text(
            json.dumps({
                "git_url": f"https://{parsed.host}{parsed.path}",
                "ref": parsed.ref,
                "commit": "a" * 40,
            }),
            encoding="utf-8",
        )
        return path

    owner_path = seed_module_cache(owner, "tool-hybrid")
    for index, uri in enumerate(controls):
        seed_module_cache(uri, f"tool-control-{index}")
    (owner_path / "embedded").mkdir()
    rows = [{"module": "tool-embedded", "source": "../embedded"}]
    explicit = owner
    if install_source != "none":
        if install_source == "subdirectory":
            explicit += "#subdirectory=installable"
            installable = scenario.active_path(explicit)
            installable.mkdir()
            (installable / "pyproject.toml").write_text(
                '[project]\nname = "fixture-installable"\nversion = "1.0.0"\n',
                encoding="utf-8",
            )
        rows.append({"module": "tool-independent", "source": explicit})
    scenario.write_bundle(ROOT, {})
    scenario.write_bundle(manifest, {"tools": rows})
    assert not (owner_path / "bundle.md").exists()
    assert not (owner_path / "bundle.yaml").exists()
    scenario.registry.register({"hybrid": manifest})
    scenario.registry.save()
    scenario.settings.get_app_bundles.return_value = []
    scenario.behind.clear()
    scenario.behind.update({owner, explicit})

    # Use real discovery, legacy scanner, Click, executor and cache refresher.
    # Only settings, remote status/update and dependency installation are faked.
    monkeypatch.setattr(discovery_module, "WELL_KNOWN_BUNDLES", {})
    monkeypatch.setattr(
        "amplifier_app_cli.lib.settings.AppSettings", lambda: scenario.settings
    )
    monkeypatch.setattr(updater, "AppBundleDiscovery", discovery_module.AppBundleDiscovery)
    monkeypatch.setattr(updater, "BundleRegistry", BundleRegistry)
    monkeypatch.setattr(
        source_status, "_get_github_commit_sha", AsyncMock(return_value="b" * 40)
    )
    check = AsyncMock(wraps=source_status.check_all_sources)
    execute = AsyncMock(wraps=update_executor.execute_updates)
    refresh = AsyncMock(wraps=module_cache.update_module)
    monkeypatch.setattr(updater, "check_all_sources", check)
    monkeypatch.setattr(updater, "execute_updates", execute)
    monkeypatch.setattr(module_cache, "update_module", refresh)

    assert discovery_module.AppBundleDiscovery().list_cached_root_bundles() == ["root"]
    cached = module_cache.scan_cached_modules()
    assert len(cached) == 4
    assert next(m for m in cached if m.cache_path == owner_path).module_type == "tool"
    statuses = asyncio.run(updater._check_all_bundle_status())
    selected = explicit if install_source != "none" else owner
    if install_source == "none":
        assert isinstance(statuses[owner], updater.TransitiveBundleStatus)
        assert not isinstance(statuses[owner], updater.DeclaredModuleStatus)
    else:
        assert isinstance(statuses[explicit], updater.DeclaredModuleStatus)
    assert str(owner_path / "embedded") not in statuses

    scenario.invoke("--check-only")
    assert not scenario.refreshed
    execute.assert_not_called()
    refresh.assert_not_called()
    output = scenario.invoke("--yes")
    assert "Update complete" in output
    assert len(scenario.refreshed) == 4
    assert scenario.refreshed.count(selected) == 1
    assert set(scenario.refreshed) == {*controls, selected}
    execute.assert_awaited_once()
    assert {
        (s.url, s.ref) for s in execute.await_args.args[0].cached_git_sources
    } == {
        (f"https://{parse_uri(uri).host}{parse_uri(uri).path}", parse_uri(uri).ref)
        for uri in controls
    }
    assert refresh.await_count == 3  # Only the distinct legacy cache identities.
    if install_source == "none":
        scenario.apply.assert_not_called()
        scenario.installs.assert_not_called()
    else:
        scenario.apply.assert_awaited_once()
        assert scenario.apply.await_args.kwargs == {
            "cache_dir": scenario.cache,
            "selective": [explicit],
            "install_deps": True,
        }
        scenario.installs.assert_awaited_once_with(scenario.active_path(explicit))
    scenario.forbidden.assert_not_called()


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        (
            "file:///C:/space%20name/manifest.yaml",
            r"C:\space name\manifest.yaml",
        ),
        (
            "file://localhost/C:/space%20name/manifest.yaml",
            r"C:\space name\manifest.yaml",
        ),
        ("file://server/share/space%20name", r"\\server\share\space name"),
    ],
)
def test_shared_file_uri_conversion_uses_explicit_windows_platform(uri, expected):
    assert file_uri_path_value(uri, windows=True) == expected
