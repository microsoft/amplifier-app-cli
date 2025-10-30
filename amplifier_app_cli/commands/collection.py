"""Collection management commands - APP LAYER POLICY.

CLI commands for installing, listing, and managing collections.

Per KERNEL_PHILOSOPHY:
- "Could two teams want different behavior?" → YES (CLI UX is policy)
- This is APP LAYER - kernel doesn't know about collections

Per IMPLEMENTATION_PHILOSOPHY:
- Ruthless simplicity: Straightforward commands, clear output
- User-friendly errors and progress messages
"""

import asyncio
import logging
import re
import shutil
from pathlib import Path

import click
from amplifier_collections import CollectionInstallError
from amplifier_collections import CollectionLock
from amplifier_collections import CollectionMetadata
from amplifier_collections import discover_collection_resources
from amplifier_collections import install_collection
from amplifier_collections import list_agents
from amplifier_collections import list_profiles
from amplifier_collections import uninstall_collection
from amplifier_module_resolution import GitSource

from ..paths import create_collection_resolver
from ..paths import get_collection_lock_path

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

        # Extract collection name from URI (app policy)
        # Format: git+https://github.com/org/collection@version
        match = re.search(r"/([^/]+?)(?:\.git)?(?:@|$)", source_uri)
        if not match:
            raise ValueError(f"Cannot extract collection name from URI: {source_uri}")
        collection_name = match.group(1)

        # Determine installation location (app policy)
        if local:
            target_dir = Path.cwd() / ".amplifier" / "collections" / collection_name
            lock_path = Path.cwd() / ".amplifier" / "collections.lock"
        else:
            target_dir = Path.home() / ".amplifier" / "collections" / collection_name
            lock_path = Path.home() / ".amplifier" / "collections.lock"

        # Create source object (protocol-based API)
        source = GitSource.from_uri(source_uri)

        # Create lock manager
        lock = CollectionLock(lock_path=lock_path)

        # Install using protocol API (library handles lock updates)
        metadata = asyncio.run(install_collection(source=source, target_dir=target_dir, lock=lock))

        # Fix: Rename directory to match metadata name if different (APP LAYER POLICY)
        # This ensures collection:profile format uses metadata name, not repo name
        if target_dir.name != metadata.name:
            logger.debug(f"Renaming {target_dir.name} → {metadata.name}")
            new_target_dir = target_dir.parent / metadata.name

            # Remove existing if present
            if new_target_dir.exists():
                logger.warning(f"Removing existing collection at {new_target_dir}")
                shutil.rmtree(new_target_dir)

            # Rename to metadata name
            target_dir.rename(new_target_dir)
            target_dir = new_target_dir

            # Update lock file with correct path
            lock.remove_entry(metadata.name)
            source_uri_for_lock = getattr(source, "uri", source_uri)
            commit_sha = getattr(source, "commit_sha", None)
            lock.add_entry(name=metadata.name, source=source_uri_for_lock, commit=commit_sha, path=target_dir)

        # Fix: Normalize collection structure to root level (APP LAYER POLICY)
        # uv pip install creates: target_dir/package_name/[pyproject.toml, profiles/, agents/, ...]
        # We need: target_dir/[pyproject.toml, profiles/, agents/, ...]
        #
        # This ensures:
        # - CollectionResolver can find collection (pyproject.toml at root)
        # - Profile search paths find profiles/ at root
        # - Agent search paths find agents/ at root
        # - Consistent structure regardless of installation method
        if not (target_dir / "pyproject.toml").exists():
            # Find package subdirectory
            package_subdir = None
            for item in target_dir.iterdir():
                if (
                    item.is_dir()
                    and not item.name.endswith(".dist-info")
                    and not item.name.startswith(".")
                    and (item / "pyproject.toml").exists()
                ):
                    package_subdir = item
                    logger.debug(f"Found package subdirectory: {item.name}")
                    break

            if package_subdir:
                # Move all contents from package subdir to collection root
                for item in package_subdir.iterdir():
                    dest = target_dir / item.name
                    if dest.exists():
                        # Remove existing to avoid conflicts
                        if dest.is_dir():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    shutil.move(str(item), str(target_dir))
                    logger.debug(f"Moved {item.name} to collection root")

                # Remove now-empty package subdirectory
                package_subdir.rmdir()
                logger.debug(f"Removed empty package subdirectory: {package_subdir.name}")

        path = target_dir
        click.echo(f"✓ Installed {metadata.name} v{metadata.version}")
        click.echo(f"  Location: {path}")

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
    resolver = create_collection_resolver()
    lock = CollectionLock(get_collection_lock_path(local=False))

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
        installed = lock.list_entries()
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
    resolver = create_collection_resolver()
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
        # Determine collections directory based on scope (app policy)
        if local:
            collections_dir = Path.cwd() / ".amplifier" / "collections"
            lock_path = Path.cwd() / ".amplifier" / "collections.lock"
        else:
            collections_dir = Path.home() / ".amplifier" / "collections"
            lock_path = Path.home() / ".amplifier" / "collections.lock"

        # Create lock manager
        lock = CollectionLock(lock_path=lock_path)

        # Check if collection is tracked as installed
        if not lock.is_installed(name):
            click.echo(f"✗ Collection '{name}' is not tracked as installed.", err=True)
            raise click.Abort()

        # Uninstall using protocol API (library handles lock updates)
        asyncio.run(uninstall_collection(collection_name=name, collections_dir=collections_dir, lock=lock))

        click.echo(f"✓ Removed collection '{name}'")

    except CollectionInstallError as e:
        click.echo(f"✗ Removal failed: {e}", err=True)
        raise click.Abort()
    except Exception as e:
        click.echo(f"✗ Unexpected error: {e}", err=True)
        logger.exception("Collection removal failed")
        raise click.Abort()
