"""Collection management commands - APP LAYER POLICY.

CLI commands for installing, listing, and managing collections.

Per KERNEL_PHILOSOPHY:
- "Could two teams want different behavior?" → YES (CLI UX is policy)
- This is APP LAYER - kernel doesn't know about collections

Per IMPLEMENTATION_PHILOSOPHY:
- Ruthless simplicity: Straightforward commands, clear output
- User-friendly errors and progress messages
"""

import logging
from pathlib import Path

import click

from ..collections.discovery import discover_collection_resources
from ..collections.discovery import list_agents
from ..collections.discovery import list_profiles
from ..collections.installer import CollectionInstallError
from ..collections.installer import install_collection
from ..collections.installer import is_collection_installed
from ..collections.installer import uninstall_collection
from ..collections.lock import CollectionLock
from ..collections.resolver import CollectionResolver
from ..collections.schema import CollectionMetadata

logger = logging.getLogger(__name__)


@click.group()
def collection():
    """Manage Amplifier collections.

    Collections are shareable bundles of expertise including profiles,
    agents, context, scenario tools, and modules.

    Examples:

        \b
        # Install a collection
        amplifier collection add git+https://github.com/org/collection@main

        \b
        # List installed collections
        amplifier collection list

        \b
        # Show collection details
        amplifier collection show foundation

        \b
        # Remove a collection
        amplifier collection remove foundation
    """


@collection.command()
@click.argument("source_uri")
@click.option(
    "--local",
    is_flag=True,
    help="Install to .amplifier/collections/ (project-local)",
)
def add(source_uri: str, local: bool):
    """Install a collection from git repository.

    SOURCE_URI should be a git URL in the format:
    git+https://github.com/org/collection@ref

    Examples:

        \b
        # Install from main branch
        amplifier collection add git+https://github.com/org/foundation@main

        \b
        # Install specific version
        amplifier collection add git+https://github.com/org/foundation@v1.0.0

        \b
        # Install to project (not user-global)
        amplifier collection add git+https://github.com/org/dev-tools@main --local
    """
    try:
        click.echo(f"Installing collection from {source_uri}...")

        # Install collection
        path, metadata = install_collection(source_uri, local=local)

        click.echo(f"✓ Installed {metadata.name} v{metadata.version}")
        click.echo(f"  Location: {path}")

        # Update lock file
        lock = CollectionLock()
        lock.add(
            name=metadata.name,
            source=source_uri,
            commit=None,  # Commit SHA tracking requires enhancing GitSource (YAGNI for now)
            path=path,
        )

        # Show what was installed
        resources = discover_collection_resources(path)
        if resources.has_resources():
            click.echo("\n  Resources:")
            if resources.profiles:
                click.echo(f"    • {len(resources.profiles)} profiles")
            if resources.agents:
                click.echo(f"    • {len(resources.agents)} agents")
            if resources.context:
                click.echo(f"    • {len(resources.context)} context files")
            if resources.scenario_tools:
                click.echo(f"    • {len(resources.scenario_tools)} scenario tools")
            if resources.modules:
                click.echo(f"    • {len(resources.modules)} modules")

        # Show capabilities
        if metadata.capabilities:
            click.echo("\n  Capabilities:")
            for capability in metadata.capabilities:
                click.echo(f"    • {capability}")

        click.echo(f"\n✓ Collection '{metadata.name}' is ready to use!")

    except CollectionInstallError as e:
        click.echo(f"✗ Installation failed: {e}", err=True)
        raise click.Abort()
    except Exception as e:
        click.echo(f"✗ Unexpected error: {e}", err=True)
        logger.exception("Collection installation failed")
        raise click.Abort()


@collection.command()
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show all collections (project + user + bundled)",
)
def list(show_all: bool):
    """List installed collections.

    By default, shows user-installed collections only.
    Use --all to include bundled collections.

    Examples:

        \b
        # List user-installed collections
        amplifier collection list

        \b
        # List all collections (including bundled)
        amplifier collection list --all
    """
    resolver = CollectionResolver()
    lock = CollectionLock()

    if show_all:
        # Show all collections from resolver
        collections = resolver.list_collections()
        if not collections:
            click.echo("No collections found.")
            return

        click.echo(f"Found {len(collections)} collections:\n")

        for name, path in collections:
            # Check if it's installed (in lock file)
            is_installed_flag = lock.is_installed(name)
            marker = "✓" if is_installed_flag else " "

            # Load metadata
            try:
                metadata_path = path / "pyproject.toml"
                metadata = CollectionMetadata.from_pyproject(metadata_path)
                version = f"v{metadata.version}"
                desc = metadata.description or "No description"
            except Exception:
                version = "unknown"
                desc = "Unable to load metadata"

            click.echo(f"{marker} {name} ({version})")
            click.echo(f"    {desc}")
            click.echo(f"    Location: {path}")
            click.echo()

    else:
        # Show only installed (in lock file)
        installed = lock.list_installed()
        if not installed:
            click.echo("No collections installed.")
            click.echo("\nInstall a collection with:")
            click.echo("  amplifier collection add git+https://github.com/org/collection@main")
            return

        click.echo(f"Installed collections ({len(installed)}):\n")

        for entry in installed:
            # Load metadata
            try:
                path = Path(entry.path)
                metadata_path = path / "pyproject.toml"
                metadata = CollectionMetadata.from_pyproject(metadata_path)
                desc = metadata.description or "No description"
            except Exception:
                desc = "Unable to load metadata"

            click.echo(f"✓ {entry.name}")
            click.echo(f"    {desc}")
            click.echo(f"    Source: {entry.source}")
            click.echo(f"    Installed: {entry.installed_at}")
            click.echo()


