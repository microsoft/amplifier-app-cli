"""Module management commands for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from typing import Literal
from typing import cast

import click
from rich.panel import Panel
from rich.table import Table

from ..console import console
from ..data.profiles import get_system_default_profile
from ..module_manager import ModuleManager
from ..paths import create_config_manager
from ..paths import create_module_resolver
from ..paths import create_profile_loader


@click.group(invoke_without_command=True)
@click.pass_context
def module(ctx: click.Context):
    """Manage Amplifier modules."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@module.command("list")
@click.option(
    "--type",
    "-t",
    type=click.Choice(["all", "orchestrator", "provider", "tool", "agent", "context", "hook"]),
    default="all",
    help="Module type to list",
)
def list_modules(type: str):
    """List installed modules and those provided by the active profile."""
    from amplifier_core.loader import ModuleLoader

    loader = ModuleLoader()
    modules_info = asyncio.run(loader.discover())
    resolver = create_module_resolver()

    if modules_info:
        table = Table(title="Installed Modules (via entry points)", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Source", style="magenta")
        table.add_column("Origin", style="cyan")
        table.add_column("Description")

        for module_info in modules_info:
            if type != "all" and type != module_info.type:
                continue

            try:
                source_obj, origin = resolver.resolve_with_layer(module_info.id)
                source_str = str(source_obj)
                if len(source_str) > 40:
                    source_str = source_str[:37] + "..."
            except Exception:
                source_str = "unknown"
                origin = "unknown"

            table.add_row(module_info.id, module_info.type, source_str, origin, module_info.description)

        console.print(table)
    else:
        console.print("[dim]No installed modules found[/dim]")

    config_manager = create_config_manager()
    active_profile = config_manager.get_active_profile() or get_system_default_profile()

    local = config_manager._read_yaml(config_manager.paths.local)
    if local and "profile" in local and "active" in local["profile"]:
        source_label = "active"
    elif config_manager.get_project_default():
        source_label = "project default"
    else:
        source_label = "system default"

    profile_modules = _get_profile_modules(active_profile)
    if profile_modules:
        filtered = [m for m in profile_modules if type == "all" or m["type"] == type]

        if filtered:
            console.print()
            table = Table(
                title=f"Profile Modules (from profile '{active_profile}' ({source_label}))",
                show_header=True,
                header_style="bold green",
            )
            table.add_column("Name", style="green")
            table.add_column("Type", style="yellow")
            table.add_column("Source", style="magenta")

            for mod in filtered:
                source_str = str(mod["source"])
                if len(source_str) > 60:
                    source_str = source_str[:57] + "..."
                table.add_row(mod["id"], mod["type"], source_str)

            console.print(table)

    # Show cached modules (downloaded from git)
    # Filter out modules that have local source overrides (local takes precedence)
    local_override_names = _get_local_override_names()
    cached_modules = [m for m in _get_cached_modules(type) if m["id"] not in local_override_names]
    if cached_modules:
        console.print()
        table = Table(
            title="Cached Modules (downloaded from git)",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Ref", style="cyan")
        table.add_column("SHA", style="dim")
        table.add_column("Mutable", style="magenta")

        for mod in cached_modules:
            mutable_str = "yes" if mod["is_mutable"] else "no"
            table.add_row(mod["id"], mod["type"], mod["ref"], mod["sha"], mutable_str)

        console.print(table)
        console.print()
        console.print("[dim]Note: Cached modules are downloaded on-demand when used.[/dim]")
        console.print("[dim]Use 'amplifier module update' to update cached modules.[/dim]")


@module.command("show")
@click.argument("module_name")
def module_show(module_name: str):
    """Show detailed information about a module."""
    from amplifier_core.loader import ModuleLoader

    config_manager = create_config_manager()
    active_profile = config_manager.get_active_profile() or get_system_default_profile()

    profile_modules = _get_profile_modules(active_profile)
    found_in_profile = next((m for m in profile_modules if m["id"] == module_name), None)

    if found_in_profile:
        source = found_in_profile["source"]
        description = found_in_profile.get("description", "No description provided")
        mount_point = found_in_profile.get("mount_point", "unknown")

        panel_content = f"""[bold]Name:[/bold] {module_name}
[bold]Type:[/bold] {found_in_profile["type"]}
[bold]Source:[/bold] {source}
[bold]Description:[/bold] {description}
[bold]Mount Point:[/bold] {mount_point}"""
        console.print(Panel(panel_content, title=f"Module: {module_name}", border_style="cyan"))
        return

    loader = ModuleLoader()
    modules_info = asyncio.run(loader.discover())
    found_module = next((m for m in modules_info if m.id == module_name), None)

    if not found_module:
        console.print(f"[red]Module '{module_name}' not found in profile or installed modules[/red]")
        return

    panel_content = f"""[bold]Name:[/bold] {found_module.id}
[bold]Type:[/bold] {found_module.type}
[bold]Description:[/bold] {found_module.description}
[bold]Mount Point:[/bold] {found_module.mount_point}
[bold]Version:[/bold] {found_module.version}
[bold]Origin:[/bold] Installed (entry point)"""

    console.print(Panel(panel_content, title=f"Module: {module_name}", border_style="cyan"))


@module.command("add")
@click.argument("module_id")
@click.option("--source", "-s", help="Source URI (git+https://... or file path)")
@click.option("--local", "scope_flag", flag_value="local", help="Add locally (just you)")
@click.option("--project", "scope_flag", flag_value="project", help="Add for project (team)")
@click.option("--global", "scope_flag", flag_value="global", help="Add globally (all projects)")
def module_add(module_id: str, source: str | None, scope_flag: str | None):
    """Add a module override to settings.

    MODULE_ID should follow naming convention: provider-*, tool-*, hooks-*, etc.
    Use --source to specify where to load the module from.
    """
    # Infer module type from ID prefix
    module_type: Literal["tool", "hook", "agent", "provider", "orchestrator", "context"] | None = None
    if module_id.startswith("tool-"):
        module_type = "tool"
    elif module_id.startswith("hooks-"):
        module_type = "hook"
    elif module_id.startswith("agent-"):
        module_type = "agent"
    elif module_id.startswith("provider-"):
        module_type = "provider"
    elif module_id.startswith("loop-"):
        module_type = "orchestrator"
    elif module_id.startswith("context-"):
        module_type = "context"
    else:
        console.print("[red]Error:[/red] Module ID must start with a known prefix")
        console.print("\nSupported prefixes:")
        console.print("  provider-*     (LLM providers: provider-anthropic, provider-openai)")
        console.print("  tool-*         (Tools: tool-filesystem, tool-bash)")
        console.print("  hooks-*        (Hooks: hooks-logging, hooks-approval)")
        console.print("  agent-*        (Agent configs: agent-custom)")
        console.print("  loop-*         (Orchestrators: loop-basic, loop-streaming)")
        console.print("  context-*      (Context managers: context-simple, context-persistent)")
        console.print("\nExamples:")
        console.print("  amplifier module add provider-anthropic --source git+https://github.com/org/repo@main")
        console.print("  amplifier module add tool-jupyter")
        return

    if not scope_flag:
        console.print("\nAdd for:")
        console.print("  [1] Just you (local)")
        console.print("  [2] Whole team (project)")
        console.print("  [3] All your projects (global)")
        choice = click.prompt("Choice", type=click.Choice(["1", "2", "3"]), default="1")
        scope_map: dict[str, Literal["local", "project", "global"]] = {"1": "local", "2": "project", "3": "global"}
        scope = scope_map[choice]
    else:
        scope = cast(Literal["local", "project", "global"], scope_flag)

    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)
    result = module_mgr.add_module(module_id, module_type, scope, source=source)  # type: ignore[arg-type]

    console.print(f"[green]✓ Added {module_id}[/green]")

    # Download the module if it's a git source
    if source and source.startswith("git+"):
        from amplifier_module_resolution.sources import GitSource

        console.print("  Downloading module...", end="")
        try:
            git_source = GitSource.from_uri(source)
            git_source.resolve()  # Downloads to cache
            console.print(" [green]✓[/green]")
        except Exception as e:
            console.print(" [yellow]⚠[/yellow]")
            console.print(f"  [yellow]Warning: Could not download module: {e}[/yellow]")
            console.print("  [dim]Module will be downloaded on first use.[/dim]")
    console.print(f"  Type: {module_type}")
    console.print(f"  Scope: {scope}")
    if source:
        console.print(f"  Source: {source}")
    console.print(f"  File: {result.file}")


@module.command("remove")
@click.argument("module_id")
@click.option("--local", "scope_flag", flag_value="local", help="Remove from local")
@click.option("--project", "scope_flag", flag_value="project", help="Remove from project")
@click.option("--global", "scope_flag", flag_value="global", help="Remove from global")
def module_remove(module_id: str, scope_flag: str | None):
    """Remove a module override from settings."""

    if not scope_flag:
        console.print("\nRemove from:")
        console.print("  [1] Just you (local)")
        console.print("  [2] Whole team (project)")
        console.print("  [3] All your projects (global)")
        choice = click.prompt("Choice", type=click.Choice(["1", "2", "3"]), default="1")
        scope_map: dict[str, Literal["local", "project", "global"]] = {"1": "local", "2": "project", "3": "global"}
        scope = scope_map[choice]
    else:
        scope = cast(Literal["local", "project", "global"], scope_flag)

    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)
    module_mgr.remove_module(module_id, scope)  # type: ignore[arg-type]

    console.print(f"[green]✓ Removed {module_id} from {scope}[/green]")


