"""Reset command for Amplifier CLI.

Provides interactive reset with category-based removal selection.
Uninstalls amplifier, clears selected data, and reinstalls fresh.

Categories:
    projects  - Session transcripts and history
    settings  - User configuration (settings.yaml)
    keys      - API keys (keys.env)
    cache     - Downloaded bundles (auto-regenerates)
    registry  - Bundle mappings (auto-regenerates)

Anything in ~/.amplifier that is not named by a category above is *not managed
by reset*: selective cleanup never touches it, and the plan lists it so it is
visible rather than implied. Only --full, which removes the directory outright,
takes unmanaged state with it.

That rule exists because the category list is a static description of a
directory other components keep adding to. It previously had a catch-all
"other" category that expanded, at runtime, to every unrecognised entry - one
checkbox that on a real machine covered 76% of ~/.amplifier, including state
belonging to components this module has never heard of. A taxonomy that goes
stale by default must fail towards keeping data, not towards deleting it.

Example:
    # Interactive mode (default)
    amplifier reset

    # Scripted usage
    amplifier reset --preserve projects,settings,keys -y
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click

from ..console import console
from ..utils.error_format import escape_markup
from ..utils.fs_utils import rmtree_robust
from ..utils.uv_utils import UvStep, defer_uv_tool_swap
from ..utils.uv_utils import remove_stale_uv_lock as _remove_stale_uv_lock
from .reset_interactive import ChecklistItem, run_checklist

# Category definitions: category name -> list of files/dirs in ~/.amplifier.
# Every entry is an explicit, static path. There is deliberately no dynamic
# catch-all: an entry that appears here is one someone decided reset owns.
RESET_CATEGORIES = {
    "projects": ["projects"],
    "settings": ["settings.yaml"],
    "keys": ["keys.env"],
    "cache": ["cache"],
    "registry": ["registry.json"],
}

# Display order for categories
CATEGORY_ORDER = ["projects", "settings", "keys", "cache", "registry"]

# Descriptions for each category (used in UI)
CATEGORY_DESCRIPTIONS = {
    "projects": "Session transcripts and history",
    "settings": "User configuration (settings.yaml)",
    "keys": "API keys (keys.env)",
    "cache": "Downloaded bundles (auto-regenerates)",
    "registry": "Bundle mappings (auto-regenerates)",
}

# Retired category. Kept only so the parser can explain what happened rather
# than emitting a bare "invalid category" for a spelling that used to work.
_RETIRED_CATEGORIES = {"other"}

# Default categories to remove - safe by default, only remove auto-regenerating items
DEFAULT_REMOVE = {"cache", "registry"}

# Default install source
DEFAULT_INSTALL_SOURCE = "git+https://github.com/microsoft/amplifier"

# The current umbrella source registers `amplifier`; older installations
# registered the CLI distribution directly. Both expose the `amplifier`
# executable and must be removed before a reset's replacement install. Neither
# name may be hardcoded at a call site - which one is registered is a runtime
# fact, read back via _installed_uv_tool_packages().
UV_TOOL_PACKAGES = ("amplifier", "amplifier-app-cli")


def _get_amplifier_dir() -> Path:
    """Get the ~/.amplifier directory path."""
    return Path.home() / ".amplifier"


def _get_known_files() -> set[str]:
    """Get all file/directory names covered by a category."""
    known = set()
    for files in RESET_CATEGORIES.values():
        known.update(files)
    return known


def _get_unmanaged_files() -> list[str]:
    """List entries in ~/.amplifier that no category claims.

    These belong to components reset does not model - other modules' state,
    credentials they store outside keys.env, anything added since this
    taxonomy was written. Selective cleanup never removes them; this exists so
    the plan can *show* what is being left alone. Returns an empty list if the
    directory does not exist.
    """
    amplifier_dir = _get_amplifier_dir()
    if not amplifier_dir.exists():
        return []

    known = _get_known_files()
    return sorted(
        item.name for item in amplifier_dir.iterdir() if item.name not in known
    )


def _get_remove_paths(remove_cats: set[str]) -> set[str]:
    """Convert category names to actual file/directory names.

    Only ever expands to statically declared category paths. Nothing here
    reads the directory, so an entry reset does not know about cannot be
    selected for removal by any category.
    """
    paths = set()
    for category in remove_cats:
        paths.update(RESET_CATEGORIES.get(category, []))
    return paths


def _parse_categories(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> set[str] | None:
    """Parse comma-separated category list with validation."""
    if value is None:
        return None
    categories = {c.strip() for c in value.split(",") if c.strip()}

    # An empty --remove is a harmless no-op, but the mirrored empty --preserve
    # means "preserve nothing" - it removes every category, projects included,
    # and -y suppresses the confirm. That is the same blast radius as --full
    # reached by an unset shell variable, so it has to be asked for by name.
    if not categories and param.name == "preserve_cats":
        raise click.BadParameter(
            "--preserve was given an empty value, which would remove every "
            "category including projects. Pass the categories to keep, or use "
            "--full if removing everything is what you want."
        )

    valid = set(RESET_CATEGORIES.keys())
    retired = categories & _RETIRED_CATEGORIES
    if retired:
        raise click.BadParameter(
            f"The '{', '.join(sorted(retired))}' category was removed. It "
            "expanded at runtime to every unrecognised entry in ~/.amplifier, "
            "so it swept state belonging to components reset does not model. "
            "Files outside the named categories are now always preserved; use "
            "--full to remove the directory outright."
        )

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
        Set of category names to remove, or None if cancelled
    """
    # Build checklist items with defaults
    items = []
    for category in CATEGORY_ORDER:
        description = CATEGORY_DESCRIPTIONS.get(category, "")
        selected = category in DEFAULT_REMOVE
        items.append(
            ChecklistItem(key=category, description=description, selected=selected)
        )

    return run_checklist(items, title="Amplifier Reset")


