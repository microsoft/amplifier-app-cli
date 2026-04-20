"""Update command for Amplifier CLI."""

import asyncio
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..lib.bundle_loader import AppBundleDiscovery
from ..paths import create_bundle_registry
from ..paths import create_config_manager
from ..utils.display import create_sha_text
from ..utils.display import create_status_symbol
from ..utils.display import print_legend
from ..utils.error_format import escape_markup
from ..utils.settings_manager import save_update_last_check
from ..utils.source_status import check_all_sources
from ..utils.update_executor import execute_updates

if TYPE_CHECKING:
    from amplifier_foundation import BundleStatus

console = Console()


def _normalize_git_url(url: str) -> str:
    """Normalize git URL for comparison by stripping git+ prefix and @branch suffix.

    Examples:
        git+https://github.com/microsoft/amplifier-bundle-recipes@main
        -> https://github.com/microsoft/amplifier-bundle-recipes

        https://github.com/microsoft/amplifier-bundle-recipes
        -> https://github.com/microsoft/amplifier-bundle-recipes
    """
    if url.startswith("git+"):
        url = url[4:]
    # Don't strip @ref from file:// URLs
    if not url.startswith("file://") and "@" in url:
        url = url.split("@")[0]
    return url.rstrip("/")


def _extract_module_name_from_uri(source_uri: str) -> str:
    """Extract a clean module name from a source URI.

    Examples:
        git+https://github.com/microsoft/amplifier-module-tool-bash@main -> tool-bash
        git+https://github.com/microsoft/amplifier-bundle-recipes@main -> amplifier-bundle-recipes
    """
    # Remove git+ prefix and @ref suffix
    uri = source_uri
    if uri.startswith("git+"):
        uri = uri[4:]
    if "@" in uri:
        uri = uri.split("@")[0]

    # Get last path component
    name = uri.rstrip("/").split("/")[-1]

    # Remove .git suffix
    if name.endswith(".git"):
        name = name[:-4]

    # Remove amplifier-module- prefix for cleaner display
    if name.startswith("amplifier-module-"):
        name = name[17:]

    return name


def _collect_unified_modules(
    report,
    bundle_results: dict[str, "BundleStatus"] | None,
) -> dict[str, dict]:
    """Collect and deduplicate all module sources from cache and bundles.

    Returns:
        Dict mapping module name -> {cached_sha, remote_sha, has_update, source_uri, used_by_bundles}

    Note: Uses proper type identification via bundle_results.keys() rather than name-based detection.
    Bundle repos are NOT included here - they're handled separately using BundleStatus.
    """
    modules: dict[str, dict] = {}

    # Track normalized bundle source URIs to exclude them from modules list
    # Normalize URLs so git+https://...@main matches https://...
    bundle_source_uris: set[str] = set()
    if bundle_results:
        for bundle_status in bundle_results.values():
            if bundle_status.bundle_source:
                bundle_source_uris.add(_normalize_git_url(bundle_status.bundle_source))

    # 1. Add all cached git sources (excluding bundle repos)
    for status in report.cached_git_sources:
        name = status.name
        # Skip if this is a bundle repo (identified by normalized URL match, not name)
        if status.url and _normalize_git_url(status.url) in bundle_source_uris:
            continue

        if name not in modules:
            modules[name] = {
                "cached_sha": status.cached_sha,
                "remote_sha": status.remote_sha,
                "has_update": status.has_update,
                "source_uri": status.url,
                "used_by_bundles": set(),
            }

    # 2. Add/merge bundle sources (excluding the bundle repos themselves)
    if bundle_results:
        for bundle_name, bundle_status in bundle_results.items():
            for source in bundle_status.sources:
                # Skip any bundle repo (identified by normalized URI match)
                if _normalize_git_url(source.source_uri) in bundle_source_uris:
                    continue

                name = _extract_module_name_from_uri(source.source_uri)

                if name not in modules:
                    # New source from bundle not in cache
                    modules[name] = {
                        "cached_sha": source.cached_commit[:7]
                        if source.cached_commit
                        else None,
                        "remote_sha": source.remote_commit[:7]
                        if source.remote_commit
                        else None,
                        "has_update": source.has_update is True,
                        "source_uri": source.source_uri,
                        "used_by_bundles": set(),
                    }

                # Track which bundles use this module
                modules[name]["used_by_bundles"].add(bundle_name)

    return modules


def _get_bundle_repo_info(bundle_status: "BundleStatus") -> dict | None:
    """Extract the bundle repo's own SHA info from BundleStatus.

    Finds the SourceStatus in bundle_status.sources that matches bundle_status.bundle_source.
    Comparison is done after stripping any #fragment (e.g. #subdirectory=...) from both
    sides, so behavior bundles whose bundle_source has a subdirectory fragment still match
    the clean repo-root URI stored in source.source_uri.

    Returns:
        Dict with cached_sha, remote_sha, has_update or None if not found.
    """
    if not bundle_status.bundle_source:
        return None

    bundle_base = bundle_status.bundle_source.split("#")[0]

    for source in bundle_status.sources:
        source_base = source.source_uri.split("#")[0]
        if source_base == bundle_base:
            return {
                "cached_sha": source.cached_commit[:7]
                if source.cached_commit
                else None,
                "remote_sha": source.remote_commit[:7]
                if source.remote_commit
                else None,
                "has_update": source.has_update is True,
            }

    return None


