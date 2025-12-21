"""Settings management for amplifier-app-cli.

Philosophy: Simple, scope-aware YAML settings designed for bundles-first world.
This is NOT legacy code - it's the clean target state.

The bundle codepath uses this module. The legacy profile/collection codepath
uses lib/legacy/config.py instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

import yaml

Scope = Literal["local", "project", "global"]


@dataclass
class SettingsPaths:
    """Standard paths for settings files."""

    global_settings: Path
    project_settings: Path
    local_settings: Path

    @classmethod
    def default(cls) -> SettingsPaths:
        """Create default paths for standard amplifier layout."""
        return cls(
            global_settings=Path.home() / ".amplifier" / "settings.yaml",
            project_settings=Path.cwd() / ".amplifier" / "settings.yaml",
            local_settings=Path.cwd() / ".amplifier" / "settings.local.yaml",
        )


class AppSettings:
    """Simple settings manager with scope-aware merging.

    Scope priority (most specific wins):
    1. local (.amplifier/settings.local.yaml) - gitignored, machine-specific
    2. project (.amplifier/settings.yaml) - committed, team-shared
    3. global (~/.amplifier/settings.yaml) - user defaults

    Usage:
        settings = AppSettings()
        bundle = settings.get_active_bundle()  # Returns name or None
        settings.set_active_bundle("foundation", scope="global")
    """

    def __init__(self, paths: SettingsPaths | None = None) -> None:
        self.paths = paths or SettingsPaths.default()

    def get_merged_settings(self) -> dict[str, Any]:
        """Load and merge settings from all scopes."""
        result: dict[str, Any] = {}
        for path in [self.paths.global_settings, self.paths.project_settings, self.paths.local_settings]:
            if path.exists():
                try:
                    with open(path) as f:
                        content = yaml.safe_load(f) or {}
                    result = self._deep_merge(result, content)
                except Exception:
                    pass  # Skip malformed files
        return result

    # ----- Bundle settings -----

    def get_active_bundle(self) -> str | None:
        """Get currently active bundle name."""
        settings = self.get_merged_settings()
        return settings.get("active_bundle")

    def set_active_bundle(self, name: str, scope: Scope = "global") -> None:
        """Set the active bundle at specified scope."""
        self._update_setting("active_bundle", name, scope)

    def clear_active_bundle(self, scope: Scope = "global") -> None:
        """Clear active bundle at specified scope."""
        self._remove_setting("active_bundle", scope)

    # ----- Provider settings -----

    def get_provider(self) -> dict[str, Any] | None:
        """Get active provider configuration."""
        settings = self.get_merged_settings()
        return settings.get("provider")

    def set_provider(self, provider_config: dict[str, Any], scope: Scope = "global") -> None:
        """Set active provider configuration."""
        self._update_setting("provider", provider_config, scope)

    def clear_provider(self, scope: Scope = "global") -> None:
        """Clear provider at specified scope."""
        self._remove_setting("provider", scope)

    # ----- Legacy profile support (for backward compatibility) -----

    def get_active_profile(self) -> str | None:
        """Get currently active profile name (legacy support)."""
        settings = self.get_merged_settings()
        return settings.get("active_profile")

    def set_active_profile(self, name: str, scope: Scope = "global") -> None:
        """Set the active profile at specified scope (legacy support)."""
        self._update_setting("active_profile", name, scope)

    def clear_active_profile(self, scope: Scope = "global") -> None:
        """Clear active profile at specified scope (legacy support)."""
        self._remove_setting("active_profile", scope)

    # ----- Override settings (dev overrides) -----

    def get_overrides(self) -> dict[str, Any]:
        """Get development overrides section."""
        settings = self.get_merged_settings()
        return settings.get("overrides", {})

    # ----- Scope utilities -----

    def _get_scope_path(self, scope: Scope) -> Path:
        """Get settings file path for scope."""
        return {
            "local": self.paths.local_settings,
            "project": self.paths.project_settings,
            "global": self.paths.global_settings,
        }[scope]

    def _read_scope(self, scope: Scope) -> dict[str, Any]:
        """Read settings from a specific scope."""
        path = self._get_scope_path(scope)
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def _write_scope(self, scope: Scope, settings: dict[str, Any]) -> None:
        """Write settings to a specific scope."""
        path = self._get_scope_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(settings, f, default_flow_style=False)

    def _update_setting(self, key: str, value: Any, scope: Scope) -> None:
        """Update a single setting at specified scope."""
        settings = self._read_scope(scope)
        settings[key] = value
        self._write_scope(scope, settings)

    def _remove_setting(self, key: str, scope: Scope) -> None:
        """Remove a setting from specified scope."""
        settings = self._read_scope(scope)
        if key in settings:
            del settings[key]
            self._write_scope(scope, settings)

    def _deep_merge(self, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        """Deep merge two dicts, overlay wins."""
        result = base.copy()
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


# Convenience function for quick access
def get_settings() -> AppSettings:
    """Get a settings instance with default paths."""
    return AppSettings()