def _show_plan(
    remove_cats: set[str],
    full: bool,
    no_install: bool,
    dry_run: bool,
) -> None:
    """Print the reset plan."""
    amplifier_dir = _get_amplifier_dir()

    if dry_run:
        console.print("[yellow]DRY RUN - No changes will be made[/yellow]\n")

    # Upfront reassurance about what's safe
    if not full and "projects" not in remove_cats:
        console.print(
            "[green]Your session transcripts are safe[/green] - "
            "projects/ will be preserved.\n"
        )

    console.print("[bold]Reset Plan:[/bold]")
    console.print("  1. Clean UV cache")
    console.print("  2. Uninstall amplifier (if installed)")

    remove_names = [category for category in CATEGORY_ORDER if category in remove_cats]
    preserve_names = [
        category for category in CATEGORY_ORDER if category not in remove_cats
    ]

    if full:
        console.print(f"  3. Remove {amplifier_dir} [red](ALL contents)[/red]")
        unmanaged = _get_unmanaged_files()
        if unmanaged:
            # --full is the only path that takes unmanaged state with it, so
            # it is the one place the user has to be told by name.
            console.print(
                "       [red]Including state reset does not manage:[/red] "
                f"{', '.join(unmanaged)}"
            )
    else:
        console.print(f"  3. Clean parts of {amplifier_dir}")
        console.print(f"       [red]Removing:[/red] {', '.join(remove_names) or 'none'}")
        console.print(
            f"       [green]Preserving:[/green] {', '.join(preserve_names) or 'none'}"
        )
        unmanaged = _get_unmanaged_files()
        if unmanaged:
            console.print(
                "       [green]Preserving (not managed by reset):[/green] "
                f"{', '.join(unmanaged)}"
            )

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

    # Remove orphaned lock file before attempting cache clean.
    # A stale uv.lock (from a killed uv process) causes 'uv cache clean'
    # to hang indefinitely waiting to acquire the lock.
    _remove_stale_uv_lock()

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
        console.print(
            f"[yellow]Warning:[/yellow] Failed to clean UV cache: {escape_markup(str(e))}"
        )
        return False
    except FileNotFoundError:
        console.print("[yellow]Warning:[/yellow] uv not found, skipping cache clean")
        return False


