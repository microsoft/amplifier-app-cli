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

        # Build content sections - now showing ALL fields dynamically
        content_parts = []

        # Priority fields first (in specific order for better readability)
        priority_fields = {
            "description": ("Description", lambda v: v),
            "author": ("Author", lambda v: v),
            "author_github": ("Author GitHub", lambda v: f"[cyan]{v}[/cyan]"),
            "license": ("License", lambda v: v),
            "repository": ("Repository", lambda v: f"[cyan]{v}[/cyan]"),
            "type": ("Type", lambda v: v),
            "module_type": ("Module Type", lambda v: v),
            "entry_point": ("Entry Point", lambda v: f"[dim]{v}[/dim]"),
        }

        # Display priority fields
        for field, (label, formatter) in priority_fields.items():
            if field in module and module[field]:
                # Combine author and author_github into one line
                if field == "author_github":
                    continue  # Skip, handled with author
                elif field == "author":
                    author = module.get("author", "Unknown")
                    author_github = module.get("author_github", "")
                    if author_github:
                        author_line = f"{author} ([cyan]{author_github}[/cyan])"
                    else:
                        author_line = author
                    content_parts.append(f"\n[bold]{label}:[/bold] {author_line}")
                else:
                    value = formatter(module[field])
                    content_parts.append(f"\n[bold]{label}:[/bold] {value}")

        # Special handling for complex fields
        # Compatibility
        if "compatibility" in module and module["compatibility"]:
            content_parts.append("\n[bold]Compatibility:[/bold]")
            compatibility = module["compatibility"]
            for key, value in compatibility.items():
                content_parts.append(f"  {key.title()}: {value}")

        # Tags
        if "tags" in module and module["tags"]:
            tags_str = ", ".join(module["tags"])
            content_parts.append(f"\n[bold]Tags:[/bold] [dim]{tags_str}[/dim]")

        # Dependencies
        if "dependencies" in module and module["dependencies"]:
            content_parts.append("\n[bold]Dependencies:[/bold]")
            if isinstance(module["dependencies"], list):
                for dep in module["dependencies"]:
                    content_parts.append(f"  - {dep}")
            elif isinstance(module["dependencies"], dict):
                for dep_name, dep_version in module["dependencies"].items():
                    content_parts.append(f"  - {dep_name}: {dep_version}")

        # Now display all remaining fields not yet shown
        displayed_fields = set(priority_fields.keys()) | {"name", "version", "verified", "compatibility", "tags", "dependencies"}

        remaining_fields = sorted(set(module.keys()) - displayed_fields)
        if remaining_fields:
            content_parts.append("\n[bold]Additional Information:[/bold]")
            for field in remaining_fields:
                value = module[field]
                # Format the field name nicely
                field_label = field.replace("_", " ").title()

                # Format the value based on type
                if isinstance(value, dict):
                    content_parts.append(f"  {field_label}:")
                    for k, v in value.items():
                        content_parts.append(f"    {k}: {v}")
                elif isinstance(value, list):
                    if value:  # Only show non-empty lists
                        content_parts.append(f"  {field_label}:")
                        for item in value:
                            content_parts.append(f"    - {item}")
                elif value is not None and value != "":  # Skip empty/null values
                    content_parts.append(f"  {field_label}: {value}")

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
