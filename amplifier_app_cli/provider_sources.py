"""Canonical sources for provider modules."""

import logging
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from amplifier_config import ConfigManager

logger = logging.getLogger(__name__)

# Single source of truth for known provider git URLs
DEFAULT_PROVIDER_SOURCES = {
    "provider-anthropic": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "provider-openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "provider-azure-openai": "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
    "provider-ollama": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
}


def get_effective_provider_sources(config_manager: "ConfigManager | None" = None) -> dict[str, str]:
    """Get provider sources with overrides applied.

    Merges DEFAULT_PROVIDER_SOURCES with any user-configured source overrides.
    User overrides take precedence over defaults.

    Args:
        config_manager: Optional config manager for source overrides

    Returns:
        Dict mapping module_id to source URI
    """
    sources = dict(DEFAULT_PROVIDER_SOURCES)

    if config_manager:
        # Get user-configured source overrides
        overrides = config_manager.get_module_sources()
        for module_id in sources:
            if module_id in overrides:
                sources[module_id] = overrides[module_id]
                logger.debug(f"Using override source for {module_id}: {overrides[module_id]}")

    return sources


def _is_local_path(source_uri: str) -> bool:
    """Check if source URI is a local file path.

    Args:
        source_uri: Source URI string

    Returns:
        True if local path (starts with /, ./, ../, or file://)
    """
    return (
        source_uri.startswith("/")
        or source_uri.startswith("./")
        or source_uri.startswith("../")
        or source_uri.startswith("file://")
    )


def install_known_providers(
    config_manager: "ConfigManager | None" = None,
    console: Console | None = None,
    verbose: bool = True,
) -> list[str]:
    """Install all known provider modules.

    Downloads and caches all known providers so they can be discovered
    via entry points for use in init and provider use commands.

    Uses source overrides from config_manager if available, otherwise
    falls back to DEFAULT_PROVIDER_SOURCES.

    Supports both git URLs (git+https://...) and local file paths
    (./path, ../path, /absolute/path, file://path).

    Args:
        config_manager: Optional config manager for source overrides
        console: Optional Rich console for progress display
        verbose: Whether to show progress messages

    Returns:
        List of successfully installed provider module IDs
    """
    from amplifier_module_resolution.sources import FileSource
    from amplifier_module_resolution.sources import GitSource

    installed: list[str] = []
    failed: list[tuple[str, str]] = []

    # Get effective sources (with overrides applied)
    sources = get_effective_provider_sources(config_manager)

    for module_id, source_uri in sources.items():
        try:
            if verbose and console:
                console.print(f"  Installing {module_id}...", end="")

            # Check if local file path or git URL
            if _is_local_path(source_uri):
                # Local file source - just validate it exists
                file_source = FileSource(source_uri)
                file_source.resolve()
                if verbose and console:
                    console.print(" [green]✓[/green] (local)")
            else:
                # Git source - download if not cached
                git_source = GitSource.from_uri(source_uri)
                git_source.resolve()
                if verbose and console:
                    console.print(" [green]✓[/green]")

            installed.append(module_id)

        except Exception as e:
            failed.append((module_id, str(e)))
            logger.warning(f"Failed to install {module_id}: {e}")

            if verbose and console:
                console.print(f"[red]Failed to install {module_id}: {e}[/red]")

    if failed and verbose and console:
        console.print(f"\n[yellow]Warning: {len(failed)} provider(s) failed to install[/yellow]")

    return installed


__all__ = ["DEFAULT_PROVIDER_SOURCES", "get_effective_provider_sources", "install_known_providers"]
