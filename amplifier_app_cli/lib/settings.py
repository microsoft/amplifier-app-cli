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

Scope = Literal["local", "project", "global", "session"]


@dataclass
class SettingsPaths:
    """Standard paths for settings files."""

    global_settings: Path
    project_settings: Path
    local_settings: Path
    session_settings: Path | None = None  # Set dynamically when session is known

    @classmethod
    def default(cls) -> SettingsPaths:
        """Create default paths for standard amplifier layout."""
        return cls(
            global_settings=Path.home() / ".amplifier" / "settings.yaml",
            project_settings=Path.cwd() / ".amplifier" / "settings.yaml",
            local_settings=Path.cwd() / ".amplifier" / "settings.local.yaml",
            session_settings=None,
        )

    @classmethod
    def with_session(cls, session_id: str, project_slug: str) -> SettingsPaths:
        """Create paths including session-scoped settings."""
        base = cls.default()
        base.session_settings = (
            Path.home() / ".amplifier" / "projects" / project_slug / "sessions" / session_id / "settings.yaml"
        )
        return base


class AppSettings:
    """Simple settings manager with scope-aware merging.

    Scope priority (most specific wins):
    1. session (~/.amplifier/projects/<slug>/sessions/<id>/settings.yaml) - session-specific
    2. local (.amplifier/settings.local.yaml) - gitignored, machine-specific
    3. project (.amplifier/settings.yaml) - committed, team-shared
    4. global (~/.amplifier/settings.yaml) - user defaults

    Usage:
        settings = AppSettings()
        bundle = settings.get_active_bundle()  # Returns name or None
        settings.set_active_bundle("foundation", scope="global")
    """

    def __init__(self, paths: SettingsPaths | None = None) -> None:
        self.paths = paths or SettingsPaths.default()

    def with_session(self, session_id: str, project_slug: str) -> "AppSettings":
        """Return a new AppSettings instance with session scope enabled."""
        new_paths = SettingsPaths.with_session(session_id, project_slug)
        return AppSettings(new_paths)

    def get_merged_settings(self) -> dict[str, Any]:
        """Load and merge settings from all scopes."""
        result: dict[str, Any] = {}
        # Order: global -> project -> local -> session (most specific wins)
        paths_to_check = [
            self.paths.global_settings,
            self.paths.project_settings,
            self.paths.local_settings,
        ]
        if self.paths.session_settings:
            paths_to_check.append(self.paths.session_settings)

        for path in paths_to_check:
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
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

    # ----- Allowed write paths settings -----

    def get_allowed_write_paths(self) -> list[tuple[str, str]]:
        """Return list of (path, scope) tuples, merged across all scopes.

        Returns paths from all scopes with their source scope for display.
        Paths are deduplicated - if same path appears in multiple scopes,
        the most specific scope wins.
        """
        result: list[tuple[str, str]] = []
        seen_paths: set[str] = set()

        # Order from most specific to least specific for deduplication
        scopes_to_check: list[tuple[Scope, Path | None]] = [
            ("session", self.paths.session_settings),
            ("local", self.paths.local_settings),
            ("project", self.paths.project_settings),
            ("global", self.paths.global_settings),
        ]

        for scope_name, path in scopes_to_check:
            if path is None or not path.exists():
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    content = yaml.safe_load(f) or {}
                paths_list = (
                    content.get("modules", {})
                    .get("tools", [{}])[0]
                    .get("config", {})
                    .get("allowed_write_paths", [])
                )
                # Handle case where tools is a list with tool-filesystem entry
                if not paths_list:
                    tools_list = content.get("modules", {}).get("tools", [])
                    for tool in tools_list:
                        if isinstance(tool, dict) and tool.get("module") == "tool-filesystem":
                            paths_list = tool.get("config", {}).get("allowed_write_paths", [])
                            break

                for p in paths_list:
                    if p not in seen_paths:
                        result.append((p, scope_name))
                        seen_paths.add(p)
            except Exception:
                pass  # Skip malformed files

        return result

    def add_allowed_write_path(self, path: str, scope: Scope = "global") -> None:
        """Add path to allowed_write_paths at specified scope.

        Args:
            path: Absolute path to allow writes to
            scope: Where to store the setting (global, project, local, session)
        """
        # Resolve to absolute path
        resolved = str(Path(path).resolve())

        settings = self._read_scope(scope)

        # Ensure modules.tools structure exists
        if "modules" not in settings:
            settings["modules"] = {}
        if "tools" not in settings["modules"]:
            settings["modules"]["tools"] = []

        # Find or create tool-filesystem entry
        tools_list = settings["modules"]["tools"]
        fs_tool = None
        for tool in tools_list:
            if isinstance(tool, dict) and tool.get("module") == "tool-filesystem":
                fs_tool = tool
                break

        if fs_tool is None:
            fs_tool = {"module": "tool-filesystem", "config": {"allowed_write_paths": []}}
            tools_list.append(fs_tool)

        if "config" not in fs_tool:
            fs_tool["config"] = {}
        if "allowed_write_paths" not in fs_tool["config"]:
            fs_tool["config"]["allowed_write_paths"] = []

        # Add path if not already present
        if resolved not in fs_tool["config"]["allowed_write_paths"]:
            fs_tool["config"]["allowed_write_paths"].append(resolved)

        self._write_scope(scope, settings)

    def remove_allowed_write_path(self, path: str, scope: Scope = "global") -> bool:
        """Remove path from allowed_write_paths at specified scope.

        Args:
            path: Path to remove (will be resolved to absolute)
            scope: Which scope to remove from

        Returns:
            True if path was found and removed, False otherwise
        """
        # Resolve to absolute path for matching
        resolved = str(Path(path).resolve())

        settings = self._read_scope(scope)

        tools_list = settings.get("modules", {}).get("tools", [])
        for tool in tools_list:
            if isinstance(tool, dict) and tool.get("module") == "tool-filesystem":
                paths_list = tool.get("config", {}).get("allowed_write_paths", [])
                if resolved in paths_list:
                    paths_list.remove(resolved)
                    self._write_scope(scope, settings)
                    return True
                # Also try matching the original path
                if path in paths_list:
                    paths_list.remove(path)
                    self._write_scope(scope, settings)
                    return True

        return False

    # ----- Denied write paths settings -----

    def _get_tool_config_paths(self, content: dict, key: str) -> list[str]:
        """Extract a path list from tool-filesystem config.

        Args:
            content: Parsed YAML content
            key: Config key to extract (e.g., 'allowed_write_paths', 'denied_write_paths')

        Returns:
            List of paths, or empty list if not found
        """
        # Try first tool's config
        paths_list = (
            content.get("modules", {})
            .get("tools", [{}])[0]
            .get("config", {})
            .get(key, [])
        )
        # Handle case where tools is a list with tool-filesystem entry
        if not paths_list:
            tools_list = content.get("modules", {}).get("tools", [])
            for tool in tools_list:
                if isinstance(tool, dict) and tool.get("module") == "tool-filesystem":
                    paths_list = tool.get("config", {}).get(key, [])
                    break
        return paths_list

    def _ensure_fs_tool_config(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Ensure modules.tools.tool-filesystem.config structure exists.

        Returns the tool-filesystem config dict for modification.
        """
        if "modules" not in settings:
            settings["modules"] = {}
        if "tools" not in settings["modules"]:
            settings["modules"]["tools"] = []

        tools_list = settings["modules"]["tools"]
        fs_tool = None
        for tool in tools_list:
            if isinstance(tool, dict) and tool.get("module") == "tool-filesystem":
                fs_tool = tool
                break

        if fs_tool is None:
            fs_tool = {"module": "tool-filesystem", "config": {}}
            tools_list.append(fs_tool)

        if "config" not in fs_tool:
            fs_tool["config"] = {}

        return fs_tool

    def get_denied_write_paths(self) -> list[tuple[str, str]]:
        """Return list of (path, scope) tuples for denied paths.

        Returns paths from all scopes with their source scope for display.
        Paths are deduplicated - if same path appears in multiple scopes,
        the most specific scope wins.
        """
        result: list[tuple[str, str]] = []
        seen_paths: set[str] = set()

        # Order from most specific to least specific for deduplication
        scopes_to_check: list[tuple[Scope, Path | None]] = [
            ("session", self.paths.session_settings),
            ("local", self.paths.local_settings),
            ("project", self.paths.project_settings),
            ("global", self.paths.global_settings),
        ]

        for scope_name, path in scopes_to_check:
            if path is None or not path.exists():
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    content = yaml.safe_load(f) or {}
                paths_list = self._get_tool_config_paths(content, "denied_write_paths")
                for p in paths_list:
                    if p not in seen_paths:
                        result.append((p, scope_name))
                        seen_paths.add(p)
            except Exception:
                pass  # Skip malformed files

        return result

    def add_denied_write_path(self, path: str, scope: Scope = "global") -> None:
        """Add path to denied_write_paths at specified scope.

        Args:
            path: Absolute path to deny writes to
            scope: Where to store the setting (global, project, local, session)
        """
        resolved = str(Path(path).resolve())
        settings = self._read_scope(scope)

        fs_tool = self._ensure_fs_tool_config(settings)

        if "denied_write_paths" not in fs_tool["config"]:
            fs_tool["config"]["denied_write_paths"] = []

        if resolved not in fs_tool["config"]["denied_write_paths"]:
            fs_tool["config"]["denied_write_paths"].append(resolved)

        self._write_scope(scope, settings)

    def remove_denied_write_path(self, path: str, scope: Scope = "global") -> bool:
        """Remove path from denied_write_paths at specified scope.

        Args:
            path: Path to remove (will be resolved to absolute)
            scope: Which scope to remove from

        Returns:
            True if path was found and removed, False otherwise
        """
        resolved = str(Path(path).resolve())
        settings = self._read_scope(scope)

        tools_list = settings.get("modules", {}).get("tools", [])
        for tool in tools_list:
            if isinstance(tool, dict) and tool.get("module") == "tool-filesystem":
                paths_list = tool.get("config", {}).get("denied_write_paths", [])
                if resolved in paths_list:
                    paths_list.remove(resolved)
                    self._write_scope(scope, settings)
                    return True
                # Also try matching the original path
                if path in paths_list:
                    paths_list.remove(path)
                    self._write_scope(scope, settings)
                    return True

        return False

    # ----- Scope utilities -----

    def _get_scope_path(self, scope: Scope) -> Path:
        """Get settings file path for scope."""
        scope_map: dict[Scope, Path | None] = {
            "session": self.paths.session_settings,
            "local": self.paths.local_settings,
            "project": self.paths.project_settings,
            "global": self.paths.global_settings,
        }
        path = scope_map.get(scope)
        if path is None:
            if scope == "session":
                raise ValueError("Session scope requires session_id to be set. Use with_session() first.")
            raise ValueError(f"Unknown scope: {scope}")
        return path

    def _read_scope(self, scope: Scope) -> dict[str, Any]:
        """Read settings from a specific scope."""
        path = self._get_scope_path(scope)
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def _write_scope(self, scope: Scope, settings: dict[str, Any]) -> None:
        """Write settings to a specific scope."""
        path = self._get_scope_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
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
