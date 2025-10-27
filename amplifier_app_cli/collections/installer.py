"""Collection installation - APP LAYER POLICY.

Installs collections from git repositories using the GitSource pattern.

Per KERNEL_PHILOSOPHY:
- "Could two teams want different behavior?" → YES (install location is policy)
- This is APP LAYER - kernel doesn't know about collections

Per IMPLEMENTATION_PHILOSOPHY:
- Reuse existing patterns: GitSource for downloads
- Ruthless simplicity: Direct copy from cache to collections dir
- No complex dependency resolution: Defer to uv
"""

import logging
import shutil
import subprocess
from pathlib import Path

from ..module_resolution.sources import GitSource
from .discovery import discover_collection_resources
from .schema import CollectionMetadata

logger = logging.getLogger(__name__)


class CollectionInstallError(Exception):
    """Raised when collection installation fails."""


def install_scenario_tools(collection_path: Path) -> list[str]:
    """
    Install scenario tools from collection using uv tool install.

    Scenario tools are Python packages in the scenario-tools/ directory.
    They are installed globally via uv tool install for PATH access.

    Args:
        collection_path: Path to installed collection

    Returns:
        List of installed tool names

    Example:
        >>> tools = install_scenario_tools(Path("~/.amplifier/collections/foundation"))
        >>> print(f"Installed {len(tools)} tools")
    """
    # Discover scenario tools
    resources = discover_collection_resources(collection_path)

    if not resources.scenario_tools:
        logger.debug(f"No scenario tools found in {collection_path}")
        return []

    installed_tools = []

    for tool_path in resources.scenario_tools:
        try:
            # Install via uv tool install
            # This installs the tool globally and makes it available in PATH
            logger.info(f"Installing scenario tool: {tool_path.name}")

            cmd = ["uv", "tool", "install", str(tool_path)]

            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )

            logger.debug(f"Installed {tool_path.name}: {result.stdout}")
            installed_tools.append(tool_path.name)

        except subprocess.CalledProcessError as e:
            # Log warning but don't fail entire collection install
            logger.warning(f"Failed to install scenario tool {tool_path.name}: {e.stderr}")
            continue
        except Exception as e:
            logger.warning(f"Failed to install scenario tool {tool_path.name}: {e}")
            continue

    return installed_tools


def uninstall_scenario_tools(collection_path: Path) -> list[str]:
    """
    Uninstall scenario tools from collection using uv tool uninstall.

    Args:
        collection_path: Path to collection

    Returns:
        List of uninstalled tool names
    """
    # Discover scenario tools
    resources = discover_collection_resources(collection_path)

    if not resources.scenario_tools:
        logger.debug(f"No scenario tools found in {collection_path}")
        return []

    uninstalled_tools = []

    for tool_path in resources.scenario_tools:
        try:
            # Load tool metadata to get package name
            import tomllib

            with open(tool_path / "pyproject.toml", "rb") as f:
                data = tomllib.load(f)

            # Get package name from [project] section
            package_name = data.get("project", {}).get("name")

            if not package_name:
                logger.warning(f"No package name in {tool_path}/pyproject.toml")
                continue

            # Uninstall via uv tool uninstall
            logger.info(f"Uninstalling scenario tool: {package_name}")

            cmd = ["uv", "tool", "uninstall", package_name]

            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )

            logger.debug(f"Uninstalled {package_name}: {result.stdout}")
            uninstalled_tools.append(package_name)

        except subprocess.CalledProcessError as e:
            # Tool might not be installed, log warning but continue
            logger.warning(f"Failed to uninstall scenario tool {tool_path.name}: {e.stderr}")
            continue
        except Exception as e:
            logger.warning(f"Failed to uninstall scenario tool {tool_path.name}: {e}")
            continue

    return uninstalled_tools


