"""Update command for Amplifier CLI."""

import asyncio
from typing import TYPE_CHECKING

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

    Returns:
        Dict with cached_sha, remote_sha, has_update or None if not found.
    """
    if not bundle_status.bundle_source:
        return None

    for source in bundle_status.sources:
        if source.source_uri == bundle_status.bundle_source:
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


async def _check_all_bundle_status() -> dict[str, "BundleStatus"]:
    """Check status of all discovered bundles WITHOUT loading them.

    This checks cache status directly from URIs to avoid the side effect of
    registry.load() downloading missing bundles, which would make deleted
    caches appear as "up to date".

    Returns:
        Dict mapping bundle name to BundleStatus
    """
    from amplifier_foundation.paths.resolution import get_amplifier_home
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler
    from amplifier_foundation.sources.protocol import SourceStatus
    from amplifier_foundation.updates import BundleStatus

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
            if git_handler.can_handle(parsed):
                source_status: SourceStatus = await git_handler.get_status(
                    parsed, cache_dir
                )
                results[bundle_name] = BundleStatus(
                    bundle_name=bundle_name,
                    bundle_source=uri,
                    sources=[source_status],
                )
            else:
                # Non-git bundles - report as unknown
                results[bundle_name] = BundleStatus(
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
        except Exception:
            continue  # Skip bundles that fail status check

    return results


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

    # === COLLECTIONS ===
    if report.collection_sources:
        console.print()
        table = Table(title="Collections", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("Installed", style="dim", justify="right")
        table.add_column("Remote", style="dim", justify="right")
        table.add_column("", width=1, justify="center")

        for status in sorted(report.collection_sources, key=lambda x: x.name):
            status_symbol = create_status_symbol(
                status.installed_sha, status.remote_sha
            )

            table.add_row(
                status.name,
                create_sha_text(status.installed_sha),
                create_sha_text(status.remote_sha),
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

    # === COLLECTIONS ===
    if report.collection_sources:
        console.print("[bold cyan]Collections[/bold cyan]")
        console.print()

        for status in sorted(report.collection_sources, key=lambda x: x.name):
            status_symbol = create_status_symbol(
                status.installed_sha, status.remote_sha
            )
            source_url = status.source if hasattr(status, "source") else None
            _print_verbose_item(
                name=status.name,
                status_symbol=status_symbol,
                local_sha=status.installed_sha,
                remote_sha=status.remote_sha,
                remote_url=source_url,
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
        console.print("Force update mode - skipping update detection...")
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

    # Check modules and collections
    if not force:
        console.print("  Checking modules...")
        console.print("  Checking collections...")

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
            console.print("  • Modules and/or collections")
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
        collections_with_updates = [
            s for s in report.collection_sources if s.has_update
        ]
        bundles_with_updates = [
            name for name, status in bundle_results.items() if status.has_updates
        ]

        if modules_with_updates:
            count = len(modules_with_updates)
            console.print(
                f"  • Update {count} cached module{'s' if count != 1 else ''}"
            )
        if collections_with_updates:
            count = len(collections_with_updates)
            console.print(f"  • Update {count} collection{'s' if count != 1 else ''}")
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

    # Execute updates with progress
    console.print()
    console.print("Updating...")

    result = asyncio.run(
        execute_updates(
            report, umbrella_info=umbrella_info if has_umbrella_updates else None
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
                loaded = asyncio.run(registry.load(bundle_name))
                if isinstance(loaded, dict):
                    bundle_errors[bundle_name] = "Expected single bundle, got dict"
                    bundle_failed.append(bundle_name)
                    continue
                bundle_obj = loaded

                # Refresh bundle sources
                asyncio.run(update_bundle(bundle_obj))
                bundle_updated.append(bundle_name)
            except Exception as exc:
                bundle_errors[bundle_name] = str(exc)
                bundle_failed.append(bundle_name)

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
            console.print(f"  [red]✗[/red] {item}: {error}")
        for bundle_name in bundle_failed:
            error = bundle_errors.get(bundle_name, "Unknown error")
            console.print(f"  [red]✗[/red] Bundle: {bundle_name}: {error}")

    # Update last check timestamp
    from datetime import datetime

    save_update_last_check(datetime.now())
