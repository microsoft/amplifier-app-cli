"""Provider configuration management."""

import logging
from dataclasses import dataclass
from typing import Any

from amplifier_config import ConfigManager

from .lib.app_settings import AppSettings
from .lib.app_settings import ScopeType
from .provider_loader import get_provider_info
from .provider_sources import get_effective_provider_sources

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Information about active provider."""

    module_id: str
    config: dict
    source: str  # Which scope provided it


@dataclass
class ConfigureResult:
    """Result of provider configuration."""

    provider: str
    scope: str
    file: str
    config: dict


@dataclass
class ResetResult:
    """Result of resetting provider."""

    scope: str
    removed: bool


class ProviderManager:
    """Manage provider configuration across scopes."""

    def __init__(self, config: ConfigManager):
        """Initialize provider manager.

        Args:
            config: Config manager instance (required)
        """
        self.config = config
        self._settings = AppSettings(config)

    def use_provider(
        self,
        provider_id: str,
        scope: ScopeType,
        config: dict,
        source: str | None = None,
    ) -> ConfigureResult:
        """Configure provider at specified scope.

        Args:
            provider_id: Provider module ID (provider-anthropic, provider-openai, etc.)
            scope: Where to save configuration (local/project/global)
            config: Provider-specific configuration (model, api_key, etc.)
            source: Module source URL (optional, will use effective source if not provided)

        Returns:
            ConfigureResult with what changed and where
        """
        # Determine provider source (explicit, user override, or canonical default)
        if source:
            provider_source = source
        else:
            # Check effective sources first (includes user overrides from settings)
            effective_sources = get_effective_provider_sources(self.config)
            provider_source = effective_sources.get(provider_id)

        # Build provider config entry with high priority (lower = higher priority)
        # Priority 1 ensures explicitly configured provider wins over profile defaults (100)
        config_with_priority = {**config, "priority": 1}
        provider_entry = {"module": provider_id, "config": config_with_priority}

        if provider_source:
            provider_entry["source"] = provider_source

        # Update config at scope
        self._settings.set_provider_override(provider_entry, scope)

        logger.info(f"Configured {provider_id} at {scope} scope")

        # Get file path for return value
        file_path = str(self._settings.scope_path(scope))

        return ConfigureResult(provider=provider_id, scope=scope, file=file_path, config=config)

    def get_current_provider(self) -> ProviderInfo | None:
        """Get currently active provider from merged config.

        Returns:
            ProviderInfo with provider details and source, or None
        """
        # Get providers from merged settings
        providers = self._settings.get_provider_overrides()

        if providers:
            provider = providers[0]  # First provider

            # Determine source (which scope provided it)
            source = self._determine_provider_source(provider)

            return ProviderInfo(module_id=provider["module"], config=provider.get("config", {}), source=source)

        return None

    def get_provider_config(self, provider_id: str, scope: ScopeType | None = None) -> dict[str, Any] | None:
        """Get configuration for a specific provider by module ID.

        Looks through provider overrides to find configuration for the specified
        provider. Useful for getting existing config values as defaults when
        re-configuring a provider.

        Args:
            provider_id: Provider module ID (e.g., "provider-anthropic", "provider-openai")
            scope: Optional scope to read from. If None, reads from merged settings
                   (LOCAL > PROJECT > USER). If specified, reads from that scope only.
                   Use "global" for USER scope to find prior global configs.

        Returns:
            Provider config dict if found, None otherwise
        """
        if scope is not None:
            # Read from specific scope only
            providers = self._settings.get_scope_provider_overrides(scope)
            scope_path = self._settings.scope_path(scope)
            logger.debug(f"get_provider_config: reading from {scope} scope at {scope_path}")
        else:
            # Read from merged settings (default behavior)
            providers = self._settings.get_provider_overrides()
            logger.debug("get_provider_config: reading from merged settings")

        logger.debug(f"get_provider_config: found {len(providers)} providers")
        for provider in providers:
            module = provider.get("module")
            logger.debug(f"get_provider_config: checking module '{module}' against '{provider_id}'")
            if module == provider_id:
                config = provider.get("config", {})
                logger.debug(f"get_provider_config: found matching config with keys: {list(config.keys())}")
                return config
        logger.debug(f"get_provider_config: no matching provider found for '{provider_id}'")
        return None

    def list_providers(self) -> list[tuple[str, str, str]]:
        """List available provider module IDs via dynamic discovery.

        Discovers providers from:
        1. Installed modules (entry points)
        2. Known provider sources (resolved via GitSource/FileSource)

        Sources are merged with entry points, allowing local overrides
        to appear alongside installed providers.

        Returns:
            List of (module_id, display_name, description) tuples
        """
        import asyncio

        from amplifier_core.loader import ModuleLoader

        providers: dict[str, tuple[str, str, str]] = {}

        # Discover installed providers via entry points
        loader = ModuleLoader()
        modules = asyncio.run(loader.discover())

        for module in modules:
            if module.type == "provider":
                # Try to get proper display_name from provider's get_info()
                info = get_provider_info(module.id)
                if info and "display_name" in info:
                    display_name = info["display_name"]
                else:
                    # Fallback to module's name from entry point
                    display_name = module.name
                providers[module.id] = (module.id, display_name, module.description)

        # Also discover from effective sources (includes local overrides)
        # This ensures providers with local file sources appear even when
        # other providers were found via entry points
        source_providers = self._discover_providers_from_sources()
        for module_id, provider_info in source_providers.items():
            if module_id not in providers:
                # Add providers not found via entry points (e.g., local overrides)
                providers[module_id] = provider_info

        return list(providers.values())

    def _discover_providers_from_sources(self) -> dict[str, tuple[str, str, str]]:
        """Discover providers by resolving effective sources.

        Uses GitSource.resolve() or FileSource.resolve() to get module paths
        (same mechanism as runtime module loading), then imports modules directly.

        Effective sources include:
        1. DEFAULT_PROVIDER_SOURCES (known providers)
        2. User-added provider modules from settings
        3. User-configured source overrides (local file paths or git URLs)

        Returns:
            Dict mapping module_id to (module_id, display_name, description) tuples
        """
        import importlib
        import sys

        from amplifier_module_resolution.sources import FileSource
        from amplifier_module_resolution.sources import GitSource

        from .provider_sources import is_local_path

        providers: dict[str, tuple[str, str, str]] = {}

        # Use effective sources (includes both default and user-added providers)
        effective_sources = get_effective_provider_sources(self.config)
        for module_id, source_uri in effective_sources.items():
            try:
                # Resolve source to path - handle both local files and git URLs
                if is_local_path(source_uri):
                    file_source = FileSource(source_uri)
                    module_path = file_source.resolve()
                else:
                    git_source = GitSource.from_uri(source_uri)
                    module_path = git_source.resolve()

                # Add to sys.path if not already there
                path_str = str(module_path)
                if path_str not in sys.path:
                    sys.path.insert(0, path_str)
                    logger.debug(f"Added module path to sys.path: {path_str}")

                # Invalidate import caches
                importlib.invalidate_caches()

                # Try to get provider info via direct import
                info = get_provider_info(module_id)
                if info:
                    display_name = info.get("display_name", module_id.replace("-", " ").title())
                    description = info.get("description", f"Provider: {module_id}")
                    providers[module_id] = (module_id, display_name, description)
                    logger.debug(f"Discovered provider from source: {module_id}")
                else:
                    # Even if we can't get info, verify module is importable
                    provider_name = module_id.replace("provider-", "")
                    module_name = f"amplifier_module_provider_{provider_name.replace('-', '_')}"
                    importlib.import_module(module_name)
                    display_name = module_id.replace("-", " ").title()
                    providers[module_id] = (module_id, display_name, f"Provider: {module_id}")
                    logger.debug(f"Discovered provider from source (no info): {module_id}")

            except Exception as e:
                logger.debug(f"Could not resolve/import provider {module_id}: {e}")

        return providers

    def reset_provider(self, scope: ScopeType) -> ResetResult:
        """Remove provider override at scope.

        Args:
            scope: Which scope to reset (local/project/global)

        Returns:
            ResetResult indicating success
        """
        removed = self._settings.clear_provider_override(scope)

        if removed:
            logger.info(f"Reset provider at {scope} scope")
            return ResetResult(scope=scope, removed=True)

        return ResetResult(scope=scope, removed=False)

    def _determine_provider_source(self, provider: dict) -> str:
        """Determine which scope provided this provider config."""

        module_id = provider["module"]

        # Check local first (highest priority)
        local_providers = self._settings.get_scope_provider_overrides("local")
        if any(p.get("module") == module_id for p in local_providers):
            return "local"

        # Check project
        project_providers = self._settings.get_scope_provider_overrides("project")
        if any(p.get("module") == module_id for p in project_providers):
            return "project"

        # Check user
        user_providers = self._settings.get_scope_provider_overrides("global")
        if any(p.get("module") == module_id for p in user_providers):
            return "global"

        return "profile"  # Must be from profile
