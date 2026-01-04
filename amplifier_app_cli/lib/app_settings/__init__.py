"""Shared application-level settings helpers.

These helpers live in the CLI repo for now so we can finalize their shape
before extracting them into a dedicated library for other front ends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Literal

from amplifier_app_cli.lib.legacy import ConfigManager
from amplifier_app_cli.lib.legacy import ModuleConfig
from amplifier_app_cli.lib.legacy import Profile
from amplifier_app_cli.lib.legacy import Scope

from ...provider_sources import DEFAULT_PROVIDER_SOURCES
from amplifier_app_cli.lib.merge_utils import merge_tool_configs

ScopeType = Literal["local", "project", "global"]

_SCOPE_MAP: dict[ScopeType, Scope] = {
    "local": Scope.LOCAL,
    "project": Scope.PROJECT,
    "global": Scope.USER,
}


class AppSettings:
    """High-level helpers for reading and writing Amplifier application settings."""

    def __init__(self, config_manager: ConfigManager):
        self._config = config_manager

    # ----- Scope helpers -----

    def _scope_enum(self, scope: ScopeType) -> Scope:
        return _SCOPE_MAP[scope]

    def scope_path(self, scope: ScopeType) -> Path | None:
        """Return the filesystem path for a scope, or None if scope is disabled."""
        return self._config.scope_to_path(self._scope_enum(scope))

    # ----- Provider overrides -----

    def set_provider_override(self, provider_entry: dict[str, Any], scope: ScopeType) -> None:
        """Persist provider override at a specific scope.
        
        Updates or adds the provider entry without replacing other providers.
        The new/updated provider is always moved to the front (becomes active).
        Other providers with priority 1 are demoted to priority 10.
        """
        # Read existing providers at this scope
        existing_providers = self.get_scope_provider_overrides(scope)
        
        module_id = provider_entry.get("module")
        other_providers = []
        
        for provider in existing_providers:
            if provider.get("module") == module_id:
                # Skip - we'll add the new entry at the front
                continue
            else:
                # Demote any other priority-1 providers to priority 10
                config = provider.get("config", {})
                if isinstance(config, dict) and config.get("priority") == 1:
                    provider = {**provider, "config": {**config, "priority": 10}}
                other_providers.append(provider)
        
        # New provider goes first (becomes active)
        new_providers = [provider_entry] + other_providers
        
        # Write back the merged list directly to avoid deep_merge replacing lists
        scope_path = self.scope_path(scope)
        scope_settings = self._config._read_yaml(scope_path) or {}  # type: ignore[attr-defined]
        if "config" not in scope_settings:
            scope_settings["config"] = {}
        scope_settings["config"]["providers"] = new_providers
        self._config._write_yaml(scope_path, scope_settings)  # type: ignore[attr-defined]

    def clear_provider_override(self, scope: ScopeType) -> bool:
        """Clear provider override from a scope."""
        scope_path = self.scope_path(scope)
        scope_settings = self._config._read_yaml(scope_path) or {}  # type: ignore[attr-defined]
        config_section = scope_settings.get("config") or {}
        providers = config_section.get("providers")

        if isinstance(providers, list) and providers:
            config_section.pop("providers", None)

            if config_section:
                scope_settings["config"] = config_section
            elif "config" in scope_settings:
                scope_settings.pop("config", None)

            self._config._write_yaml(scope_path, scope_settings)  # type: ignore[attr-defined]
            return True

        return False

    def get_provider_overrides(self) -> list[dict[str, Any]]:
        """Return merged provider overrides (local > project > global)."""
        merged = self._config.get_merged_settings()
        providers = merged.get("config", {}).get("providers", [])
        return providers if isinstance(providers, list) else []

    def get_tool_overrides(self, session_id: str | None = None, project_slug: str | None = None) -> list[dict[str, Any]]:
        """Return merged tool overrides (session > local > project > global).

        Tool overrides allow settings like allowed_write_paths for tool-filesystem
        to be configured in user settings and applied to bundles.

        Args:
            session_id: Optional session ID to include session-scoped settings
            project_slug: Optional project slug (required if session_id provided)

        Expected structure in settings.yaml:
            modules:
              tools:
                - module: tool-filesystem
                  config:
                    allowed_write_paths:
                      - /path/to/dir
        """
        import yaml

        merged = self._config.get_merged_settings()
        tools = merged.get("modules", {}).get("tools", [])

        # Also check session-scoped settings if session context provided
        if session_id and project_slug:
            session_settings_path = (
                Path.home() / ".amplifier" / "projects" / project_slug / "sessions" / session_id / "settings.yaml"
            )
            if session_settings_path.exists():
                try:
                    with open(session_settings_path) as f:
                        session_settings = yaml.safe_load(f) or {}
                    session_tools = session_settings.get("modules", {}).get("tools", [])
                    if session_tools:
                        # Merge session tools with other scopes (session wins)
                        tools = self._merge_tool_lists(tools, session_tools)
                except Exception:
                    pass  # Skip malformed session settings
        return tools if isinstance(tools, list) else []

    def _merge_tool_lists(self, base: list[dict[str, Any]], overlay: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge tool lists, with overlay taking precedence for matching modules."""
        result = list(base)
        base_modules = {t.get("module"): i for i, t in enumerate(base) if isinstance(t, dict)}

        for tool in overlay:
            if not isinstance(tool, dict):
                continue
            module_id = tool.get("module")
            if module_id and module_id in base_modules:
                # Merge configs using shared helper (handles permission field unions)
                idx = base_modules[module_id]
                base_config = result[idx].get("config", {}) or {}
                overlay_config = tool.get("config", {}) or {}
                merged_config = merge_tool_configs(base_config, overlay_config)
                result[idx] = {**result[idx], **tool, "config": merged_config}
            else:
                result.append(tool)

        return result

    def get_scope_provider_overrides(self, scope: ScopeType) -> list[dict[str, Any]]:
        """Return provider overrides defined at a specific scope."""
        scope_path = self.scope_path(scope)
        scope_settings = self._config._read_yaml(scope_path) or {}  # type: ignore[attr-defined]
        config_section = scope_settings.get("config") or {}
        providers = config_section.get("providers", [])
        return providers if isinstance(providers, list) else []

    def apply_provider_overrides_to_profile(
        self, profile: Profile, overrides: list[dict[str, Any]] | None = None
    ) -> Profile:
        """Return a copy of `profile` with provider overrides applied.

        This uses a FILTER + MERGE strategy:
        - FILTER: Only providers that exist in overrides are included (prevents loading
          providers the user hasn't configured, which would fail without API keys)
        - MERGE: For providers that exist in both profile and overrides, config values
          are merged (profile as base, override on top) so profile settings like
          `debug: true` are preserved even if user only configured `model`

        This ensures:
        1. User selects Ollama → only Ollama loads (not Anthropic/OpenAI from profile)
        2. User selects Anthropic → gets profile's debug settings + their model choice
        """
        provider_overrides = overrides if overrides is not None else self.get_provider_overrides()
        if not provider_overrides:
            return profile

        # Build set of override module IDs for filtering
        override_ids = {entry.get("module") for entry in provider_overrides if entry.get("module")}

        # Normalize overrides into a dict for merging
        normalized_overrides: dict[str, dict[str, Any]] = {}
        for entry in provider_overrides:
            module_id = entry.get("module")
            if module_id and "source" not in entry:
                canonical = DEFAULT_PROVIDER_SOURCES.get(module_id)
                if canonical:
                    entry = {**entry, "source": canonical}
            if module_id:
                normalized_overrides[module_id] = entry

        providers: list[ModuleConfig] = []

        # FILTER: Only include profile providers that match an override
        # MERGE: Combine profile config (base) with override config (on top)
        for provider in profile.providers or []:
            if provider.module in override_ids:
                override_entry = normalized_overrides.pop(provider.module, None)
                if override_entry:
                    # Merge: profile config as base, override config on top
                    merged_config = {**(provider.config or {}), **(override_entry.get("config") or {})}
                    provider = ModuleConfig(
                        module=provider.module,
                        source=override_entry.get("source", provider.source),
                        config=merged_config or None,
                    )
                providers.append(provider)

        # Add any override providers not in profile (e.g., Ollama when profile only has Anthropic/OpenAI)
        for _module_id, entry in normalized_overrides.items():
            providers.append(ModuleConfig.model_validate(entry))

        return profile.model_copy(update={"providers": providers})