def install_collection(
    source_uri: str,
    target_dir: Path | None = None,
    local: bool = False,
) -> tuple[Path, CollectionMetadata]:
    """
    Install collection from git repository (APP LAYER POLICY).

    Uses GitSource pattern for downloads (reuses existing code).

    Args:
        source_uri: Git URI (e.g., git+https://github.com/org/collection@main)
        target_dir: Target directory (default: ~/.amplifier/collections)
        local: If True, install to .amplifier/collections instead

    Returns:
        Tuple of (install_path, metadata)

    Raises:
        CollectionInstallError: Installation failed

    Example:
        >>> path, metadata = install_collection(
        ...     "git+https://github.com/org/foundation@main"
        ... )
        >>> print(f"Installed {metadata.name} to {path}")
    """
    # Determine target directory (APP LAYER POLICY)
    if target_dir is None:
        if local:
            target_dir = Path.cwd() / ".amplifier" / "collections"
        else:
            target_dir = Path.home() / ".amplifier" / "collections"

    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Download via GitSource (to cache)
        logger.info(f"Downloading collection from {source_uri}")
        git_source = GitSource.from_uri(source_uri)
        cache_path = git_source.resolve()

        logger.debug(f"Downloaded to cache: {cache_path}")

        # Step 2: Load metadata to get collection name
        metadata_path = cache_path / "pyproject.toml"
        if not metadata_path.exists():
            raise CollectionInstallError(f"No pyproject.toml found in collection at {source_uri}")

        metadata = CollectionMetadata.from_pyproject(metadata_path)
        logger.debug(f"Collection name: {metadata.name}")

        # Step 3: Copy to collections directory
        collection_path = target_dir / metadata.name

        if collection_path.exists():
            logger.info(f"Removing existing installation: {collection_path}")
            shutil.rmtree(collection_path)

        logger.info(f"Installing to {collection_path}")
        shutil.copytree(cache_path, collection_path, symlinks=True)

        # Step 4: Install scenario tools (if any)
        installed_tools = install_scenario_tools(collection_path)
        if installed_tools:
            logger.info(f"Installed {len(installed_tools)} scenario tools: {', '.join(installed_tools)}")

        logger.info(f"Successfully installed collection: {metadata.name}")
        return (collection_path, metadata)

    except Exception as e:
        if isinstance(e, CollectionInstallError):
            raise
        raise CollectionInstallError(f"Failed to install collection: {e}") from e


def uninstall_collection(
    collection_name: str,
    target_dir: Path | None = None,
    local: bool = False,
) -> None:
    """
    Uninstall collection (APP LAYER POLICY).

    Args:
        collection_name: Name of collection to remove
        target_dir: Target directory (default: ~/.amplifier/collections)
        local: If True, remove from .amplifier/collections instead

    Raises:
        CollectionInstallError: Collection not found or removal failed

    Example:
        >>> uninstall_collection("foundation")
    """
    # Determine target directory (APP LAYER POLICY)
    if target_dir is None:
        if local:
            target_dir = Path.cwd() / ".amplifier" / "collections"
        else:
            target_dir = Path.home() / ".amplifier" / "collections"

    collection_path = target_dir / collection_name

    if not collection_path.exists():
        raise CollectionInstallError(f"Collection '{collection_name}' not found at {collection_path}")

    try:
        # Uninstall scenario tools first
        uninstalled_tools = uninstall_scenario_tools(collection_path)
        if uninstalled_tools:
            logger.info(f"Uninstalled {len(uninstalled_tools)} scenario tools: {', '.join(uninstalled_tools)}")

        logger.info(f"Uninstalling collection: {collection_name}")
        shutil.rmtree(collection_path)
        logger.info(f"Successfully uninstalled: {collection_name}")
    except Exception as e:
        raise CollectionInstallError(f"Failed to uninstall collection '{collection_name}': {e}") from e


def is_collection_installed(
    collection_name: str,
    target_dir: Path | None = None,
    local: bool = False,
) -> bool:
    """
    Check if collection is installed (APP LAYER HELPER).

    Args:
        collection_name: Name of collection
        target_dir: Target directory (default: ~/.amplifier/collections)
        local: If True, check .amplifier/collections instead

    Returns:
        True if collection exists and has pyproject.toml

    Example:
        >>> if is_collection_installed("foundation"):
        ...     print("Foundation is installed")
    """
    # Determine target directory (APP LAYER POLICY)
    if target_dir is None:
        if local:
            target_dir = Path.cwd() / ".amplifier" / "collections"
        else:
            target_dir = Path.home() / ".amplifier" / "collections"

    collection_path = target_dir / collection_name

    return collection_path.exists() and collection_path.is_dir() and (collection_path / "pyproject.toml").exists()