async def _get_umbrella_dependency_details(umbrella_info) -> list[dict]:
    """Get details of all Amplifier ecosystem packages with their SHAs.

    Uses recursive discovery to find ALL packages with [tool.uv.sources] entries,
    then enriches each with local installation info and remote SHA for comparison.

    Returns:
        List of dicts with {name, local_sha, remote_sha, source_url, has_update,
                           is_local, path, has_changes}
    """
    import httpx

    from ..utils.source_status import _get_github_commit_sha
    from ..utils.umbrella_discovery import discover_ecosystem_packages
    from ..utils.umbrella_discovery import get_installed_package_info

    if not umbrella_info:
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Recursively discover all ecosystem packages
            ecosystem_packages = await discover_ecosystem_packages(
                client, umbrella_info
            )

            details = []
            for pkg in ecosystem_packages:
                # Get local installation info
                installed = get_installed_package_info(pkg.name)

                if installed:
                    local_sha = installed["sha"]
                    is_local = installed["is_local"]
                    path = installed["path"]
                    has_changes = installed["has_changes"]
                else:
                    local_sha = None
                    is_local = False
                    path = None
                    has_changes = False

                # Get remote SHA
                try:
                    remote_sha_full = await _get_github_commit_sha(
                        client, pkg.url, pkg.branch
                    )
                    remote_sha = remote_sha_full[:7]
                except Exception:
                    remote_sha = "unknown"

                # Determine if update available
                # Local installs don't compare to remote (user controls them)
                if is_local:
                    has_update = False
                elif (
                    local_sha
                    and remote_sha
                    and local_sha != "unknown"
                    and remote_sha != "unknown"
                ):
                    has_update = local_sha != remote_sha
                else:
                    has_update = False

                details.append(
                    {
                        "name": pkg.name,
                        "local_sha": local_sha,
                        "remote_sha": remote_sha,
                        "source_url": pkg.url,
                        "has_update": has_update,
                        "is_local": is_local,
                        "path": path,
                        "has_changes": has_changes,
                    }
                )

            return details
    except Exception:
        return []


def _read_bundle_file(
    uri: str, parsed: Any, cache_dir: Any, git_handler: Any
) -> str | None:
    """Read raw bundle file content from a URI (file:// or git cache).

    For file:// URIs: reads bundle.md or bundle.yaml from the local path.
    For git URIs: reads bundle.md or bundle.yaml from the local git cache.

    Args:
        uri: The bundle URI string.
        parsed: ParsedURI from parse_uri(uri).
        cache_dir: The amplifier cache directory (Path).
        git_handler: GitSourceHandler instance.

    Returns:
        Raw file content string, or None if the file cannot be read.
    """
    from pathlib import Path as _Path

    try:
        if parsed.is_file:
            # file:// URI — strip prefix to get the real filesystem path
            local_path = _Path(uri.replace("file://", ""))
            if local_path.is_dir():
                for fname in ("bundle.md", "bundle.yaml"):
                    bundle_file = local_path / fname
                    if bundle_file.exists():
                        return bundle_file.read_text(encoding="utf-8")
            elif local_path.is_file():
                return local_path.read_text(encoding="utf-8")

        elif parsed.is_git:
            # git URI — find the already-cloned cache directory.
            # GitSourceHandler._get_cache_path() computes the deterministic path.
            cache_path = git_handler._get_cache_path(parsed, cache_dir)
            if cache_path.exists():
                for fname in ("bundle.md", "bundle.yaml"):
                    bundle_file = cache_path / fname
                    if bundle_file.exists():
                        return bundle_file.read_text(encoding="utf-8")
    except Exception:
        pass

    return None


def _extract_git_sources_from_bundle_content(content: str) -> list[str]:
    """Extract git source URIs from bundle YAML frontmatter.

    Parses the bundle file (YAML frontmatter in .md files, or plain YAML in
    .yaml files) and collects ``source:`` field values from ``hooks:``,
    ``tools:``, and ``providers:`` sections that are git URIs.

    Only ``git+…`` URIs are returned; file:// and bare-path sources are
    intentionally excluded because they are user-local and handled elsewhere.

    Args:
        content: Raw content of the bundle file.

    Returns:
        Deduplicated list of git source URI strings.
    """
    import yaml as _yaml

    sources: list[str] = []
    try:
        if content.lstrip().startswith("---"):
            # Markdown bundle: YAML frontmatter between --- delimiters
            parts = content.split("---", 2)
            if len(parts) < 3:
                return sources
            frontmatter = _yaml.safe_load(parts[1])
        else:
            # Pure YAML bundle (bundle.yaml)
            frontmatter = _yaml.safe_load(content)

        if not isinstance(frontmatter, dict):
            return sources

        for section_name in ("hooks", "tools", "providers"):
            section = frontmatter.get(section_name)
            if not isinstance(section, list):
                continue
            for item in section:
                if isinstance(item, dict):
                    source = item.get("source")
                    if isinstance(source, str) and source.startswith("git+"):
                        if source not in sources:
                            sources.append(source)
    except Exception:
        pass

    return sources