@module.command("current")
def module_current():
    """Display modules configured in settings overrides."""
    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)
    modules = module_mgr.get_current_modules()

    if not modules:
        console.print("[yellow]No modules configured in settings[/yellow]")
        console.print("\nAdd modules with:")
        console.print("  [cyan]amplifier module add <module-id>[/cyan]")
        return

    table = Table(title="Currently Configured Modules (from settings)")
    table.add_column("Module", style="green")
    table.add_column("Type", style="yellow")
    table.add_column("Source", style="cyan")

    for mod in modules:
        table.add_row(mod.module_id, mod.module_type, mod.source)

    console.print(table)
    console.print("\n[dim]Note: This shows modules added via settings.[/dim]")
    console.print("[dim]For all installed modules, use: amplifier module list[/dim]")


def _get_profile_modules(profile_name: str) -> list[dict[str, Any]]:
    """Return module metadata for a profile."""
    loader = create_profile_loader()
    try:
        profile = loader.load_profile(profile_name)
    except Exception:
        return []

    modules: list[dict[str, Any]] = []

    def add_module(module, module_type: str):
        if module is None:
            return
        modules.append(
            {
                "id": module.module,
                "type": module_type,
                "source": module.source or "profile",
                "config": module.config or {},
                "description": getattr(module, "description", "No description"),
                "mount_point": getattr(module, "mount_point", "unknown"),
            }
        )

    for provider in profile.providers:
        add_module(provider, "provider")
    for tool in profile.tools:
        add_module(tool, "tool")
    for hook in profile.hooks:
        add_module(hook, "hook")
    if profile.session:
        add_module(profile.session.orchestrator, "orchestrator")
        add_module(profile.session.context, "context")

    return modules


