"""Update command for Amplifier CLI."""

import asyncio

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..utils.display import create_sha_text
from ..utils.display import create_status_symbol
from ..utils.display import print_legend
from ..utils.settings_manager import save_update_last_check
from ..utils.source_status import check_all_sources
from ..utils.update_executor import execute_updates

console = Console()


def _get_installed_amplifier_packages() -> list[dict]:
    """Get details of installed Amplifier packages.

    Returns list of dicts with:
        - name: package name
        - version: installed version
        - sha: git SHA if available
        - is_local: True if installed from local path (editable or file://)
        - is_git: True if path is a git repository
        - has_changes: True if git repo has uncommitted changes
        - path: local path if applicable
        - category: 'core', 'app', or 'library'
    """
    import importlib.metadata
    import json
    import subprocess

    # Package categorization
    core_packages = {"amplifier-core"}
    app_packages = {"amplifier-app-cli"}
    # Libraries are everything else

    # Core amplifier packages to check
    package_names = [
        "amplifier-core",
        "amplifier-app-cli",
        "amplifier-profiles",
        "amplifier-collections",
        "amplifier-config",
        "amplifier-module-resolution",
    ]

    packages = []
    for name in package_names:
        try:
            dist = importlib.metadata.distribution(name)
            version = dist.version

            # Check installation type and get SHA
            sha = None
            is_local = False
            is_git = False
            has_changes = False
            path = None

            if hasattr(dist, "read_text"):
                try:
                    direct_url_text = dist.read_text("direct_url.json")
                    if direct_url_text:
                        direct_url = json.loads(direct_url_text)

                        if "dir_info" in direct_url:
                            # Local install (editable or file://)
                            is_local = True
                            path = direct_url.get("url", "").replace("file://", "")

                            # Check if it's a git repo and get status
                            if path:
                                try:
                                    # Check if git repo
                                    result = subprocess.run(
                                        ["git", "rev-parse", "--git-dir"],
                                        cwd=path,
                                        capture_output=True,
                                        text=True,
                                        timeout=5,
                                    )
                                    if result.returncode == 0:
                                        is_git = True

                                        # Get HEAD SHA
                                        result = subprocess.run(
                                            ["git", "rev-parse", "HEAD"],
                                            cwd=path,
                                            capture_output=True,
                                            text=True,
                                            timeout=5,
                                        )
                                        if result.returncode == 0:
                                            sha = result.stdout.strip()[:7]

                                        # Check for uncommitted changes
                                        result = subprocess.run(
                                            ["git", "status", "--porcelain"],
                                            cwd=path,
                                            capture_output=True,
                                            text=True,
                                            timeout=5,
                                        )
                                        if result.returncode == 0:
                                            has_changes = bool(result.stdout.strip())
                                except Exception:
                                    pass

                        elif "vcs_info" in direct_url:
                            # Git install from URL
                            sha = direct_url["vcs_info"].get("commit_id", "")[:7]
                except Exception:
                    pass

            # Determine category
            if name in core_packages:
                category = "core"
            elif name in app_packages:
                category = "app"
            else:
                category = "library"

            packages.append(
                {
                    "name": name,
                    "version": version,
                    "sha": sha,
                    "is_local": is_local,
                    "is_git": is_git,
                    "has_changes": has_changes,
                    "path": path,
                    "category": category,
                }
            )
        except importlib.metadata.PackageNotFoundError:
            continue

    return packages


async def _get_umbrella_dependency_details(umbrella_info) -> list[dict]:
    """Get details of Amplifier dependencies (libs with their SHAs).

    Returns:
        List of dicts with {name, current_sha, remote_sha, source_url}
    """
    import importlib.metadata
    import json

    from ..utils.umbrella_discovery import fetch_umbrella_dependencies

    if not umbrella_info:
        return []

    try:
        # Get dependency definitions from umbrella
        umbrella_deps = await fetch_umbrella_dependencies(umbrella_info)

        details = []
        for lib_name, dep_info in umbrella_deps.items():
            # Get current installed SHA
            current_sha = None
            try:
                dist = importlib.metadata.distribution(lib_name)
                if hasattr(dist, "read_text"):
                    direct_url_text = dist.read_text("direct_url.json")
                    if direct_url_text:
                        direct_url = json.loads(direct_url_text)
                        if "vcs_info" in direct_url:
                            current_sha = direct_url["vcs_info"].get("commit_id", "")[:7]
            except Exception:
                current_sha = "unknown"

            # Get remote SHA
            from ..utils.source_status import _get_github_commit_sha

            try:
                remote_sha_full = await _get_github_commit_sha(dep_info["url"], dep_info["branch"])
                remote_sha = remote_sha_full[:7]
            except Exception:
                remote_sha = "unknown"

            details.append(
                {
                    "name": lib_name,
                    "current_sha": current_sha,
                    "remote_sha": remote_sha,
                    "source_url": dep_info["url"],
                    "has_update": current_sha != remote_sha,
                }
            )

        return details
    except Exception:
        return []


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


