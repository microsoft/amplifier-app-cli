"""Canonical sources for provider modules."""

import importlib
import importlib.metadata
import logging
import site
import subprocess
import sys
from typing import TYPE_CHECKING

from rich.console import Console

from .utils.error_format import escape_markup

if TYPE_CHECKING:
    from amplifier_app_cli.lib.config_compat import ConfigManager

logger = logging.getLogger(__name__)

# Single source of truth for known provider git URLs
DEFAULT_PROVIDER_SOURCES = {
    "provider-anthropic": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "provider-azure-openai": "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
    "provider-gemini": "git+https://github.com/microsoft/amplifier-module-provider-gemini@main",
    "provider-github-copilot": "git+https://github.com/microsoft/amplifier-module-provider-github-copilot@main",
    "provider-ollama": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
    "provider-openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "provider-vllm": "git+https://github.com/microsoft/amplifier-module-provider-vllm@main",
}

# Runtime dependencies between providers.
# Some providers extend others (e.g., Azure OpenAI extends OpenAI's provider class).
# These are runtime dependencies, NOT build dependencies, to avoid transitive
# dependency issues with editable installs during development.
# Format: {"dependent": ["dependency1", "dependency2", ...]}
PROVIDER_DEPENDENCIES: dict[str, list[str]] = {
    "provider-azure-openai": [
        "provider-openai"
    ],  # AzureOpenAIProvider extends OpenAIProvider
}


def _get_ordered_providers(sources: dict[str, str]) -> list[tuple[str, str]]:
    """Order providers so dependencies are installed first (topological sort).

    Ensures providers that depend on others are installed after their dependencies.
    For example, provider-azure-openai depends on provider-openai at runtime
    (AzureOpenAIProvider extends OpenAIProvider), so openai must be installed first.

    Args:
        sources: Dict mapping module_id to source URI

    Returns:
        List of (module_id, source_uri) tuples in dependency-respecting order
    """
    ordered: list[tuple[str, str]] = []
    remaining = set(sources.keys())

    while remaining:
        # Find providers whose dependencies are all satisfied (not in remaining)
        ready = [
            p
            for p in remaining
            if all(dep not in remaining for dep in PROVIDER_DEPENDENCIES.get(p, []))
        ]

        if not ready:
            # No providers ready - either circular dependency or dependency not in sources.
            # Fall back to taking any remaining provider to avoid infinite loop.
            ready = [sorted(remaining)[0]]
            logger.debug(
                f"Dependency ordering: no ready providers, falling back to {ready[0]}"
            )

        # Process ready providers in sorted order for determinism
        for provider in sorted(ready):
            ordered.append((provider, sources[provider]))
            remaining.remove(provider)

    return ordered


def get_effective_provider_sources(
    config_manager: "ConfigManager | None" = None,
) -> dict[str, str]:
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
                logger.debug(
                    f"Using override source for {module_id}: {overrides[module_id]}"
                )

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


def ensure_provider_installed(
    module_id: str,
    config_manager: "ConfigManager | None" = None,
    console: Console | None = None,
) -> bool:
    """Ensure a single provider module is installed.

    This is a lightweight alternative to install_known_providers() that installs
    only the specified provider. Used for auto-fixing the post-update scenario
    where settings exist but the venv was wiped.

    Args:
        module_id: Provider module ID (e.g., "provider-anthropic")
        config_manager: Optional config manager for source overrides
        console: Optional Rich console for status messages

    Returns:
        True if provider was installed (or already available), False on failure
    """
    import importlib
    import importlib.metadata
    import site

    # Normalize module ID
    if not module_id.startswith("provider-"):
        module_id = f"provider-{module_id}"

    # Get source URI for this provider
    sources = get_effective_provider_sources(config_manager)
    source_uri = sources.get(module_id)

    if not source_uri:
        logger.warning(f"No source found for provider {module_id}")
        return False

    try:
        if console:
            console.print(f"[dim]Installing {module_id}...[/dim]", end="")

        # Resolve and install
        source = source_from_uri(source_uri)
        module_path = source.resolve()

        result = subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "-e",
                str(module_path),
                "--python",
                sys.executable,
                "--refresh",  # Force fresh fetch from git sources
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Install failed: {result.stderr}")

        # Refresh Python's view of installed packages
        importlib.invalidate_caches()
        for site_dir in site.getsitepackages():
            site.addsitedir(site_dir)
        if hasattr(importlib.metadata, "distributions"):
            list(importlib.metadata.distributions())

        if console:
            console.print(" [green]✓[/green]")

        logger.info(f"Successfully installed {module_id}")
        return True

    except Exception as e:
        logger.warning(f"Failed to install {module_id}: {e}")
        if console:
            console.print(" [red]✗[/red]")
        return False


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

    # Order providers so dependencies are installed first
    # (e.g., provider-openai before provider-azure-openai)
    ordered_providers = _get_ordered_providers(sources)

    for module_id, source_uri in ordered_providers:
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
                [
                    "uv",
                    "pip",
                    "install",
                    "-e",
                    str(module_path),
                    "--python",
                    sys.executable,
                ],
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
                console.print(
                    f"[red]Failed to install {module_id}: {escape_markup(e)}[/red]"
                )

    if failed and verbose and console:
        console.print(
            f"\n[yellow]Warning: {len(failed)} provider(s) failed to install[/yellow]"
        )

    # Refresh Python's view of installed packages so they're immediately importable.
    # Without this, the current Python process won't see packages installed via subprocess.
    # This must be thorough - just invalidate_caches() is not enough for subprocess installs.
    if installed:
        importlib.invalidate_caches()

        # Re-add site directories to ensure newly installed packages are found
        for site_dir in site.getsitepackages():
            site.addsitedir(site_dir)

        # Force refresh of importlib.metadata distributions cache
        if hasattr(importlib.metadata, "distributions"):
            list(importlib.metadata.distributions())

    return installed


__all__ = [
    "DEFAULT_PROVIDER_SOURCES",
    "ensure_provider_installed",
    "get_effective_provider_sources",
    "install_known_providers",
    "is_local_path",
    "source_from_uri",
]
