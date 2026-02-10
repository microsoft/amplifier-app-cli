"""Reset command for Amplifier CLI.

Provides interactive reset with category-based preservation.
Uninstalls amplifier, clears selected data, and reinstalls fresh.

Categories:
    projects  - Session transcripts and history
    settings  - User configuration (settings.yaml)
    keys      - API keys (keys.env)
    cache     - Downloaded bundles (auto-regenerates)
    registry  - Bundle mappings (auto-regenerates)

Example:
    # Interactive mode (default)
    amplifier reset

    # Scripted usage
    amplifier reset --preserve projects,settings,keys -y
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click

from ..console import console
from .reset_interactive import ChecklistItem, run_checklist


# Category definitions: category name -> list of files/dirs in ~/.amplifier
# Note: "other" is a special dynamic category - see _get_other_files()
RESET_CATEGORIES = {
    "projects": ["projects"],
    "settings": ["settings.yaml"],
    "keys": ["keys.env"],
    "cache": ["cache"],
    "registry": ["registry.json"],
    "other": [],  # Dynamic - populated at runtime with uncategorized files
}

# Display order for categories
CATEGORY_ORDER = ["projects", "settings", "keys", "cache", "registry", "other"]

# Descriptions for each category (used in UI)
CATEGORY_DESCRIPTIONS = {
    "projects": "Session transcripts and history",
    "settings": "User configuration (settings.yaml)",
    "keys": "API keys (keys.env)",
    "cache": "Downloaded bundles (auto-regenerates)",
    "registry": "Bundle mappings (auto-regenerates)",
    "other": "Other files (custom configs, plugins, etc.)",
}

# Default categories to preserve - safe by default, only remove auto-regenerating items
DEFAULT_PRESERVE = {"projects", "settings", "keys", "other"}

# Default install source
DEFAULT_INSTALL_SOURCE = "git+https://github.com/microsoft/amplifier"


def _get_amplifier_dir() -> Path:
    """Get the ~/.amplifier directory path."""
    return Path.home() / ".amplifier"


def _get_known_files() -> set[str]:
    """Get all file/directory names covered by non-dynamic categories."""
    known = set()
    for category, files in RESET_CATEGORIES.items():
        if category != "other":  # Skip dynamic category
            known.update(files)
    return known


def _get_other_files() -> list[str]:
    """Get list of files in ~/.amplifier not covered by any category.

    These are user-created files like custom configs, plugins, etc.
    Returns empty list if directory doesn't exist.
    """
    amplifier_dir = _get_amplifier_dir()
    if not amplifier_dir.exists():
        return []

    known = _get_known_files()
    other = []
    for item in amplifier_dir.iterdir():
        if item.name not in known:
            other.append(item.name)
    return sorted(other)


def _get_preserve_paths(preserve: set[str]) -> set[str]:
    """Convert category names to actual file/directory names."""
    paths = set()
    for category in preserve:
        if category == "other":
            # Dynamic category - include all uncategorized files
            paths.update(_get_other_files())
        elif category in RESET_CATEGORIES:
            paths.update(RESET_CATEGORIES[category])
    return paths


def _parse_categories(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> set[str] | None:
    """Parse comma-separated category list with validation."""
    if value is None:
        return None
    categories = {c.strip() for c in value.split(",") if c.strip()}
    valid = set(RESET_CATEGORIES.keys())
    invalid = categories - valid
    if invalid:
        raise click.BadParameter(
            f"Invalid categories: {', '.join(sorted(invalid))}. "
            f"Valid categories: {', '.join(CATEGORY_ORDER)}"
        )
    return categories


def _run_interactive() -> set[str] | None:
    """Run the interactive checklist for category selection.

    Returns:
        Set of category names to preserve, or None if cancelled
    """
    # Build checklist items with defaults
    items = []
    for category in CATEGORY_ORDER:
        description = CATEGORY_DESCRIPTIONS.get(category, "")
        selected = category in DEFAULT_PRESERVE
        items.append(
            ChecklistItem(key=category, description=description, selected=selected)
        )

    return run_checklist(items, title="Amplifier Reset")


def _show_plan(
    preserve: set[str],
    no_install: bool,
    dry_run: bool,
) -> None:
    """Print the reset plan."""
    amplifier_dir = _get_amplifier_dir()

    if dry_run:
        console.print("[yellow]DRY RUN - No changes will be made[/yellow]\n")

    # Upfront reassurance about what's safe
    if "projects" in preserve:
        console.print(
            "[green]Your session transcripts are safe[/green] - "
            "projects/ will be preserved.\n"
        )

    console.print("[bold]Reset Plan:[/bold]")
    console.print("  1. Clean UV cache")
    console.print("  2. Uninstall amplifier (if installed)")

    preserve_names = sorted(preserve) if preserve else []
    remove_names = sorted(set(RESET_CATEGORIES.keys()) - preserve)

    # Show what "other" actually contains if present
    other_files = _get_other_files()

    if not preserve:
        console.print(f"  3. Remove {amplifier_dir} [red](ALL contents)[/red]")
    else:
        console.print(f"  3. Clean parts of {amplifier_dir}")
        # Build preserve display with "other" expansion
        preserve_display = []
        for name in preserve_names:
            if name == "other" and other_files:
                preserve_display.append(f"other ({', '.join(other_files)})")
            else:
                preserve_display.append(name)
        console.print(
            f"       [green]Preserving:[/green] {', '.join(preserve_display)}"
        )
        console.print(f"       [red]Removing:[/red] {', '.join(remove_names)}")

    if no_install:
        console.print("  4. [dim]Skip reinstall (--no-install)[/dim]")
    else:
        console.print(f"  4. Reinstall amplifier from: {DEFAULT_INSTALL_SOURCE}")

    console.print()


def _clean_uv_cache(dry_run: bool = False) -> bool:
    """Run 'uv cache clean' to purge the UV package cache."""
    console.print("[bold]>>>[/bold] Cleaning UV cache...")

    if dry_run:
        console.print("    [dim][dry-run] Would run: uv cache clean[/dim]")
        return True

    try:
        subprocess.run(
            ["uv", "cache", "clean"],
            check=True,
            capture_output=True,
            timeout=60,  # Safeguard timeout
        )
        return True
    except subprocess.TimeoutExpired:
        console.print("[yellow]Warning:[/yellow] UV cache clean timed out")
        return False
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning:[/yellow] Failed to clean UV cache: {e}")
        return False
    except FileNotFoundError:
        console.print("[yellow]Warning:[/yellow] uv not found, skipping cache clean")
        return False


def _uninstall_amplifier(dry_run: bool = False) -> bool:
    """Uninstall amplifier via uv tool uninstall."""
    console.print("[bold]>>>[/bold] Checking if amplifier is installed...")

    # Check if amplifier is installed
    try:
        result = subprocess.run(
            ["uv", "tool", "list"],
            check=True,
            capture_output=True,
            text=True,
        )
        if "amplifier" not in result.stdout:
            console.print("    [dim]Amplifier is not installed via uv tool[/dim]")
            return False
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("    [dim]Could not check uv tool list[/dim]")
        return False

    console.print("[bold]>>>[/bold] Uninstalling amplifier...")

    if dry_run:
        console.print("    [dim][dry-run] Would run: uv tool uninstall amplifier[/dim]")
        return True

    try:
        subprocess.run(
            ["uv", "tool", "uninstall", "amplifier"],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning:[/yellow] Failed to uninstall amplifier: {e}")
        return False


def _remove_amplifier_dir(preserve: set[str], dry_run: bool = False) -> bool:
    """Remove ~/.amplifier directory contents based on category preservation.

    Uses shared cache_management utilities for cache/registry removal when those
    categories are being removed, ensuring DRY compliance across commands.
    """
    # Try to use shared utilities, but fall back to inline removal if not available
    # (handles case where reset runs with old code before new module exists)
    clear_download_cache = None
    clear_registry = None
    try:
        from ..utils.cache_management import clear_download_cache
        from ..utils.cache_management import clear_registry
    except ImportError:
        pass  # Will use inline shutil.rmtree fallback

    amplifier_dir = _get_amplifier_dir()
    console.print(f"[bold]>>>[/bold] Removing {amplifier_dir}...")

    if not amplifier_dir.exists():
        console.print("    [dim]Directory does not exist, skipping[/dim]")
        return True

    # Convert categories to actual paths
    preserve_paths = _get_preserve_paths(preserve)

    # If nothing to preserve, remove entire directory
    if not preserve_paths:
        if dry_run:
            console.print(
                f"    [dim][dry-run] Would remove entire directory: {amplifier_dir}[/dim]"
            )
            return True

        try:
            shutil.rmtree(amplifier_dir)
            console.print("    [green]Removed entire directory[/green]")
            return True
        except OSError as e:
            from ..utils.error_format import format_error_message

            console.print(
                f"[red]Error:[/red] Failed to remove {amplifier_dir}: {format_error_message(e)}"
            )
            return False

    # Selective removal - preserve specified paths
    removed_count = 0
    preserved_count = 0
    clearing_cache = "cache" not in preserve_paths

    try:
        for item in amplifier_dir.iterdir():
            if item.name in preserve_paths:
                console.print(f"    [green]Preserving:[/green] {item.name}")
                preserved_count += 1
            else:
                if dry_run:
                    console.print(f"    [dim][dry-run] Would remove: {item.name}[/dim]")
                    removed_count += 1
                else:
                    # Use shared utilities for cache and registry if available
                    if clear_download_cache is not None and item.name == "cache":
                        _count, success = clear_download_cache(dry_run=False)
                        if success:
                            removed_count += 1
                    elif clear_registry is not None and item.name == "registry.json":
                        if clear_registry(dry_run=False):
                            removed_count += 1
                    else:
                        # Standard removal (fallback or other items)
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                        removed_count += 1

        # CRITICAL: Clear install-state.json when cache is being removed
        # The install state tracks module dependency fingerprints. When cache is cleared,
        # modules are removed but install-state.json persists. On next run, the state
        # says "installed" but packages are gone → import errors.
        # This fixes Issue #11: tool-web missing aiohttp after upgrade.
        # TODO: Consider consolidating with InstallStateManager from amplifier-foundation.
        # See: amplifier_foundation.modules.install_state
        if clearing_cache and not dry_run:
            from amplifier_app_cli.paths import get_install_state_path

            install_state_file = get_install_state_path()
            if install_state_file.exists():
                install_state_file.unlink()
                console.print("    [dim]Cleared install state[/dim]")
        elif clearing_cache and dry_run:
            console.print("    [dim][dry-run] Would clear install-state.json[/dim]")

        action = "Would remove" if dry_run else "Removed"
        console.print(
            f"    {action} {removed_count} items, preserved {preserved_count}"
        )
        return True
    except OSError as e:
        from ..utils.error_format import format_error_message

        console.print(
            f"[yellow]Warning:[/yellow] Error during cleanup: {format_error_message(e)}"
        )
        return False


def _install_amplifier(dry_run: bool = False) -> bool:
    """Install amplifier via uv tool install."""
    console.print(
        f"[bold]>>>[/bold] Installing amplifier from {DEFAULT_INSTALL_SOURCE}..."
    )

    if dry_run:
        console.print(
            f"    [dim][dry-run] Would run: uv tool install {DEFAULT_INSTALL_SOURCE}[/dim]"
        )
        return True

    try:
        subprocess.run(
            ["uv", "tool", "install", DEFAULT_INSTALL_SOURCE],
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        from ..utils.error_format import format_error_message

        console.print(
            f"[red]Error:[/red] Failed to install amplifier: {format_error_message(e)}"
        )
        console.print("\n[yellow]To recover manually:[/yellow]")
        console.print(f"  uv tool install {DEFAULT_INSTALL_SOURCE}")
        return False
    except FileNotFoundError:
        console.print("[red]Error:[/red] uv not found")
        return False


@click.command()
@click.option(
    "--preserve",
    "preserve_cats",
    callback=_parse_categories,
    metavar="LIST",
    help="Comma-separated categories to preserve (e.g., projects,settings,keys)",
)
@click.option(
    "--remove",
    "remove_cats",
    callback=_parse_categories,
    metavar="LIST",
    help="Comma-separated categories to remove (e.g., cache,registry)",
)
@click.option(
    "--full",
    is_flag=True,
    help="Remove everything including projects (nuclear option)",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip interactive prompt",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview what would be removed without making changes",
)
@click.option(
    "--no-install",
    is_flag=True,
    help="Uninstall only, don't reinstall",
)
def reset(
    preserve_cats: set[str] | None,
    remove_cats: set[str] | None,
    full: bool,
    yes: bool,
    dry_run: bool,
    no_install: bool,
) -> None:
    """Reinstall Amplifier while preserving your data.

    Safe by default: Your session transcripts, settings, API keys, and any
    custom files are preserved. Only the cache and registry are cleared
    (they auto-regenerate on next run).

    Runs in interactive mode by default where you can adjust what to keep.

    \b
    Categories:
      projects   - Session transcripts and history [preserved by default]
      settings   - User configuration (settings.yaml) [preserved by default]
      keys       - API keys (keys.env) [preserved by default]
      other      - Custom files you've added [preserved by default]
      cache      - Downloaded bundles (auto-regenerates)
      registry   - Bundle mappings (auto-regenerates)

    \b
    Examples:
      amplifier reset                      Interactive mode (recommended)
      amplifier reset -y                   Quick reset with safe defaults
      amplifier reset --dry-run            Preview what would happen
      amplifier reset --remove cache -y    Remove only cache
      amplifier reset --full -y            Remove everything (use with caution)
    """
    # Check for mutually exclusive options
    exclusive_count = sum(
        [
            preserve_cats is not None,
            remove_cats is not None,
            full,
        ]
    )
    if exclusive_count > 1:
        raise click.UsageError(
            "Options --preserve, --remove, and --full are mutually exclusive"
        )

    # Determine preserve set based on arguments
    preserve: set[str]

    if full:
        preserve = set()
    elif remove_cats is not None:
        preserve = set(RESET_CATEGORIES.keys()) - remove_cats
    elif preserve_cats is not None:
        preserve = preserve_cats
    elif yes:
        # Non-interactive with -y but no category flags: use defaults
        preserve = DEFAULT_PRESERVE.copy()
    else:
        # Interactive mode
        result = _run_interactive()
        if result is None:
            console.print("[yellow]Cancelled.[/yellow]")
            return
        preserve = result

    # Show plan
    _show_plan(preserve, no_install, dry_run)

    # Confirm unless -y or dry-run
    if not yes and not dry_run:
        if not click.confirm("Proceed?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    # Execute reset steps
    _clean_uv_cache(dry_run)
    _uninstall_amplifier(dry_run)
    _remove_amplifier_dir(preserve, dry_run)

    if dry_run:
        console.print("\n[green]>>>[/green] Dry run complete - no changes were made")
        return

    # Reinstall if not skipped
    if not no_install:
        if not _install_amplifier(dry_run):
            return

    console.print("\n[green]>>>[/green] Reset complete!")
