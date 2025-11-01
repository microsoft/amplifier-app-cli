"""Shared application-level settings helpers.

These helpers live in the CLI repo for now so we can finalize their shape
before extracting them into a dedicated library for other front ends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from amplifier_config import ConfigManager
from amplifier_config import Scope
from amplifier_profiles.schema import ModuleConfig
from amplifier_profiles.schema import Profile

from ...provider_sources import DEFAULT_PROVIDER_SOURCES

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

    def scope_path(self, scope: ScopeType) -> Path:
        """Return the filesystem path for a scope."""
        return self._config.scope_to_path(self._scope_enum(scope))

    # ----- Provider overrides -----

    def set_provider_override(self, provider_entry: dict[str, Any], scope: ScopeType) -> None:
        """Persist provider override at a specific scope."""
        self._config.update_settings({"config": {"providers": [provider_entry]}}, scope=self._scope_enum(scope))

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
        """Return a copy of `profile` with provider overrides applied."""
        provider_overrides = overrides if overrides is not None else self.get_provider_overrides()
        if not provider_overrides:
            return profile

        normalized_overrides: list[dict[str, Any]] = []
        for entry in provider_overrides:
            module_id = entry.get("module")
            if module_id and "source" not in entry:
                canonical = DEFAULT_PROVIDER_SOURCES.get(module_id)
                if canonical:
                    entry = {**entry, "source": canonical}
            normalized_overrides.append(entry)

        module_configs = [ModuleConfig.model_validate(entry) for entry in normalized_overrides]
        return profile.model_copy(update={"providers": module_configs})
