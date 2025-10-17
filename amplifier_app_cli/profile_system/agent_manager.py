"""Agent manager for CLI operations."""

import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .agent_loader import AgentLoader

logger = logging.getLogger(__name__)
console = Console()


class AgentManager:
    """Manages agent discovery and display for CLI."""

    def __init__(self, loader: AgentLoader | None = None):
        """
        Initialize agent manager.

        Args:
            loader: Optional agent loader. If None, creates default loader.
        """
        if loader is None:
            self.loader = AgentLoader()
        else:
            self.loader = loader

    def list_agents(self) -> None:
        """Display all available agents in a table."""
        agents = self.loader.list_agents()

        if not agents:
            console.print("No agents found.")
            return

        table = Table(title="Available Agents")
        table.add_column("Name", style="cyan")
        table.add_column("Source", style="yellow")
        table.add_column("Description", style="white")

        for agent_name in agents:
            source = self.loader.get_agent_source(agent_name) or "unknown"

            # Load agent to get description
            try:
                agent = self.loader.load_agent(agent_name)
                description = agent.meta.description[:60]  # Truncate long descriptions
                if len(agent.meta.description) > 60:
                    description += "..."
            except Exception:
                description = "(error loading)"

            table.add_row(agent_name, source, description)

        console.print(table)

    def show_agent(self, name: str) -> None:
        """
        Display detailed information about an agent.

        Args:
            name: Agent name
        """
        try:
            # Resolve and display source
            agent_file = self.loader.resolver.resolve(name)
            if agent_file is None:
                console.print(f"[red]Agent '{name}' not found[/red]")
                return

            source = self.loader.get_agent_source(name)
            console.print(f"\n[bold cyan]Agent:[/bold cyan] {name}")
            console.print(f"[bold yellow]Source:[/bold yellow] {source}")
            console.print(f"[bold green]File:[/bold green] {agent_file}\n")

            # Load and display agent
            agent = self.loader.load_agent(name)

            # Display metadata
            console.print("[bold]Metadata:[/bold]")
            console.print(f"  Name: {agent.meta.name}")
            console.print(f"  Description: {agent.meta.description}\n")

            # Display configuration
            mount_plan_fragment = agent.to_mount_plan_fragment()

            if agent.providers:
                console.print("[bold]Providers:[/bold]")
                for provider in agent.providers:
                    console.print(f"  - {provider.module}")
                    if provider.config:
                        for key, val in provider.config.items():
                            console.print(f"      {key}: {val}")
                console.print()

            if agent.tools:
                console.print("[bold]Tools:[/bold]")
                for tool in agent.tools:
                    console.print(f"  - {tool.module}")
                    if tool.source:
                        console.print(f"      source: {tool.source}")
                    if tool.config:
                        for key, val in tool.config.items():
                            console.print(f"      {key}: {val}")
                console.print()

            if agent.hooks:
                console.print("[bold]Hooks:[/bold]")
                for hook in agent.hooks:
                    console.print(f"  - {hook.module}")
                    if hook.config:
                        for key, val in hook.config.items():
                            console.print(f"      {key}: {val}")
                console.print()

            if agent.session:
                console.print("[bold]Session Overrides:[/bold]")
                for key, val in agent.session.items():
                    console.print(f"  {key}: {val}")
                console.print()

            if agent.system and agent.system.get("instruction"):
                console.print("[bold]System Instruction:[/bold]")
                instruction = agent.system["instruction"]
                # Show first 500 chars
                if len(instruction) > 500:
                    console.print(f"{instruction[:500]}...\n")
                else:
                    console.print(f"{instruction}\n")

        except Exception as e:
            console.print(f"[red]Error loading agent '{name}': {e}[/red]")

    def validate_agent(self, file_path: str) -> None:
        """
        Validate an agent file.

        Args:
            file_path: Path to agent file
        """
        try:
            path = Path(file_path)
            if not path.exists():
                console.print(f"[red]File not found: {file_path}[/red]")
                return

            # Try to load as agent
            from .utils import parse_frontmatter

            data = parse_frontmatter(path)

            # Validate as Agent
            from .agent_schema import Agent

            agent = Agent(**data)

            console.print(f"[green]✓[/green] Agent file is valid: {file_path}")
            console.print(f"  Name: {agent.meta.name}")
            console.print(f"  Description: {agent.meta.description}")

            if agent.providers:
                console.print(f"  Providers: {len(agent.providers)}")
            if agent.tools:
                console.print(f"  Tools: {len(agent.tools)}")
            if agent.hooks:
                console.print(f"  Hooks: {len(agent.hooks)}")

        except Exception as e:
            console.print(f"[red]✗ Validation failed:[/red] {e}")