def _show_concise_report(report, check_only: bool, has_umbrella_updates: bool, umbrella_deps=None) -> None:
    """Show concise table format for all sources.

    Organized by type: Core → Application → Libraries → Modules → Collections
    Uses Rich Tables with status symbols: ✓ (up to date), ● (update available), ◦ (local changes)
    """
    console.print()

    # === AMPLIFIER PACKAGES ===
    if umbrella_deps:
        # Production install - show dependencies with remote comparison
        table = Table(title="Amplifier", show_header=True, header_style="bold cyan")
        table.add_column("Package", style="green")
        table.add_column("Local", style="dim", justify="right")
        table.add_column("Remote", style="dim", justify="right")
        table.add_column("", width=1, justify="center")

        for dep in sorted(umbrella_deps, key=lambda x: x["name"]):
            status_symbol = create_status_symbol(dep["current_sha"], dep["remote_sha"])
            table.add_row(
                dep["name"],
                create_sha_text(dep["current_sha"]),
                create_sha_text(dep["remote_sha"]),
                status_symbol,
            )

        console.print(table)
    else:
        # Local install - show packages by category
        installed = _get_installed_amplifier_packages()
        if installed:
            # Separate and sort by category
            core_pkgs = sorted([p for p in installed if p["category"] == "core"], key=lambda x: x["name"])
            app_pkgs = sorted([p for p in installed if p["category"] == "app"], key=lambda x: x["name"])
            lib_pkgs = sorted([p for p in installed if p["category"] == "library"], key=lambda x: x["name"])

            # Core section
            core_table = _create_local_package_table(core_pkgs, "Core")
            if core_table:
                console.print(core_table)

            # Application section
            app_table = _create_local_package_table(app_pkgs, "Application")
            if app_table:
                console.print()
                console.print(app_table)

            # Libraries section
            lib_table = _create_local_package_table(lib_pkgs, "Libraries")
            if lib_table:
                console.print()
                console.print(lib_table)

    # === MODULES (Local overrides and/or Cached git sources) ===
    # Show local overrides first (if any)
    if report.local_file_sources:
        console.print()
        table = Table(title="Modules (Local Overrides)", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("SHA", style="dim", justify="right")
        table.add_column("Path", style="dim")
        table.add_column("", width=1, justify="center")

        for status in sorted(report.local_file_sources, key=lambda x: x.name):
            has_local_changes = status.uncommitted_changes or status.unpushed_commits
            status_symbol = create_status_symbol(status.local_sha, status.local_sha, has_local_changes)

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

    # Show cached git sources (if any)
    if report.cached_git_sources:
        console.print()
        table = Table(title="Modules (Cached)", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("Cached", style="dim", justify="right")
        table.add_column("Remote", style="dim", justify="right")
        table.add_column("", width=1, justify="center")

        for status in sorted(report.cached_git_sources, key=lambda x: x.name):
            status_symbol = create_status_symbol(status.cached_sha, status.remote_sha)

            table.add_row(
                status.name,
                create_sha_text(status.cached_sha),
                create_sha_text(status.remote_sha),
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
            status_symbol = create_status_symbol(status.installed_sha, status.remote_sha)

            table.add_row(
                status.name,
                create_sha_text(status.installed_sha),
                create_sha_text(status.remote_sha),
                status_symbol,
            )

        console.print(table)

    console.print()
    print_legend()
    if not check_only and (report.has_updates or has_umbrella_updates):
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


def _show_verbose_report(report, check_only: bool, umbrella_deps=None) -> None:
    """Show detailed multi-line format for each source (no truncation)."""

    # === AMPLIFIER PACKAGES ===
    if umbrella_deps:
        # Production install - show dependencies with remote comparison
        console.print()
        console.print("[bold cyan]Amplifier[/bold cyan]")
        console.print()

        for dep in sorted(umbrella_deps, key=lambda x: x["name"]):
            status_symbol = create_status_symbol(dep["current_sha"], dep["remote_sha"])
            _print_verbose_item(
                name=dep["name"],
                status_symbol=status_symbol,
                local_sha=dep["current_sha"],
                remote_sha=dep["remote_sha"],
                remote_url=dep.get("source_url", ""),
            )
            console.print()
    else:
        # Local install - show packages by category
        installed = _get_installed_amplifier_packages()
        if installed:
            # Separate and sort by category
            core_pkgs = sorted([p for p in installed if p["category"] == "core"], key=lambda x: x["name"])
            app_pkgs = sorted([p for p in installed if p["category"] == "app"], key=lambda x: x["name"])
            lib_pkgs = sorted([p for p in installed if p["category"] == "library"], key=lambda x: x["name"])

            # Core section
            if core_pkgs:
                console.print()
                console.print("[bold cyan]Core[/bold cyan]")
                console.print()
                for pkg in core_pkgs:
                    status_symbol = Text("◦", style="cyan") if pkg["has_changes"] else Text("✓", style="green")
                    _print_verbose_item(
                        name=pkg["name"],
                        status_symbol=status_symbol,
                        local_sha=pkg["sha"],
                        version=pkg["version"],
                        local_path=pkg["path"],
                    )
                    console.print()

            # Application section
            if app_pkgs:
                console.print("[bold cyan]Application[/bold cyan]")
                console.print()
                for pkg in app_pkgs:
                    status_symbol = Text("◦", style="cyan") if pkg["has_changes"] else Text("✓", style="green")
                    _print_verbose_item(
                        name=pkg["name"],
                        status_symbol=status_symbol,
                        local_sha=pkg["sha"],
                        version=pkg["version"],
                        local_path=pkg["path"],
                    )
                    console.print()

            # Libraries section
            if lib_pkgs:
                console.print("[bold cyan]Libraries[/bold cyan]")
                console.print()
                for pkg in lib_pkgs:
                    status_symbol = Text("◦", style="cyan") if pkg["has_changes"] else Text("✓", style="green")
                    _print_verbose_item(
                        name=pkg["name"],
                        status_symbol=status_symbol,
                        local_sha=pkg["sha"],
                        version=pkg["version"],
                        local_path=pkg["path"],
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
            modules_by_name[status.name]["remote_url"] = status.url if hasattr(status, "url") else None
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
            status_symbol = create_status_symbol(mod["local_sha"], mod["remote_sha"], mod["has_local_changes"])
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
            status_symbol = create_status_symbol(status.installed_sha, status.remote_sha)
            source_url = status.source if hasattr(status, "source") else None
            _print_verbose_item(
                name=status.name,
                status_symbol=status_symbol,
                local_sha=status.installed_sha,
                remote_sha=status.remote_sha,
                remote_url=source_url,
            )
            console.print()

    print_legend()


@click.command()
@click.option("--check-only", is_flag=True, help="Check for updates without installing")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmations")
@click.option("--force", is_flag=True, help="Force update even if already latest")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed multi-line output per source")
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
            has_umbrella_updates = asyncio.run(check_umbrella_dependencies_for_updates(umbrella_info))

    # Check modules and collections
    if not force:
        console.print("  Checking modules...")
        console.print("  Checking collections...")

    report = asyncio.run(check_all_sources(include_all_cached=True, force=force))

    # Get Amplifier dependency details
    umbrella_deps = asyncio.run(_get_umbrella_dependency_details(umbrella_info)) if umbrella_info else []

    # Display results based on verbosity
    if verbose:
        _show_verbose_report(report, check_only, umbrella_deps=umbrella_deps)
    else:
        _show_concise_report(report, check_only, has_umbrella_updates, umbrella_deps=umbrella_deps)

    # Check if anything actually needs updating
    nothing_to_update = not report.has_updates and not has_umbrella_updates and not force

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
        console.print("\nRun [cyan]amplifier update[/cyan] to install")
        return

    # Execute updates
    console.print()

    # Confirm unless --yes flag
    if not yes:
        # Show what will be updated (only count items with actual updates)
        modules_with_updates = [s for s in report.cached_git_sources if s.has_update]
        collections_with_updates = [s for s in report.collection_sources if s.has_update]

        if modules_with_updates:
            count = len(modules_with_updates)
            console.print(f"  • Update {count} cached module{'s' if count != 1 else ''}")
        if collections_with_updates:
            count = len(collections_with_updates)
            console.print(f"  • Update {count} collection{'s' if count != 1 else ''}")
        if has_umbrella_updates:
            console.print("  • Update Amplifier to latest version (dependencies have updates)")

        console.print()
        response = input("Proceed with update? [Y/n]: ").strip().lower()
        if response not in ("", "y", "yes"):
            console.print("[dim]Update cancelled[/dim]")
            return

    # Execute updates with progress
    console.print()
    console.print("Updating...")

    result = asyncio.run(execute_updates(report, umbrella_info=umbrella_info if has_umbrella_updates else None))

    # Show results
    console.print()
    if result.success:
        console.print("[green]✓ Update complete[/green]")
        for item in result.updated:
            console.print(f"  [green]✓[/green] {item}")
        for msg in result.messages:
            console.print(f"  {msg}")
    else:
        console.print("[yellow]⚠ Update completed with errors[/yellow]")
        for item in result.updated:
            console.print(f"  [green]✓[/green] {item}")
        for item in result.failed:
            error = result.errors.get(item, "Unknown error")
            console.print(f"  [red]✗[/red] {item}: {error}")

    # Update last check timestamp
    from datetime import datetime

    save_update_last_check(datetime.now())