async def _get_module_source_statuses(
    uri: str,
    parsed: Any,
    cache_dir: Any,
    git_handler: Any,
) -> list[Any]:
    """Discover and status-check git module sources declared inside a bundle file.

    Reads the bundle file, parses YAML frontmatter, extracts git source URIs
    from ``hooks:``, ``tools:``, and ``providers:`` sections, then calls
    ``git_handler.get_status()`` for each URI so the returned
    ``SourceStatus`` objects have accurate ``cached_commit`` / ``remote_commit``
    / ``has_update`` fields.

    These SourceStatus objects are appended to the bundle's
    ``BundleStatus.sources`` list so that ``_collect_unified_modules()`` and
    the update executor can discover, display, and cache the modules — even
    when the owning bundle is a local file:// bundle rather than a git-sourced
    bundle.

    Args:
        uri: The bundle URI string.
        parsed: ParsedURI from parse_uri(uri).
        cache_dir: The amplifier cache directory (Path).
        git_handler: GitSourceHandler instance.

    Returns:
        List of SourceStatus objects (one per discoverable git module source).
    """
    from amplifier_foundation.paths.resolution import parse_uri as _parse_uri

    content = _read_bundle_file(uri, parsed, cache_dir, git_handler)
    if not content:
        return []

    module_uris = _extract_git_sources_from_bundle_content(content)
    if not module_uris:
        return []

    statuses: list[Any] = []
    for module_uri in module_uris:
        try:
            module_parsed = _parse_uri(module_uri)
            if git_handler.can_handle(module_parsed):
                status = await git_handler.get_status(module_parsed, cache_dir)
                statuses.append(status)
        except Exception:
            continue  # Non-fatal: skip sources that cannot be status-checked

    return statuses


async def _check_all_bundle_status() -> dict[str, "BundleStatus"]:
    """Check status of all discovered bundles WITHOUT loading them.

    This checks cache status directly from URIs to avoid the side effect of
    registry.load() downloading missing bundles, which would make deleted
    caches appear as "up to date".

    For every bundle, the bundle file is also parsed to discover any git module
    sources declared in ``hooks:``, ``tools:``, or ``providers:`` sections.
    These module source statuses are appended to ``BundleStatus.sources`` so
    that downstream logic (``_collect_unified_modules()``, update executor) can
    find and cache them — this is critical for local file:// bundles whose
    modules would otherwise never be discovered.

    Returns:
        Dict mapping bundle name to BundleStatus
    """
    from amplifier_foundation.paths.resolution import get_amplifier_home
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler
    from amplifier_foundation.sources.protocol import SourceStatus
    from amplifier_foundation.updates import BundleStatus

    from ..lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES

    discovery = AppBundleDiscovery()
    registry = create_bundle_registry()
    cache_dir = get_amplifier_home() / "cache"
    git_handler = GitSourceHandler()

    # Use cached root bundles for update checking (all roots, not filtered user list)
    bundle_names = discovery.list_cached_root_bundles()
    results: dict[str, BundleStatus] = {}

    for bundle_name in bundle_names:
        try:
            # Get URI without loading (avoids download side effect)
            uri = registry.find(bundle_name)
            if not uri:
                continue

            # Check status directly from URI
            parsed = parse_uri(uri)

            # For file:// URIs (editable installs), check if this is a well-known
            # bundle with a remote URI we can use for update checking
            if parsed.is_file and bundle_name in WELL_KNOWN_BUNDLES:
                well_known_info = WELL_KNOWN_BUNDLES[bundle_name]
                remote_uri_value = well_known_info.get("remote")
                if remote_uri_value and isinstance(remote_uri_value, str):
                    # Use remote URI for status checking, but get local SHA from file path
                    source_status = await _get_file_bundle_status(
                        bundle_name, uri, remote_uri_value, git_handler, cache_dir
                    )
                    bundle_status = BundleStatus(
                        bundle_name=bundle_name,
                        bundle_source=uri,
                        sources=[source_status],
                    )
                    # Also discover any git module sources declared in this bundle
                    module_sources = await _get_module_source_statuses(
                        uri, parsed, cache_dir, git_handler
                    )
                    bundle_status.sources.extend(module_sources)
                    results[bundle_name] = bundle_status
                    continue

            if git_handler.can_handle(parsed):
                source_status: SourceStatus = await git_handler.get_status(
                    parsed, cache_dir
                )
                bundle_status = BundleStatus(
                    bundle_name=bundle_name,
                    bundle_source=uri,
                    sources=[source_status],
                )
            else:
                # Non-git bundles (local file:// user bundles) — report as unknown
                # but still extract module sources from the bundle file so that
                # git-sourced modules declared inside are discovered and cached.
                bundle_status = BundleStatus(
                    bundle_name=bundle_name,
                    bundle_source=uri,
                    sources=[
                        SourceStatus(
                            source_uri=uri,
                            is_cached=True,
                            has_update=None,
                            summary="Update checking not supported for this source type",
                        )
                    ],
                )

            # For both git and non-git bundles, discover any git module sources
            # declared inside the bundle's hooks/tools/providers sections.
            module_sources = await _get_module_source_statuses(
                uri, parsed, cache_dir, git_handler
            )
            bundle_status.sources.extend(module_sources)
            results[bundle_name] = bundle_status

        except Exception:
            continue  # Skip bundles that fail status check

    return results