def _installed_uv_tool_packages() -> tuple[str, ...] | None:
    """Report which known uv tool distributions are currently registered.

    Both entries of ``UV_TOOL_PACKAGES`` expose the same ``amplifier``
    executable, so the name to uninstall cannot be assumed - it has to be read
    back from uv. Matching is anchored to ``"<package> "`` at the start of a
    line so the indented ``- amplifier`` executable rows listed underneath a
    package never count as a package of their own.

    Returns:
        The registered distribution names, possibly empty, or None when
        ``uv tool list`` could not be consulted at all.
    """
    try:
        result = subprocess.run(
            ["uv", "tool", "list"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    lines = result.stdout.splitlines()
    return tuple(
        package
        for package in UV_TOOL_PACKAGES
        if any(line.startswith(f"{package} ") for line in lines)
    )


def _uninstall_amplifier(dry_run: bool = False) -> bool:
    """Uninstall amplifier via uv tool uninstall."""
    console.print("[bold]>>>[/bold] Checking if amplifier is installed...")

    installed_packages = _installed_uv_tool_packages()
    if installed_packages is None:
        console.print("    [dim]Could not check uv tool list[/dim]")
        return False
    if not installed_packages:
        console.print("    [dim]Amplifier is not installed via uv tool[/dim]")
        return False

    console.print("[bold]>>>[/bold] Uninstalling amplifier...")

    if dry_run:
        for package in installed_packages:
            console.print(
                f"    [dim][dry-run] Would run: uv tool uninstall {package}[/dim]"
            )
        return True

    success = True
    for package in installed_packages:
        try:
            subprocess.run(
                ["uv", "tool", "uninstall", package],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            console.print(
                "[yellow]Warning:[/yellow] Failed to uninstall "
                f"{package}: {escape_markup(str(e))}"
            )
            success = False
    return success


def _remove_amplifier_dir(
    remove_cats: set[str], full: bool = False, dry_run: bool = False
) -> bool:
    """Remove paths from the selected reset categories.

    Whole-directory removal is driven by ``full``, never inferred from the
    category set. Selecting every category still only removes the paths those
    categories name, because the directory holds state no category models and
    naming five things is not a request to delete a sixth.

    Uses shared cache_management utilities for cache/registry removal when those
    categories are being removed, ensuring DRY compliance across commands.
    """
    # Try to use shared utilities, but fall back to inline removal if not available
    # (handles case where reset runs with old code before new module exists)
    clear_download_cache = None
    clear_registry = None
    try:
        from ..utils.cache_management import clear_download_cache, clear_registry
    except ImportError:
        pass  # Will use inline rmtree_robust fallback

    amplifier_dir = _get_amplifier_dir()

    # The shared utilities resolve ~/.amplifier independently, via
    # cache_management.get_amplifier_dir(). Only delegate to them when that
    # answer matches the directory actually being cleaned - otherwise they
    # would clear the real cache and registry while this loop removes paths
    # from somewhere else entirely, which is a silent cross-directory delete
    # rather than a redirected one.
    if clear_download_cache is not None or clear_registry is not None:
        from ..utils.cache_management import get_amplifier_dir as _shared_dir

        if _shared_dir() != amplifier_dir:
            clear_download_cache = None
            clear_registry = None

    console.print(f"[bold]>>>[/bold] Removing {amplifier_dir}...")

    if not amplifier_dir.exists():
        console.print("    [dim]Directory does not exist, skipping[/dim]")
        return True

    # --full is the only way to reach whole-directory removal: it is the one
    # request that explicitly includes state reset does not manage. An empty
    # category set is deliberately *not* the inverse of this case; it is a
    # data-cleanup no-op.
    if full:
        if dry_run:
            console.print(
                f"    [dim][dry-run] Would remove entire directory: {amplifier_dir}[/dim]"
            )
            return True

        try:
            rmtree_robust(amplifier_dir)
            console.print("    [green]Removed entire directory[/green]")
            return True
        except OSError as e:
            from ..utils.error_format import format_error_message

            console.print(
                f"[red]Error:[/red] Failed to remove {amplifier_dir}: {format_error_message(e)}"
            )
            return False

    # Expand only the selected categories. This must not iterate the root to
    # decide what to delete; "other" is the one explicit dynamic expansion.
    remove_paths = _get_remove_paths(remove_cats)

    # Selective removal deletes only the explicit paths selected above.
    removed_count = 0
    failed_items: list[str] = []
    clearing_cache = "cache" in remove_cats

    try:
        for path_name in sorted(remove_paths):
            item = amplifier_dir / path_name
            if not item.exists() and not item.is_symlink():
                continue

            if dry_run:
                console.print(f"    [dim][dry-run] Would remove: {item.name}[/dim]")
                removed_count += 1
                continue

            # Never let rmtree follow a selected directory symlink.
            if item.is_symlink():
                item.unlink()
                removed_count += 1
            # Use shared utilities for cache and registry if available.
            elif clear_download_cache is not None and item.name == "cache":
                _count, success = clear_download_cache(dry_run=False)
                if success:
                    removed_count += 1
                else:
                    failed_items.append(item.name)
            elif clear_registry is not None and item.name == "registry.json":
                if clear_registry(dry_run=False):
                    removed_count += 1
                else:
                    failed_items.append(item.name)
            # Standard removal (fallback or other items).
            elif item.is_dir():
                rmtree_robust(item)
                removed_count += 1
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
        console.print(f"    {action} {removed_count} items")
        if failed_items:
            console.print(
                "[red]Error:[/red] Cleanup incomplete; failed to remove: "
                f"{', '.join(sorted(failed_items))}"
            )
            return False
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
            f"    [dim][dry-run] Would run: uv tool install --force {DEFAULT_INSTALL_SOURCE}[/dim]"
        )
        return True

    try:
        subprocess.run(
            # `--force` is narrowly about replacing an existing executable in
            # uv's bin directory. Reset explicitly owns the `amplifier`
            # executable, and this repairs an orphan left by an interrupted or
            # historically mis-targeted uninstall; resolution/install errors
            # still fail this command normally.
            ["uv", "tool", "install", "--force", DEFAULT_INSTALL_SOURCE],
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        from ..utils.error_format import format_error_message

        console.print(
            f"[red]Error:[/red] Failed to install amplifier: {format_error_message(e)}"
        )
        console.print("\n[yellow]To recover manually:[/yellow]")
        console.print(f"  uv tool install --force {DEFAULT_INSTALL_SOURCE}")
        return False
    except FileNotFoundError:
        console.print("[red]Error:[/red] uv not found")
        return False


def _windows_defer_tool_swap(no_install: bool) -> bool:
    """Hand the uv tool uninstall/reinstall to a script that runs after we exit.

    See ``defer_uv_tool_swap`` for why Windows needs this at all. Reset's shape
    is an uninstall, followed (unless ``--no-install``) by a fresh install.

    Returns:
        True only when the deferred script was launched successfully.
    """
    # Which distribution is registered cannot be assumed here any more than it
    # can on POSIX: the umbrella source registers `amplifier`, older installs
    # registered `amplifier-app-cli`, and uninstalling the name the user does
    # *not* have is what strands them with the tool still installed.
    detected = _installed_uv_tool_packages()
    if detected is None:
        # uv could not be read. Cover every known distribution best-effort
        # rather than betting on one name; an uninstall for a distribution that
        # is not registered is a no-op, missing the registered one is not.
        uninstall_packages = list(UV_TOOL_PACKAGES)
        uninstall_required = False
    else:
        uninstall_packages = list(detected)
        uninstall_required = True

    if no_install:
        if not uninstall_packages:
            console.print("    [dim]Amplifier is not installed via uv tool[/dim]")
            return True

        steps = [
            UvStep(
                command=f"uv tool uninstall {package}",
                label=f"Uninstalling {package}...",
                required=uninstall_required,
            )
            for package in uninstall_packages
        ]
        recovery = [f"uv tool uninstall {package}" for package in uninstall_packages]
        success = "Amplifier removed."
    else:
        steps = [
            # Best effort: the forced reinstall replaces an orphaned
            # `amplifier` executable if the uninstall could not complete.
            # Aborting here is what would leave the user with no amplifier.
            UvStep(
                command=f"uv tool uninstall {package}",
                label=f"Uninstalling {package} (best effort)...",
                attempts=5,
                required=False,
            )
            for package in uninstall_packages
        ]
        steps.append(
            UvStep(
                command=f"uv tool install --force {DEFAULT_INSTALL_SOURCE}",
                label="Reinstalling amplifier...",
            )
        )
        recovery = [
            *(f"uv tool uninstall {package}" for package in uninstall_packages),
            f"uv tool install --force {DEFAULT_INSTALL_SOURCE}",
        ]
        success = "Amplifier reset complete."

    launched = defer_uv_tool_swap(
        steps,
        operation="reset",
        intro_lines=[
            "Amplifier reset: finishing the uninstall/reinstall now that the app",
            "has exited. Windows locks a running program's own files.",
        ],
        success_message=success,
        recovery_commands=recovery,
    )

    if not launched:
        console.print("    Run these yourself once Amplifier has exited:")
        for command in recovery:
            console.print(f"      {command}")
        console.print(
            "\n[red]>>>[/red] Reset was not staged; no deferred tool changes will run."
        )
        return False

    console.print("\n[green]>>>[/green] Reset staged - it will finish after this exits.")
    return True


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
    """Reinstall Amplifier and reset selected data.

    Safe by default: Your session transcripts, settings, API keys, and any
    custom files are preserved. Only the cache and registry are cleared
    (they auto-regenerate on next run).

    Runs in interactive mode by default where you select what to remove/reset.

    \b
    Categories:
      projects   - Session transcripts and history [not removed by default]
      settings   - User configuration (settings.yaml) [not removed by default]
      keys       - API keys (keys.env) [not removed by default]
      cache      - Downloaded bundles (auto-regenerates) [removed by default]
      registry   - Bundle mappings (auto-regenerates) [removed by default]

    \b
    Anything else in ~/.amplifier belongs to components reset does not model
    and is always preserved. Only --full removes it, and the plan names it
    first.

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

    # Determine the removal plan. --preserve remains a compatibility boundary
    # adapter; every downstream operation receives the removal set.
    all_categories = set(RESET_CATEGORIES)

    if full:
        remove_cats = all_categories
    elif remove_cats is not None:
        pass
    elif preserve_cats is not None:
        remove_cats = all_categories - preserve_cats
    elif yes:
        # Non-interactive with -y but no category flags: use defaults
        remove_cats = DEFAULT_REMOVE.copy()
    else:
        # Interactive mode
        remove_cats = _run_interactive()
        if remove_cats is None:
            console.print("[yellow]Cancelled.[/yellow]")
            return

    # Show plan
    _show_plan(remove_cats, full, no_install, dry_run)

    # Confirm unless -y or dry-run
    if not yes and not dry_run:
        if not click.confirm("Proceed?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    # Execute reset steps. Cache clean and ~/.amplifier cleanup are safe to run
    # in-process on every OS (neither touches the running uv tool environment).
    _clean_uv_cache(dry_run)

    # Windows self-modification guard: on Windows the live amplifier.exe and its
    # loaded .dll/.pyd under the uv tool env are OS-locked, so an in-process
    # `uv tool uninstall`/`install` fails with "Access is denied (os error 5)".
    # Do the safe cleanup now, then defer the tool-env swap to a script that
    # runs after this process exits. POSIX unlinks open files, so the path below
    # is unchanged there.
    if os.name == "nt" and not dry_run:
        if not _remove_amplifier_dir(remove_cats, full=full, dry_run=dry_run):
            raise click.ClickException(
                "Reset stopped because cleanup was incomplete; "
                "no reinstall was staged."
            )
        if not _windows_defer_tool_swap(no_install):
            raise click.ClickException("Reset could not be staged.")
        return

    _uninstall_amplifier(dry_run)
    cleanup_succeeded = _remove_amplifier_dir(remove_cats, full=full, dry_run=dry_run)

    if dry_run:
        console.print("\n[green]>>>[/green] Dry run complete - no changes were made")
        return

    # Reinstall if not skipped
    install_succeeded = True
    if not no_install:
        install_succeeded = _install_amplifier(dry_run)

    if not cleanup_succeeded:
        if no_install:
            recovery_status = "No reinstall was requested."
        elif install_succeeded:
            recovery_status = "Amplifier was reinstalled for recovery."
        else:
            recovery_status = "The reinstall recovery also failed."
        raise click.ClickException(
            f"Reset cleanup was incomplete. {recovery_status}"
        )

    if not install_succeeded:
        # The uninstall above already ran, so a failed reinstall leaves the user
        # with no amplifier at all. Exiting 0 here would report that as success
        # and hide it from any script or CI step driving the reset.
        raise click.ClickException(
            "Reset removed Amplifier but the reinstall failed; "
            "Amplifier is not currently installed."
        )

    console.print("\n[green]>>>[/green] Reset complete!")