def _get_local_override_names() -> set[str]:
    """Get names of modules that have local source overrides.

    These modules use FileSource and should take precedence over cached versions.
    """
    from amplifier_module_resolution import FileSource

    resolver = create_module_resolver()
    local_names: set[str] = set()

    # Check all cached modules to see which have local overrides
    from ..utils.module_cache import scan_cached_modules

    for module in scan_cached_modules():
        try:
            source, _layer = resolver.resolve_with_layer(module.module_id)
            if isinstance(source, FileSource):
                local_names.add(module.module_id)
        except Exception:
            pass

    return local_names


def _get_cached_modules(type_filter: str = "all") -> list[dict[str, Any]]:
    """Return metadata for all cached modules.

    Uses centralized scan_cached_modules() utility and converts to dict format
    for backward compatibility with existing display code.
    """
    from ..utils.module_cache import scan_cached_modules

    modules = scan_cached_modules(type_filter)
    return [
        {
            "id": m.module_id,
            "type": m.module_type,
            "ref": m.ref,
            "sha": m.sha,
            "cached_at": m.cached_at,
            "is_mutable": m.is_mutable,
            "url": m.url,
        }
        for m in modules
    ]


@module.command("update")
@click.argument("module_id", required=False)
@click.option("--check-only", is_flag=True, help="Check for updates without installing")
@click.option("--mutable-only", is_flag=True, help="Only update mutable refs (branches, not tags/SHAs)")
def module_update(module_id: str | None, check_only: bool, mutable_only: bool):
    """Update module cache.

    Clears cached git modules and re-downloads them immediately.
    Useful for updating modules pinned to branches (e.g., @main).

    Use --check-only to see available updates without installing.
    """
    from ..utils.display import show_modules_report
    from ..utils.module_cache import clear_module_cache
    from ..utils.module_cache import find_cached_module
    from ..utils.module_cache import get_cache_dir
    from ..utils.module_cache import update_module
    from ..utils.source_status import check_all_sources

    cache_dir = get_cache_dir()

    if not cache_dir.exists():
        console.print("[yellow]No module cache found[/yellow]")
        console.print("Modules will download on next use")
        return

    # Check-only mode: show status using shared display utilities
    if check_only:
        console.print("Checking for updates...")
        report = asyncio.run(check_all_sources(include_all_cached=True))
        show_modules_report(report.cached_git_sources, report.local_file_sources, check_only=True)
        return

    if module_id:
        # Update specific module - find it first to get URL and ref
        cached_module = find_cached_module(module_id)

        if not cached_module:
            console.print(f"[yellow]No cached module found for '{module_id}'[/yellow]")
            return

        # Check mutable-only flag
        if mutable_only and not cached_module.is_mutable:
            console.print(f"[dim]Skipping {module_id} - immutable ref (tag/SHA)[/dim]")
            return

        # Update: clear + immediate re-download
        console.print(f"Updating {module_id}@{cached_module.ref}...")
        try:
            update_module(
                url=cached_module.url,
                ref=cached_module.ref,
                progress_callback=lambda mid, status: console.print(f"  {status}...", end="\r"),
            )
            console.print(f"[green]✓ Updated {module_id}@{cached_module.ref}[/green]")
        except Exception as e:
            console.print(f"[red]✗ Failed to update {module_id}: {e}[/red]")
    else:
        # Update all modules - clear cache, modules will re-download on next use
        # For "all modules" case, we just clear (immediate re-download would take too long)
        cleared, skipped = clear_module_cache(mutable_only=mutable_only)

        console.print(f"[green]✓ Cleared {cleared} cached modules[/green]")
        if skipped > 0:
            console.print(f"[dim]Skipped {skipped} immutable refs (tags/SHAs)[/dim]")
        console.print("Modules will re-download on next use")