async def _get_file_bundle_status(
    bundle_name: str,
    file_uri: str,
    remote_uri: str,
    git_handler: Any,
    cache_dir: Any,
) -> Any:
    """Get status for a file:// bundle by comparing local git SHA to remote.

    For well-known bundles that are editable-installed (file:// URI), we can
    still check for updates by:
    1. Getting the local git SHA from the file path
    2. Getting the remote SHA from the well-known remote URI

    This allows `amplifier update` to show meaningful version info for
    editable installs like foundation.

    Args:
        bundle_name: Name of the bundle
        file_uri: The file:// URI pointing to local path
        remote_uri: The git+ remote URI from WELL_KNOWN_BUNDLES
        git_handler: GitSourceHandler for remote checks
        cache_dir: Cache directory for git operations

    Returns:
        SourceStatus with local and remote commit info
    """
    import subprocess

    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.protocol import SourceStatus

    # Extract local path from file:// URI
    local_path = file_uri.replace("file://", "")

    # Get local git SHA
    local_sha = None
    has_local_changes = False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            local_sha = result.stdout.strip()

        # Check for uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            has_local_changes = True
    except Exception:
        pass  # Failed to get local SHA, will show as unknown

    # Get remote SHA using the git handler
    remote_sha = None
    try:
        remote_parsed = parse_uri(remote_uri)
        if git_handler.can_handle(remote_parsed):
            remote_status = await git_handler.get_status(remote_parsed, cache_dir)
            remote_sha = remote_status.remote_commit
    except Exception:
        pass  # Failed to get remote SHA, will show as unknown

    # Determine if there's an update available
    has_update = None
    if local_sha and remote_sha:
        has_update = local_sha != remote_sha

    summary = "Local editable install"
    if has_local_changes:
        summary = "Local editable install (with uncommitted changes)"

    return SourceStatus(
        source_uri=file_uri,
        is_cached=True,
        has_update=has_update,
        cached_commit=local_sha,
        remote_commit=remote_sha,
        summary=summary,
    )


def _get_active_bundle_name() -> str | None:
    """Get the name of the currently active bundle, if any."""
    config_manager = create_config_manager()
    merged = config_manager.get_merged_settings()
    bundle_settings = merged.get("bundle", {})
    return bundle_settings.get("active") if isinstance(bundle_settings, dict) else None


def _create_local_package_table(packages: list[dict], title: str) -> Table | None:
    """Create a table for local packages (core, app, or libraries).

    Returns None if no packages to display.
    """
    if not packages:
        return None

    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Package", style="green")
    table.add_column("Version", style="dim", justify="right")
    table.add_column("SHA", style="dim", justify="right")
    table.add_column("", width=1, justify="center")

    for pkg in packages:
        # Status: ◦ only if actual uncommitted changes, otherwise ✓
        if pkg["has_changes"]:
            status_symbol = Text("◦", style="cyan")
        else:
            status_symbol = Text("✓", style="green")

        # SHA display: show SHA if available, "local" if local but no git, "-" otherwise
        if pkg["sha"]:
            sha_display = create_sha_text(pkg["sha"])
        elif pkg["is_local"] and not pkg["is_git"]:
            sha_display = Text("local", style="dim")
        else:
            sha_display = Text("-", style="dim")

        table.add_row(
            pkg["name"],
            Text(pkg["version"], style="dim"),
            sha_display,
            status_symbol,
        )

    return table


