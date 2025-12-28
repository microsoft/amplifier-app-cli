"""Canonical sources for provider modules."""

import logging
import subprocess
import sys
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from amplifier_app_cli.lib.legacy import ConfigManager

logger = logging.getLogger(__name__)

# Single source of truth for known provider git URLs
DEFAULT_PROVIDER_SOURCES = {
    "provider-anthropic": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "provider-openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "provider-azure-openai": "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
    "provider-ollama": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
}


def get_effective_provider_sources(config_manager: "ConfigManager | None" = None) -> dict[str, str]:
    """Get provider sources with settings modules and overrides applied.

    Merges:
    1. DEFAULT_PROVIDER_SOURCES (known providers)
    2. User-configured source overrides (for known providers)
    3. User-added provider modules from settings (for additional providers)

    User overrides and additions take precedence over defaults.

    Args:
        config_manager: Optional config manager for source overrides and settings

    Returns:
        Dict mapping module_id to source URI
    """
    sources = dict(DEFAULT_PROVIDER_SOURCES)

    if config_manager:
        # 1. Apply source overrides for known providers
        overrides = config_manager.get_module_sources()
        for module_id in list(sources.keys()):
            if module_id in overrides:
                sources[module_id] = overrides[module_id]
                logger.debug(f"Using override source for {module_id}: {overrides[module_id]}")

        # 2. Add user-added provider modules from settings
        # These are providers added via `amplifier module add provider-X --source ...`
        merged = config_manager.get_merged_settings()
        settings_providers = merged.get("modules", {}).get("providers", [])
        for provider in settings_providers:
            if isinstance(provider, dict):
                module_id = provider.get("module")
                source = provider.get("source")
                if module_id and source:
                    if module_id not in sources:
                        sources[module_id] = source
                        logger.debug(f"Added settings provider {module_id}: {source}")
                    elif sources[module_id] != source:
                        # Settings source overrides default (user's explicit choice)
                        sources[module_id] = source
                        logger.debug(f"Using settings source for {module_id}: {source}")

    return sources


def is_local_path(source_uri: str) -> bool:
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


def source_from_uri(source_uri: str):
    """Create appropriate source from URI (local path or git URL).

    Single source of truth for source type decision - use this instead of
    manually checking is_local_path() and creating FileSource/GitSource.

    Uses foundation-based source classes that create new-format cache directories:
    {repo-name}-{hash}/ instead of legacy {hash}/{ref}/ format.

    Args:
        source_uri: Source URI (git+https://... or local path like /path, ./path)

    Returns:
        FoundationFileSource for local paths, FoundationGitSource for git URLs
    """
    from amplifier_app_cli.lib.bundle_loader.resolvers import FoundationFileSource
    from amplifier_app_cli.lib.bundle_loader.resolvers import FoundationGitSource

    if is_local_path(source_uri):
        return FoundationFileSource(source_uri)
    return FoundationGitSource(source_uri)


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
    installed: list[str] = []
    failed: list[tuple[str, str]] = []

    # Get effective sources (with overrides applied)
    sources = get_effective_provider_sources(config_manager)

    for module_id, source_uri in sources.items():
        try:
            if verbose and console:
                console.print(f"  Installing {module_id}...", end="")

            # Use helper to create appropriate source type (DRY)
            source = source_from_uri(source_uri)

            # Resolve downloads to cache (for git) or validates path (for local)
            module_path = source.resolve()

            # Always install editable (-e) so that:
            # 1. Cache updates are immediately effective without reinstall
            # 2. Consistent behavior with foundation's ModuleActivator
            # 3. Dependencies are properly installed from the source location
            result = subprocess.run(
                ["uv", "pip", "install", "-e", str(module_path), "--python", sys.executable],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Failed to install: {result.stderr}")

            if verbose and console:
                suffix = " (local)" if is_local_path(source_uri) else ""
                console.print(f" [green]✓[/green]{suffix}")

            installed.append(module_id)

        except Exception as e:
            failed.append((module_id, str(e)))
            logger.warning(f"Failed to install {module_id}: {e}")

            if verbose and console:
                console.print(f"[red]Failed to install {module_id}: {e}[/red]")

    if failed and verbose and console:
        console.print(f"\n[yellow]Warning: {len(failed)} provider(s) failed to install[/yellow]")

    return installed


__all__ = [
    "DEFAULT_PROVIDER_SOURCES",
    "get_effective_provider_sources",
    "install_known_providers",
    "is_local_path",
    "source_from_uri",
]