@module.command("validate")
@click.argument("module_path", type=click.Path(exists=True))
@click.option(
    "--type",
    "-t",
    "module_type",
    type=click.Choice(["provider", "tool", "hook", "orchestrator", "context"]),
    help="Module type (auto-detected from name if not specified)",
)
@click.option(
    "--output",
    "-o",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show additional details and actionable tips for failed checks",
)
@click.option(
    "--behavioral",
    "-b",
    is_flag=True,
    default=False,
    help="Run behavioral tests in addition to structural validation",
)
def module_validate(module_path: str, module_type: str | None, output_format: str, verbose: bool, behavioral: bool):
    """Validate a module against its contract.

    MODULE_PATH should be a path to a module directory.
    Module type is auto-detected from directory name (e.g., provider-*, tool-*, hooks-*).

    Use --behavioral to run pytest-based behavioral tests after structural validation.
    """
    asyncio.run(_module_validate_async(module_path, module_type, output_format, verbose, behavioral))


async def _module_validate_async(
    module_path: str, module_type: str | None, output_format: str, verbose: bool, behavioral: bool
):
    """Async implementation of module validate."""
    from amplifier_core.validation import ContextValidator
    from amplifier_core.validation import HookValidator
    from amplifier_core.validation import OrchestratorValidator
    from amplifier_core.validation import ProviderValidator
    from amplifier_core.validation import ToolValidator

    path = Path(module_path).resolve()

    # Auto-detect module type from directory name if not specified
    if module_type is None:
        module_type = _infer_module_type_for_validation(path.name)
        if module_type is None:
            console.print("[red]Could not auto-detect module type from directory name.[/red]")
            console.print("Use --type flag to specify: provider, tool, hook, orchestrator, or context")
            raise SystemExit(1)

    # Select validator
    validators = {
        "provider": ProviderValidator,
        "tool": ToolValidator,
        "hook": HookValidator,
        "orchestrator": OrchestratorValidator,
        "context": ContextValidator,
    }
    validator = validators[module_type]()

    # Run validation
    result = await validator.validate(path)

    # Output
    if output_format == "json":
        print(
            json.dumps(
                {
                    "module_type": result.module_type,
                    "module_path": result.module_path,
                    "passed": result.passed,
                    "checks": [
                        {
                            "name": c.name,
                            "passed": c.passed,
                            "message": c.message,
                            "severity": c.severity,
                        }
                        for c in result.checks
                    ],
                },
                indent=2,
            )
        )
    else:
        _display_validation_result(result, verbose=verbose)

    # Exit with error code if structural validation failed
    if not result.passed:
        raise SystemExit(1)

    # Run behavioral tests if requested
    if behavioral:
        behavioral_result = _run_behavioral_tests(str(path), module_type)
        if not behavioral_result:
            raise SystemExit(1)


