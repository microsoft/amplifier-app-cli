"""Provider configuration management."""

import logging
from dataclasses import dataclass
from typing import Literal

from amplifier_config import ConfigManager

logger = logging.getLogger(__name__)

ScopeType = Literal["local", "project", "global"]


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
        # Build provider config entry
        provider_entry = {"module": provider_id, "config": config}

        # Add source if provided
        if source:
            provider_entry["source"] = source

        # Update config at scope
        from amplifier_config import Scope

        scope_map = {
            "local": Scope.LOCAL,
            "project": Scope.PROJECT,
            "global": Scope.USER,
        }
        config_scope = scope_map[scope]

        # Set provider config at scope (using generic update_settings)
        self.config.update_settings({"providers": [provider_entry]}, scope=config_scope)

        logger.info(f"Configured {provider_id} at {scope} scope")

        # Get file path for return value
        file_path = str(self.config.scope_to_path(config_scope))

        return ConfigureResult(provider=provider_id, scope=scope, file=file_path, config=config)

    def get_current_provider(self) -> ProviderInfo | None:
        """Get currently active provider from merged config.

        Returns:
            ProviderInfo with provider details and source, or None
        """
        # Get providers from merged settings
        merged = self.config.get_merged_settings()
        providers = merged.get("providers", [])

        if providers:
            provider = providers[0]  # First provider

            # Determine source (which scope provided it)
            source = self._determine_provider_source(provider)

            return ProviderInfo(module_id=provider["module"], config=provider.get("config", {}), source=source)

        return None

    def list_providers(self) -> list[tuple[str, str, str]]:
        """List available provider module IDs.

        Returns:
            List of (module_id, display_name, description) tuples
        """
        return [
            ("provider-anthropic", "Anthropic Claude", "Recommended, most tested"),
            ("provider-openai", "OpenAI", "Good alternative"),
            ("provider-azure-openai", "Azure OpenAI", "Enterprise with Azure"),
            ("provider-ollama", "Ollama", "Local, free, no API key"),
        ]

    def reset_provider(self, scope: ScopeType) -> ResetResult:
        """Remove provider override at scope.

        Args:
            scope: Which scope to reset (local/project/global)

        Returns:
            ResetResult indicating success
        """
        from amplifier_config import Scope

        scope_map = {
            "local": Scope.LOCAL,
            "project": Scope.PROJECT,
            "global": Scope.USER,
        }
        config_scope = scope_map[scope]

        # Check if providers exist at this scope
        scope_path = self.config.scope_to_path(config_scope)
        scope_settings = self.config._read_yaml(scope_path) or {}
        had_providers = "providers" in scope_settings and len(scope_settings.get("providers", [])) > 0

        if had_providers:
            # Clear providers at this scope
            del scope_settings["providers"]
            self.config._write_yaml(scope_path, scope_settings)
            logger.info(f"Reset provider at {scope} scope")
            return ResetResult(scope=scope, removed=True)

        return ResetResult(scope=scope, removed=False)

    def _determine_provider_source(self, provider: dict) -> str:
        """Determine which scope provided this provider config."""

        module_id = provider["module"]

        # Check local first (highest priority)
        local_settings = self.config._read_yaml(self.config.paths.local) or {}
        local_providers = local_settings.get("providers", [])
        if any(p.get("module") == module_id for p in local_providers):
            return "local"

        # Check project
        project_settings = self.config._read_yaml(self.config.paths.project) or {}
        project_providers = project_settings.get("providers", [])
        if any(p.get("module") == module_id for p in project_providers):
            return "project"

        # Check user
        user_settings = self.config._read_yaml(self.config.paths.user) or {}
        user_providers = user_settings.get("providers", [])
        if any(p.get("module") == module_id for p in user_providers):
            return "global"

        return "profile"  # Must be from profile
