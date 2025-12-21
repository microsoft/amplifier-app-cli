"""LEGACY: Minimal ConfigManager. DELETE when profiles/collections removed.

This is a temporary internalization of amplifier-config functionality
to allow removing the external dependency. The bundle codepath uses
lib/settings.py instead - it should NEVER import from this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class Scope(Enum):
    """Configuration scope (user, project, local)."""

    USER = "user"  # ~/.amplifier/settings.yaml
    PROJECT = "project"  # .amplifier/settings.yaml
    LOCAL = "local"  # .amplifier/settings.local.yaml


@dataclass
class ConfigPaths:
    """Configuration file paths for each scope.

    Paths can be None to indicate that scope is disabled (e.g., when running
    from home directory, project/local scopes are disabled).
    """

    user: Path
    project: Path | None = None
    local: Path | None = None


class ConfigManager:
    """LEGACY: Manage YAML config with scope-based merging.

    DELETE when profiles/collections removed. Bundle codepath should
    use lib/settings.py instead.

    Settings are merged in order: user < project < local (most specific wins).
    """

    def __init__(self, paths: ConfigPaths) -> None:
        self.paths = paths

    # ----- Scope utilities -----

    def scope_to_path(self, scope: Scope) -> Path | None:
        """Get the filesystem path for a scope, or None if disabled."""
        return {
            Scope.USER: self.paths.user,
            Scope.PROJECT: self.paths.project,
            Scope.LOCAL: self.paths.local,
        }[scope]

    def is_scope_available(self, scope: Scope) -> bool:
        """Check if a scope is available (has a valid path)."""
        return self.scope_to_path(scope) is not None

    # ----- YAML helpers -----

    def _read_yaml(self, path: Path | None) -> dict[str, Any] | None:
        """Read YAML file, returning None if not found or invalid."""
        if path is None or not path.exists():
            return None
        try:
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return None

    def _write_yaml(self, path: Path | None, data: dict[str, Any]) -> None:
        """Write YAML file, creating parent directories if needed."""
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False)

    def _deep_merge(self, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        """Deep merge two dicts, overlay wins."""
        result = base.copy()
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    # ----- Core settings -----

    def get_merged_settings(self) -> dict[str, Any]:
        """Load and merge settings from all scopes (user < project < local)."""
        result: dict[str, Any] = {}
        for scope in [Scope.USER, Scope.PROJECT, Scope.LOCAL]:
            path = self.scope_to_path(scope)
            content = self._read_yaml(path)
            if content:
                result = self._deep_merge(result, content)
        return result

    def update_settings(self, updates: dict[str, Any], scope: Scope = Scope.USER) -> None:
        """Update settings at specified scope."""
        path = self.scope_to_path(scope)
        if path is None:
            return

        settings = self._read_yaml(path) or {}
        settings = self._deep_merge(settings, updates)
        self._write_yaml(path, settings)

    # ----- Profile management -----

    def get_active_profile(self) -> str | None:
        """Get currently active profile name from merged settings."""
        merged = self.get_merged_settings()
        # Check profile.active first (new location), then active_profile (legacy)
        profile_section = merged.get("profile", {})
        if isinstance(profile_section, dict) and "active" in profile_section:
            return profile_section["active"]
        return merged.get("active_profile")

    def set_active_profile(self, name: str, scope: Scope = Scope.LOCAL) -> None:
        """Set the active profile at specified scope."""
        self.update_settings({"profile": {"active": name}}, scope=scope)

    def clear_active_profile(self, scope: Scope = Scope.LOCAL) -> None:
        """Clear active profile from specified scope."""
        path = self.scope_to_path(scope)
        if path is None:
            return

        settings = self._read_yaml(path) or {}
        profile_section = settings.get("profile", {})

        if isinstance(profile_section, dict) and "active" in profile_section:
            del profile_section["active"]
            if not profile_section:
                settings.pop("profile", None)
            else:
                settings["profile"] = profile_section
            self._write_yaml(path, settings)

        # Also check legacy location
        if "active_profile" in settings:
            del settings["active_profile"]
            self._write_yaml(path, settings)

    # ----- Project default -----

    def get_project_default(self) -> str | None:
        """Get project default profile from project scope only."""
        content = self._read_yaml(self.paths.project)
        if content:
            profile_section = content.get("profile", {})
            if isinstance(profile_section, dict):
                return profile_section.get("default")
        return None

    def set_project_default(self, name: str) -> None:
        """Set project default profile (in project scope only)."""
        if self.paths.project is None:
            return
        settings = self._read_yaml(self.paths.project) or {}
        profile_section = settings.get("profile", {})
        if not isinstance(profile_section, dict):
            profile_section = {}
        profile_section["default"] = name
        settings["profile"] = profile_section
        self._write_yaml(self.paths.project, settings)

    def clear_project_default(self) -> None:
        """Clear project default profile."""
        if self.paths.project is None:
            return
        settings = self._read_yaml(self.paths.project) or {}
        profile_section = settings.get("profile", {})
        if isinstance(profile_section, dict) and "default" in profile_section:
            del profile_section["default"]
            if not profile_section:
                settings.pop("profile", None)
            else:
                settings["profile"] = profile_section
            self._write_yaml(self.paths.project, settings)

    # ----- Module source overrides -----

    def get_module_sources(self) -> dict[str, str]:
        """Get all module source overrides from merged settings."""
        merged = self.get_merged_settings()
        sources = merged.get("sources", {}).get("modules", {})
        return sources if isinstance(sources, dict) else {}

    def add_source_override(self, module_id: str, source_uri: str, scope: Scope = Scope.USER) -> None:
        """Add a module source override at specified scope."""
        path = self.scope_to_path(scope)
        if path is None:
            return

        settings = self._read_yaml(path) or {}
        if "sources" not in settings:
            settings["sources"] = {}
        if "modules" not in settings["sources"]:
            settings["sources"]["modules"] = {}
        settings["sources"]["modules"][module_id] = source_uri
        self._write_yaml(path, settings)

    def remove_source_override(self, module_id: str, scope: Scope = Scope.USER) -> bool:
        """Remove a module source override from specified scope. Returns True if removed."""
        path = self.scope_to_path(scope)
        if path is None:
            return False

        settings = self._read_yaml(path) or {}
        modules = settings.get("sources", {}).get("modules", {})
        if isinstance(modules, dict) and module_id in modules:
            del modules[module_id]
            if not modules:
                settings["sources"].pop("modules", None)
            if not settings.get("sources"):
                settings.pop("sources", None)
            self._write_yaml(path, settings)
            return True
        return False

    # ----- Collection source overrides -----

    def get_collection_sources(self) -> dict[str, str]:
        """Get all collection source overrides from merged settings."""
        merged = self.get_merged_settings()
        sources = merged.get("sources", {}).get("collections", {})
        return sources if isinstance(sources, dict) else {}

    def add_collection_source_override(self, collection_id: str, source_uri: str, scope: Scope = Scope.USER) -> None:
        """Add a collection source override at specified scope."""
        path = self.scope_to_path(scope)
        if path is None:
            return

        settings = self._read_yaml(path) or {}
        if "sources" not in settings:
            settings["sources"] = {}
        if "collections" not in settings["sources"]:
            settings["sources"]["collections"] = {}
        settings["sources"]["collections"][collection_id] = source_uri
        self._write_yaml(path, settings)

    def remove_collection_source_override(self, collection_id: str, scope: Scope = Scope.USER) -> bool:
        """Remove a collection source override from specified scope. Returns True if removed."""
        path = self.scope_to_path(scope)
        if path is None:
            return False

        settings = self._read_yaml(path) or {}
        collections = settings.get("sources", {}).get("collections", {})
        if isinstance(collections, dict) and collection_id in collections:
            del collections[collection_id]
            if not collections:
                settings["sources"].pop("collections", None)
            if not settings.get("sources"):
                settings.pop("sources", None)
            self._write_yaml(path, settings)
            return True
        return False