def _run_behavioral_tests(module_path: str, module_type: str) -> bool:
    """Run pytest behavioral tests for a module.

    Args:
        module_path: Path to module directory
        module_type: Type of module (provider, tool, hook, orchestrator, context)

    Returns:
        True if tests passed, False otherwise
    """
    import subprocess

    console.print()
    console.print("[bold]Running behavioral tests...[/bold]")

    # Find the behavioral test file - look in amplifier-core package
    try:
        import amplifier_core

        core_path = Path(amplifier_core.__file__).parent
        test_file = core_path / "validation" / "behavioral" / f"test_{module_type}.py"

        if not test_file.exists():
            console.print(f"[yellow]No behavioral tests found for {module_type} modules[/yellow]")
            return True  # Not a failure - tests just don't exist yet

    except ImportError:
        console.print("[red]amplifier-core not installed - cannot run behavioral tests[/red]")
        return False

    # Run pytest with the module path
    cmd = [
        "pytest",
        str(test_file),
        f"--module-path={module_path}",
        "-v",
        "--tb=short",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        console.print("[green]✓ Behavioral tests passed[/green]")
        if result.stdout:
            console.print(result.stdout)
        return True

    console.print("[red]✗ Behavioral tests failed[/red]")
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(f"[red]{result.stderr}[/red]")
    return False


def _infer_module_type_for_validation(name: str) -> str | None:
    """Infer module type from directory/module name for validation."""
    prefixes = {
        "provider-": "provider",
        "tool-": "tool",
        "hooks-": "hook",
        "loop-": "orchestrator",
        "context-": "context",
    }
    for prefix, mod_type in prefixes.items():
        if prefix in name:
            return mod_type
    return None


def _display_validation_result(result, verbose: bool = False):
    """Display validation result with Rich formatting.

    Args:
        result: ValidationResult from the validator
        verbose: If True, show actionable tips for failed checks
    """
    # Summary header
    status_color = "green" if result.passed else "red"
    console.print(
        Panel(
            f"[{status_color}]{result.summary()}[/{status_color}]",
            title=f"Validation: {result.module_type}",
            subtitle=result.module_path,
        )
    )

    # Detailed checks table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Message")

    for check in result.checks:
        if check.passed:
            status = "[green]PASS[/green]"
        elif check.severity == "warning":
            status = "[yellow]WARN[/yellow]"
        else:
            status = "[red]FAIL[/red]"
        table.add_row(check.name, status, check.message)

    console.print(table)

    # Verbose mode: show actionable tips for failed checks
    if verbose and not result.passed:
        console.print()
        console.print("[dim]─── Actionable Tips ───[/dim]")
        for check in result.checks:
            if not check.passed:
                tip = _get_actionable_tip_for_check(check.name, result.module_type)
                if tip:
                    console.print(f"[dim]• {check.name}:[/dim] {tip}")


def _get_actionable_tip_for_check(check_name: str, module_type: str) -> str | None:
    """Generate an actionable tip based on the failed check name and module type."""
    check_lower = check_name.lower()

    # Common tips based on check patterns
    if "mount" in check_lower:
        return "Ensure your module exports an async mount(coordinator, config) function in __init__.py"

    if "package" in check_lower or "structure" in check_lower:
        return "Check that the module has a valid pyproject.toml and __init__.py"

    if "export" in check_lower:
        return "Verify that required exports are present in the module's __init__.py"

    if "signature" in check_lower:
        return f"Check that function signatures match the {module_type} contract"

    if "protocol" in check_lower or "compliance" in check_lower:
        return f"Ensure your module implements all required methods from the {module_type} protocol"

    if "entry" in check_lower or "entrypoint" in check_lower:
        return "Verify that pyproject.toml defines the correct entry point under [project.entry-points]"

    if "import" in check_lower:
        return "Check that the module has a valid __init__.py file in the package directory"

    # Module type specific tips
    if module_type == "provider" and "model" in check_lower:
        return "Provider must implement get_info() and list_models() methods"

    if module_type == "tool" and "execute" in check_lower:
        return "Tool must implement async execute(input) -> ToolResult method"

    if module_type == "hook" and "call" in check_lower:
        return "Hook must implement __call__(event, data) -> HookResult method"

    return None


__all__ = ["module"]