def _show_concise_report(
    report,
    check_only: bool,
    has_umbrella_updates: bool,
    umbrella_deps=None,
    bundle_results: dict[str, "BundleStatus"] | None = None,
) -> None:
    """Show concise table format for all sources.

    Organized by type: Core → Application → Libraries → Modules → Collections → Bundles
    Uses Rich Tables with status symbols: ✓ (up to date), ● (update available), ◦ (local changes)
    """
    console.print()

    # === AMPLIFIER ECOSYSTEM PACKAGES ===
    if umbrella_deps:
        # Show all dynamically discovered ecosystem packages
        table = Table(title="Amplifier", show_header=True, header_style="bold cyan")
        table.add_column("Package", style="green")
        table.add_column("Local", style="dim", justify="right")
        table.add_column("Remote", style="dim", justify="right")
        table.add_column("", width=1, justify="center")

        for dep in sorted(umbrella_deps, key=lambda x: x["name"]):
            # Handle local installs specially - show path indicator
            if dep.get("is_local"):
                # Local install - show SHA with local indicator
                local_display = create_sha_text(dep["local_sha"])
                # Show path hint in name if local
                path = dep.get("path", "")
                if path:
                    # Truncate path for display
                    if len(path) > 30:
                        path = "..." + path[-27:]
                    name_display = f"{dep['name']} [dim]({path})[/dim]"
                else:
                    name_display = f"{dep['name']} [dim](local)[/dim]"
                # Local changes indicator
                status_symbol = create_status_symbol(
                    dep["local_sha"], dep["local_sha"], dep.get("has_changes", False)
                )
                remote_display = Text("-", style="dim")
            else:
                # Standard git install - compare local vs remote
                name_display = dep["name"]
                local_display = create_sha_text(dep["local_sha"])
                remote_display = create_sha_text(dep["remote_sha"])
                status_symbol = create_status_symbol(
                    dep["local_sha"], dep["remote_sha"]
                )

            table.add_row(
                name_display,
                local_display,
                remote_display,
                status_symbol,
            )

        console.print(table)

    # === MODULES (Local overrides and/or Cached git sources) ===
    # Show local overrides first (if any)
    if report.local_file_sources:
        console.print()
        table = Table(
            title="Modules (Local Overrides)",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Name", style="green")
        table.add_column("SHA", style="dim", justify="right")
        table.add_column("Path", style="dim")
        table.add_column("", width=1, justify="center")

        for status in sorted(report.local_file_sources, key=lambda x: x.name):
            has_local_changes = status.uncommitted_changes or status.unpushed_commits
            status_symbol = create_status_symbol(
                status.local_sha, status.local_sha, has_local_changes
            )

            # Truncate path for display
            path_str = str(status.path) if status.path else "-"
            if len(path_str) > 40:
                path_str = "..." + path_str[-37:]

            table.add_row(
                status.name,
                create_sha_text(status.local_sha),
                Text(path_str, style="dim"),
                status_symbol,
            )

        console.print(table)

    # === MODULES (Unified, Deduplicated) ===
    # Collect all module sources from cache and bundles, deduplicate by name
    unified_modules = _collect_unified_modules(report, bundle_results)

    # Show unified modules table (deduplicated from cache + bundle sources)
    if unified_modules:
        console.print()
        table = Table(title="Modules", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("Cached", style="dim", justify="right")
        table.add_column("Remote", style="dim", justify="right")
        table.add_column("", width=1, justify="center")

        for name in sorted(unified_modules.keys()):
            info = unified_modules[name]
            status_symbol = create_status_symbol(info["cached_sha"], info["remote_sha"])

            table.add_row(
                name,
                create_sha_text(info["cached_sha"]),
                create_sha_text(info["remote_sha"]),
                status_symbol,
            )

        console.print(table)

    # === BUNDLES (Bundle repos with their own SHA info) ===
    # Use bundle_results.keys() as authoritative list (not name-based detection)
    if bundle_results:
        console.print()
        active_bundle = _get_active_bundle_name()
        table = Table(title="Bundles", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("Cached", style="dim", justify="right")
        table.add_column("Remote", style="dim", justify="right")
        table.add_column("", width=1, justify="center")

        for bundle_name in sorted(bundle_results.keys()):
            bundle_status = bundle_results[bundle_name]
            # Get the bundle repo's own SHA info from its sources
            repo_info = _get_bundle_repo_info(bundle_status)

            if repo_info:
                cached_sha = repo_info["cached_sha"]
                remote_sha = repo_info["remote_sha"]
            else:
                # Bundle loaded but no repo info available (e.g., local bundle)
                cached_sha = None
                remote_sha = None

            # Status symbol reflects aggregate bundle state (bundle repo + ALL module sources)
            # not just the bundle repo's SHA - this makes status consistent with "Update X bundles" message
            if bundle_status.has_updates:
                status_symbol = Text("●", style="yellow")
            else:
                status_symbol = Text("✓", style="green")

            # Add "(active)" marker if this is the active bundle
            display_name = bundle_name
            if active_bundle and bundle_name == active_bundle:
                display_name = f"{bundle_name} [green](active)[/green]"

            table.add_row(
                display_name,
                create_sha_text(cached_sha),
                create_sha_text(remote_sha),
                status_symbol,
            )

        console.print(table)

    console.print()
    print_legend()

    # Determine if there are bundle updates
    has_bundle_updates = bundle_results and any(
        s.has_updates for s in bundle_results.values()
    )

    if not check_only and (
        report.has_updates or has_umbrella_updates or has_bundle_updates
    ):
        console.print()
        console.print("Run [cyan]amplifier update[/cyan] to install")


def _print_verbose_item(
    name: str,
    status_symbol: Text,
    local_sha: str | None = None,
    remote_sha: str | None = None,
    version: str | None = None,
    local_path: str | None = None,
    remote_url: str | None = None,
    ref: str | None = None,
) -> None:
    """Print a single item in verbose multi-line format."""
    # Header line: name + status
    header = Text()
    header.append(name, style="green bold")
    header.append(" ")
    header.append(status_symbol)
    if version:
        header.append(f"  v{version}", style="dim")
    console.print(header)

    # Local info line
    if local_sha or local_path:
        local_line = Text("  Local:  ", style="dim")
        if local_sha:
            local_line.append(local_sha[:7], style="cyan")
        if local_path:
            if local_sha:
                local_line.append("  ", style="dim")
            local_line.append(local_path, style="dim")
        console.print(local_line)

    # Remote info line
    if remote_sha or remote_url:
        remote_line = Text("  Remote: ", style="dim")
        if remote_sha:
            remote_line.append(remote_sha[:7], style="cyan")
        if ref:
            remote_line.append(f" ({ref})", style="dim")
        if remote_url:
            if remote_sha:
                remote_line.append("  ", style="dim")
            remote_line.append(remote_url, style="dim magenta")
        console.print(remote_line)


def _show_verbose_report(
    report,
    check_only: bool,
    umbrella_deps=None,
    bundle_results: dict[str, "BundleStatus"] | None = None,
) -> None:
    """Show detailed multi-line format for each source (no truncation)."""

    # === AMPLIFIER ECOSYSTEM PACKAGES ===
    if umbrella_deps:
        # Show all dynamically discovered ecosystem packages
        console.print()
        console.print("[bold cyan]Amplifier[/bold cyan]")
        console.print()

        for dep in sorted(umbrella_deps, key=lambda x: x["name"]):
            # Handle local installs specially
            if dep.get("is_local"):
                status_symbol = create_status_symbol(
                    dep["local_sha"], dep["local_sha"], dep.get("has_changes", False)
                )
                _print_verbose_item(
                    name=dep["name"],
                    status_symbol=status_symbol,
                    local_sha=dep["local_sha"],
                    local_path=dep.get("path"),
                )
            else:
                status_symbol = create_status_symbol(
                    dep["local_sha"], dep["remote_sha"]
                )
                _print_verbose_item(
                    name=dep["name"],
                    status_symbol=status_symbol,
                    local_sha=dep["local_sha"],
                    remote_sha=dep["remote_sha"],
                    remote_url=dep.get("source_url", ""),
                )
            console.print()
    else:
        # No umbrella info - can't discover ecosystem packages dynamically
        console.print()
        console.print(
            "[dim]No umbrella source detected - ecosystem package discovery unavailable[/dim]"
        )
        console.print(
            "[dim]This typically means Amplifier is installed in development mode.[/dim]"
        )
        console.print()

    # === MODULES ===
    # Merge local file sources and cached git sources by module name
    modules_by_name: dict[str, dict] = {}

    # Add local file sources
    for status in report.local_file_sources:
        has_local_changes = status.uncommitted_changes or status.unpushed_commits
        modules_by_name[status.name] = {
            "name": status.name,
            "local_sha": status.local_sha,
            "local_path": str(status.path) if status.path else None,
            "has_local_changes": has_local_changes,
            "remote_sha": status.remote_sha if status.has_remote else None,
            "remote_url": None,
            "ref": None,
        }

    # Merge/add cached git sources
    for status in report.cached_git_sources:
        if status.name in modules_by_name:
            # Merge remote info into existing entry
            modules_by_name[status.name]["remote_sha"] = status.remote_sha
            modules_by_name[status.name]["remote_url"] = (
                status.url if hasattr(status, "url") else None
            )
            modules_by_name[status.name]["ref"] = status.ref
        else:
            # Add new entry
            modules_by_name[status.name] = {
                "name": status.name,
                "local_sha": status.cached_sha,
                "local_path": None,
                "has_local_changes": False,
                "remote_sha": status.remote_sha,
                "remote_url": status.url if hasattr(status, "url") else None,
                "ref": status.ref,
            }

    if modules_by_name:
        console.print("[bold cyan]Modules[/bold cyan]")
        console.print()

        for mod in sorted(modules_by_name.values(), key=lambda x: x["name"]):
            status_symbol = create_status_symbol(
                mod["local_sha"], mod["remote_sha"], mod["has_local_changes"]
            )
            _print_verbose_item(
                name=mod["name"],
                status_symbol=status_symbol,
                local_sha=mod["local_sha"],
                remote_sha=mod["remote_sha"],
                local_path=mod["local_path"],
                remote_url=mod["remote_url"],
                ref=mod["ref"],
            )
            console.print()

    # === BUNDLES ===
    if bundle_results:
        active_bundle = _get_active_bundle_name()
        for bundle_name in sorted(bundle_results.keys()):
            status = bundle_results[bundle_name]
            if status.sources:
                # Add "(active)" marker if this is the active bundle
                title_suffix = " (active)" if bundle_name == active_bundle else ""
                console.print(
                    f"[bold cyan]Bundle: {bundle_name}{title_suffix}[/bold cyan]"
                )
                console.print()

                for source in sorted(status.sources, key=lambda x: x.source_uri):
                    # Extract module name from source URI for cleaner display
                    source_name = source.source_uri
                    if "/" in source_name:
                        # Get last path component, strip common prefixes
                        source_name = source_name.split("/")[-1]
                        if source_name.startswith("amplifier-module-"):
                            source_name = source_name[17:]  # Remove "amplifier-module-"
                        elif "@" in source_name:
                            source_name = source_name.split("@")[0]

                    status_symbol = create_status_symbol(
                        source.cached_commit, source.remote_commit
                    )
                    _print_verbose_item(
                        name=source_name,
                        status_symbol=status_symbol,
                        local_sha=source.cached_commit,
                        remote_sha=source.remote_commit,
                        remote_url=source.source_uri,
                    )
                    console.print()

    print_legend()


def _format_update_progress(name: str, phase: str) -> str:
    """Format an update progress callback into a human-readable label for the spinner.

    Maps update phases to user-friendly descriptions, following the same pattern
    as _format_progress() in runtime/config.py for bundle preparation.

    Args:
        name: Item being updated (module name, "amplifier", bundle name).
        phase: Progress phase (e.g., "updating", "clearing", "downloading").

    Returns:
        Human-readable progress label.
    """
    labels = {
        "updating": f"Updating {name}...",
        "clearing": f"Clearing cache for {name}...",
        "downloading": f"Downloading {name}...",
        "done": f"Updated {name}",
        "failed": f"Failed to update {name}",
        "installing": f"Installing {name} (uv tool install)...",
        "checking_deps": f"Checking {name} dependencies...",
        "updating_bundle": f"Loading bundle {name}...",
        "refreshing_bundle": f"Refreshing bundle {name}...",
    }
    label = labels.get(phase)
    if label:
        return label
    # For raw uv output lines streamed during self-update, show them directly.
    # execute_self_update passes cleaned stderr lines as the phase value
    # (see its docstring for the dual-use contract).
    if name == "amplifier" and phase:
        return f"Updating Amplifier: {phase}"
    return f"{phase}: {name}"


def _refresh_skills_cache(console: Console) -> None:
    """Refresh stale skills caches (populated by tool-skills during mount).

    Scans ~/.amplifier/cache/skills/ for directories that contain
    .amplifier_cache_meta.json metadata (written by tool-skills after a clone).
    For each, compares the cached commit SHA against the remote; if stale,
    deletes and re-clones.  Directories without metadata are left untouched for
    backward compatibility.
    """
    import json
    import logging
    import shutil
    import subprocess
    from datetime import datetime
    from pathlib import Path

    _logger = logging.getLogger(__name__)

    skills_cache_dir = Path.home() / ".amplifier" / "cache" / "skills"
    if not skills_cache_dir.exists():
        return

    refreshed = 0
    for cached_repo in skills_cache_dir.iterdir():
        if not cached_repo.is_dir():
            continue
        meta_file = cached_repo / ".amplifier_cache_meta.json"
        if not meta_file.exists():
            continue  # Legacy cache without metadata – leave untouched

        try:
            meta = json.loads(meta_file.read_text())
            git_url = meta.get("git_url", "")
            ref = meta.get("ref", "main")
            cached_commit = meta.get("commit", "")

            if not git_url or not cached_commit:
                continue

            # Check remote for latest commit SHA
            result = subprocess.run(
                ["git", "ls-remote", git_url, ref],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                continue

            remote_sha = result.stdout.split()[0] if result.stdout.strip() else ""
            if not remote_sha or remote_sha == cached_commit:
                continue  # Cache is fresh

            # Stale – delete and re-clone
            console.print(f"  Refreshing skills cache: {cached_repo.name}")
            shutil.rmtree(cached_repo)
            clone_result = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    ref,
                    git_url,
                    str(cached_repo),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if clone_result.returncode == 0:
                sha_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(cached_repo),
                    capture_output=True,
                    text=True,
                )
                new_sha = (
                    sha_result.stdout.strip() if sha_result.returncode == 0 else ""
                )
                new_meta = {
                    "cached_at": datetime.now().isoformat(),
                    "ref": ref,
                    "commit": new_sha,
                    "git_url": git_url,
                    "type": "skills",
                }
                (cached_repo / ".amplifier_cache_meta.json").write_text(
                    json.dumps(new_meta, indent=2)
                )
                refreshed += 1
                console.print(
                    f"    [green]\u2713[/green] Updated ({cached_commit[:8]} \u2192 {new_sha[:8]})"
                )
            else:
                console.print(
                    f"    [red]\u2717[/red] Clone failed: {clone_result.stderr[:100]}"
                )
        except Exception as exc:
            _logger.warning(f"Failed to refresh skills cache {cached_repo.name}: {exc}")

    if refreshed > 0:
        console.print(f"  Skills: {refreshed} cache(s) refreshed")


@click.command()
@click.option("--check-only", is_flag=True, help="Check for updates without installing")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmations")
@click.option("--force", is_flag=True, help="Force update even if already latest")
@click.option(
    "--verbose", "-v", is_flag=True, help="Show detailed multi-line output per source"
)
def update(check_only: bool, yes: bool, force: bool, verbose: bool):
    """Update Amplifier to latest version.

    Checks all sources (local files and cached git) and executes updates.
    """
    # Check for updates with status messages
    if force:
        console.print("Force update mode...")
        # Clear regenerable cache so everything is fetched fresh
        from ..utils.cache_management import clear_all_regenerable

        console.print("  Clearing cache...")
        count, success = clear_all_regenerable(dry_run=False)
        if success:
            console.print(f"  [green]✓[/green] Cleared {count} cached items")
        else:
            console.print(
                "  [yellow]Warning:[/yellow] Some cache items could not be cleared"
            )
    else:
        console.print("Checking for updates...")

    # Check umbrella first
    from ..utils.umbrella_discovery import discover_umbrella_source
    from ..utils.update_executor import check_umbrella_dependencies_for_updates

    umbrella_info = discover_umbrella_source()
    has_umbrella_updates = False

    if umbrella_info:
        if force:
            has_umbrella_updates = True  # Force update umbrella
        else:
            console.print("  Checking Amplifier dependencies...")
            has_umbrella_updates = asyncio.run(
                check_umbrella_dependencies_for_updates(umbrella_info)
            )

    # Check modules
    if not force:
        console.print("  Checking modules...")

    async def _check_sources():
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            return await check_all_sources(
                client=client, include_all_cached=True, force=force
            )

    report = asyncio.run(_check_sources())

    # Check bundles
    if not force:
        console.print("  Checking bundles...")
    bundle_results = asyncio.run(_check_all_bundle_status())
    has_bundle_updates = (
        any(s.has_updates for s in bundle_results.values()) if bundle_results else False
    )

    # Get Amplifier dependency details
    umbrella_deps = (
        asyncio.run(_get_umbrella_dependency_details(umbrella_info))
        if umbrella_info
        else []
    )

    # Display results based on verbosity
    if verbose:
        _show_verbose_report(
            report,
            check_only,
            umbrella_deps=umbrella_deps,
            bundle_results=bundle_results,
        )
    else:
        _show_concise_report(
            report,
            check_only,
            has_umbrella_updates,
            umbrella_deps=umbrella_deps,
            bundle_results=bundle_results,
        )

    # Check if anything actually needs updating
    nothing_to_update = (
        not report.has_updates
        and not has_umbrella_updates
        and not has_bundle_updates
        and not force
    )

    # Exit early if nothing to update
    if nothing_to_update:
        console.print("[green]✓ All sources up to date[/green]")
        return

    # Check-only mode (we know there ARE updates if we got here)
    if check_only:
        console.print("\n[yellow]Updates available:[/yellow]")
        if has_umbrella_updates:
            console.print("  • Amplifier (umbrella dependencies have updates)")
        if report.has_updates:
            console.print("  • Modules")
        if has_bundle_updates:
            bundles_with_updates = [
                name for name, status in bundle_results.items() if status.has_updates
            ]
            console.print(f"  • {len(bundles_with_updates)} bundle(s)")
        console.print("\nRun [cyan]amplifier update[/cyan] to install")
        return

    # Execute updates
    console.print()

    # Confirm unless --yes flag
    if not yes:
        # Show what will be updated (only count items with actual updates)
        modules_with_updates = [s for s in report.cached_git_sources if s.has_update]
        bundles_with_updates = [
            name for name, status in bundle_results.items() if status.has_updates
        ]

        if modules_with_updates:
            count = len(modules_with_updates)
            console.print(
                f"  • Update {count} cached module{'s' if count != 1 else ''}"
            )
        if bundles_with_updates:
            count = len(bundles_with_updates)
            console.print(f"  • Update {count} bundle{'s' if count != 1 else ''}")
        if has_umbrella_updates:
            console.print(
                "  • Update Amplifier to latest version (dependencies have updates)"
            )

        console.print()
        response = input("Proceed with update? [Y/n]: ").strip().lower()
        if response not in ("", "y", "yes"):
            console.print("[dim]Update cancelled[/dim]")
            return

    # Execute updates with progress spinner (same pattern as bundle preparation)
    console.print()

    spinner = console.status(
        "[dim]Updating...[/dim]",
        spinner="dots",
    )
    spinner.start()

    def _on_update_progress(name: str, phase: str) -> None:
        label = _format_update_progress(name, phase)
        spinner.update(f"[dim]{label}[/dim]")

    try:
        result = asyncio.run(
            execute_updates(
                report,
                umbrella_info=umbrella_info if has_umbrella_updates else None,
                progress_callback=_on_update_progress,
            )
        )

        # Execute bundle updates
        bundle_updated: list[str] = []
        bundle_failed: list[str] = []
        bundle_errors: dict[str, str] = {}

        if has_bundle_updates:
            from amplifier_foundation import update_bundle

            registry = create_bundle_registry()
            bundles_to_update = [
                name for name, status in bundle_results.items() if status.has_updates
            ]

            for bundle_name in bundles_to_update:
                try:
                    _on_update_progress(bundle_name, "updating_bundle")
                    loaded = asyncio.run(registry.load(bundle_name))
                    if isinstance(loaded, dict):
                        bundle_errors[bundle_name] = "Expected single bundle, got dict"
                        bundle_failed.append(bundle_name)
                        continue
                    bundle_obj = loaded

                    # Refresh bundle sources
                    _on_update_progress(bundle_name, "refreshing_bundle")
                    asyncio.run(update_bundle(bundle_obj))
                    bundle_updated.append(bundle_name)
                except Exception as exc:
                    bundle_errors[bundle_name] = str(exc)
                    bundle_failed.append(bundle_name)

        # Refresh any stale skills caches
        spinner.update("[dim]Refreshing skills caches...[/dim]")
        _refresh_skills_cache(console)
    finally:
        spinner.stop()

    # Show results
    console.print()
    # Determine overall success including bundles
    overall_success = result.success and not bundle_failed
    if overall_success:
        console.print("[green]✓ Update complete[/green]")
        for item in result.updated:
            console.print(f"  [green]✓[/green] {item}")
        for bundle_name in bundle_updated:
            console.print(f"  [green]✓[/green] Bundle: {bundle_name}")
        for msg in result.messages:
            console.print(f"  {msg}")
    else:
        console.print("[yellow]⚠ Update completed with errors[/yellow]")
        for item in result.updated:
            console.print(f"  [green]✓[/green] {item}")
        for bundle_name in bundle_updated:
            console.print(f"  [green]✓[/green] Bundle: {bundle_name}")
        for item in result.failed:
            error = result.errors.get(item, "Unknown error")
            console.print(f"  [red]✗[/red] {item}: {escape_markup(error)}")
        for bundle_name in bundle_failed:
            error = bundle_errors.get(bundle_name, "Unknown error")
            console.print(
                f"  [red]✗[/red] Bundle: {bundle_name}: {escape_markup(error)}"
            )

    # Update last check timestamp
    from datetime import datetime

    save_update_last_check(datetime.now())
