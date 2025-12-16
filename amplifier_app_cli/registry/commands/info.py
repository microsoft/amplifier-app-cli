"""Info command - show detailed information about a module from the registry."""

from __future__ import annotations

import json

import click
from rich.panel import Panel

from ...console import console
from ..client import RegistryClient


@click.command("info")
@click.argument("module_name")
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON",
)
def info_command(module_name: str, output_json: bool):
    """Show detailed information about a module from the registry.

    This shows information from the central registry.
    For installed modules, use 'amplifier module show <name>'.

    Example:

        amplifier module info code-reviewer
    """
    client = RegistryClient()

    try:
        # Fetch module
        module = client.get_module(module_name)

        if not module:
            console.print(f"[red]Module '{module_name}' not found in registry[/red]")

            # Try to suggest similar modules
            all_modules = client.list_modules()
            similar = [
                m["name"]
                for m in all_modules
                if module_name.lower() in m["name"].lower() or m["name"].lower() in module_name.lower()
            ]

            if similar:
                console.print("\n[yellow]Did you mean:[/yellow]")
                for name in similar[:5]:  # Show max 5 suggestions
                    console.print(f"  - {name}")

            console.print("\n[dim]Browse all modules:[/dim]")
            console.print("  [cyan]amplifier module registry[/cyan]")
            return

        # JSON output
        if output_json:
            print(json.dumps(module, indent=2))
            return

        # Human-readable output
        name = module["name"]
        version = module.get("version", "unknown")
        verified = module.get("verified", False)

        # Build title
        title = f"{name} ({version})"
        if verified:
            title = f"✓ {title} [Verified]"

        # Build content sections
        content_parts = []

        # Description
        desc = module.get("description", "No description provided")
        content_parts.append(f"[bold]Description:[/bold]\n{desc}")

        # Author
        author = module.get("author", "Unknown")
        author_github = module.get("author_github", "")
        if author_github:
            author_line = f"{author} ([cyan]{author_github}[/cyan])"
        else:
            author_line = author
        content_parts.append(f"\n[bold]Author:[/bold] {author_line}")

        # License
        license_name = module.get("license", "Not specified")
        content_parts.append(f"[bold]License:[/bold] {license_name}")

        # Repository
        repo = module.get("repository", "")
        if repo:
            content_parts.append(f"[bold]Repository:[/bold] [cyan]{repo}[/cyan]")

        # Type and entry point
        module_type = module.get("type", "unknown")
        content_parts.append(f"\n[bold]Type:[/bold] {module_type}")

        entry_point = module.get("entry_point", "")
        if entry_point:
            content_parts.append(f"[bold]Entry Point:[/bold] [dim]{entry_point}[/dim]")

        # Compatibility
        compatibility = module.get("compatibility", {})
        if compatibility:
            content_parts.append("\n[bold]Compatibility:[/bold]")
            if "foundation" in compatibility:
                content_parts.append(f"  Foundation: {compatibility['foundation']}")
            if "python" in compatibility:
                content_parts.append(f"  Python: {compatibility['python']}")

        # Tags
        tags = module.get("tags", [])
        if tags:
            tags_str = ", ".join(tags)
            content_parts.append(f"\n[bold]Tags:[/bold] [dim]{tags_str}[/dim]")

        # Installation instructions
        content_parts.append(f"\n[bold]Installation:[/bold]")
        content_parts.append(f"  [cyan]amplifier module add {name}[/cyan]")

        content = "\n".join(content_parts)

        # Render panel
        console.print()
        console.print(Panel(content, title=title, border_style="cyan", padding=(1, 2)))
        console.print()

    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to fetch module info: {e}")
        console.print("\n[dim]Possible solutions:[/dim]")
        console.print("  1. Check your internet connection")
        console.print("  2. Verify the module name is correct")
        console.print("  3. Try again in a few moments")


def info_command_func(module_name: str, output_json: bool):
    """Wrapper function for calling from code."""
    info_command.callback(module_name=module_name, output_json=output_json)
