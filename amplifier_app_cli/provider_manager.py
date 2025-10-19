"""Provider configuration management."""

import logging
from dataclasses import dataclass
from typing import Literal

from .settings import SettingsManager

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

    def __init__(self, settings: SettingsManager | None = None):
        """Initialize provider manager.

        Args:
            settings: Settings manager instance (creates new if None)
        """
        self.settings = settings or SettingsManager()

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

        # Update settings at scope
        scope_map = {
            "local": "local",
            "project": "project",
            "global": "user",  # global maps to user settings
        }
        settings_scope = scope_map[scope]

        self.settings._update_settings(
            self._get_file_for_scope(settings_scope), {"config": {"providers": [provider_entry]}}
        )

        logger.info(f"Configured {provider_id} at {scope} scope")

        return ConfigureResult(
            provider=provider_id, scope=scope, file=str(self._get_file_for_scope(settings_scope)), config=config
        )

    def get_current_provider(self) -> ProviderInfo | None:
        """Get currently active provider from merged config.

        Returns:
            ProviderInfo with provider details and source, or None
        """
        # Get merged settings
        merged = self.settings.get_merged_settings()

        # Extract provider from config
        providers = merged.get("config", {}).get("providers", [])
        if providers and isinstance(providers, list):
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
        scope_map = {
            "local": "local",
            "project": "project",
            "global": "user",
        }
        settings_scope = scope_map[scope]

        target_file = self._get_file_for_scope(settings_scope)
        settings = self.settings._read_settings(target_file)

        if settings and "config" in settings and "providers" in settings["config"]:
            del settings["config"]["providers"]

            # Clean up empty config section
            if not settings["config"]:
                del settings["config"]

            self.settings._write_settings(target_file, settings)
            logger.info(f"Reset provider at {scope} scope")
            return ResetResult(scope=scope, removed=True)

        return ResetResult(scope=scope, removed=False)

    def _get_file_for_scope(self, scope: str):
        """Get settings file path for scope."""
        if scope == "user":
            return self.settings.user_settings_file
        if scope == "project":
            return self.settings.project_settings_file
        # local
        return self.settings.local_settings_file

    def _determine_provider_source(self, provider: dict) -> str:
        """Determine which scope provided this provider config."""
        module_id = provider["module"]

        # Check local first (highest priority)
        local = self.settings._read_settings(self.settings.local_settings_file)
        if local and "config" in local and "providers" in local["config"]:
            local_providers = local["config"]["providers"]
            if any(p.get("module") == module_id for p in local_providers):
                return "local"

        # Check project
        project = self.settings._read_settings(self.settings.project_settings_file)
        if project and "config" in project and "providers" in project["config"]:
            project_providers = project["config"]["providers"]
            if any(p.get("module") == module_id for p in project_providers):
                return "project"

        # Check user
        user = self.settings._read_settings(self.settings.user_settings_file)
        if user and "config" in user and "providers" in user["config"]:
            user_providers = user["config"]["providers"]
            if any(p.get("module") == module_id for p in user_providers):
                return "global"

        return "profile"  # Must be from profile