@collection.command()
@click.argument("name")
def show(name: str):
    """Show detailed information about a collection.

    NAME is the collection name (e.g., 'foundation', 'developer-expertise')

    Examples:

        \b
        # Show foundation collection details
        amplifier collection show foundation

        \b
        # Show developer-expertise collection
        amplifier collection show developer-expertise
    """
    # Resolve collection
    resolver = CollectionResolver()
    path = resolver.resolve(name)

    if path is None:
        click.echo(f"✗ Collection '{name}' not found.", err=True)
        click.echo("\nAvailable collections:")
        for coll_name, _ in resolver.list_collections():
            click.echo(f"  • {coll_name}")
        raise click.Abort()

    # Load metadata
    try:
        metadata_path = path / "pyproject.toml"
        metadata = CollectionMetadata.from_pyproject(metadata_path)
    except Exception as e:
        click.echo(f"✗ Failed to load collection metadata: {e}", err=True)
        raise click.Abort()

    # Display metadata
    click.echo(f"\n{metadata.name} v{metadata.version}")
    click.echo("=" * 60)

    if metadata.description:
        click.echo(f"\n{metadata.description}")

    if metadata.author:
        click.echo(f"\nAuthor: {metadata.author}")

    click.echo(f"\nLocation: {path}")

    # Show capabilities
    if metadata.capabilities:
        click.echo("\nCapabilities:")
        for capability in metadata.capabilities:
            click.echo(f"  • {capability}")

    # Show dependencies
    if metadata.requires:
        click.echo("\nRequires:")
        for dep, version in metadata.requires.items():
            click.echo(f"  • {dep} {version}")

    # Show URLs
    if metadata.homepage or metadata.repository:
        click.echo("\nLinks:")
        if metadata.homepage:
            click.echo(f"  Homepage: {metadata.homepage}")
        if metadata.repository:
            click.echo(f"  Repository: {metadata.repository}")

    # Discover resources
    resources = discover_collection_resources(path)

    if resources.has_resources():
        click.echo("\nResources:")

        if resources.profiles:
            profiles = list_profiles(path)
            click.echo(f"\n  Profiles ({len(profiles)}):")
            for profile in profiles:
                click.echo(f"    • {profile}")

        if resources.agents:
            agents = list_agents(path)
            click.echo(f"\n  Agents ({len(agents)}):")
            for agent in agents:
                click.echo(f"    • {agent}")

        if resources.context:
            click.echo(f"\n  Context files ({len(resources.context)}):")
            # Show relative paths
            for ctx_file in resources.context[:10]:  # Show first 10
                rel_path = ctx_file.relative_to(path)
                click.echo(f"    • {rel_path}")
            if len(resources.context) > 10:
                click.echo(f"    ... and {len(resources.context) - 10} more")

        if resources.scenario_tools:
            click.echo(f"\n  Scenario tools ({len(resources.scenario_tools)}):")
            for tool in resources.scenario_tools:
                click.echo(f"    • {tool.name}")

        if resources.modules:
            click.echo(f"\n  Modules ({len(resources.modules)}):")
            for module in resources.modules:
                click.echo(f"    • {module.name}")

    click.echo()


@collection.command()
@click.argument("name")
@click.option(
    "--local",
    is_flag=True,
    help="Remove from .amplifier/collections/ (project-local)",
)
@click.confirmation_option(prompt="Are you sure you want to remove this collection?")
def remove(name: str, local: bool):
    """Remove an installed collection.

    NAME is the collection name to remove.

    Note: This only removes collections installed with 'amplifier collection add'.
    It does not remove bundled collections.

    Examples:

        \b
        # Remove a collection
        amplifier collection remove foundation

        \b
        # Remove project-local collection
        amplifier collection remove dev-tools --local
    """
    try:
        # Check if installed
        if not is_collection_installed(name, local=local):
            click.echo(f"✗ Collection '{name}' is not installed.", err=True)
            raise click.Abort()

        # Uninstall
        uninstall_collection(name, local=local)

        # Update lock file
        lock = CollectionLock()
        lock.remove(name)

        click.echo(f"✓ Removed collection '{name}'")

    except CollectionInstallError as e:
        click.echo(f"✗ Removal failed: {e}", err=True)
        raise click.Abort()
    except Exception as e:
        click.echo(f"✗ Unexpected error: {e}", err=True)
        logger.exception("Collection removal failed")
        raise click.Abort()
