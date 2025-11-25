"""Provider configuration management."""

import logging
from dataclasses import dataclass

from amplifier_config import ConfigManager

from .lib.app_settings import AppSettings
from .lib.app_settings import ScopeType
from .provider_sources import DEFAULT_PROVIDER_SOURCES

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
            source: Module source URL (optional, will use canonical if not provided)

        Returns:
            ConfigureResult with what changed and where
        """
        # Determine provider source (explicit or canonical)
        canonical_source = DEFAULT_PROVIDER_SOURCES.get(provider_id)
        provider_source = source or canonical_source

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

    def list_providers(self) -> list[tuple[str, str, str]]:
        """List available provider module IDs via dynamic discovery.

        Discovers providers from:
        1. Installed modules (entry points)
        2. Profile-configured modules
        3. Settings-configured modules

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
                # Use module's name and description
                providers[module.id] = (module.id, module.name, module.description)

        return list(providers.values())

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
