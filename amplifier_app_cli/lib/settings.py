"""Settings management for amplifier-app-cli.

Philosophy: Simple, scope-aware YAML settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

import yaml

Scope = Literal["local", "project", "global", "session"]

# Backward compatibility alias (OLD AppSettings used ScopeType without "session")
ScopeType = Literal["local", "project", "global"]


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
            Path.home()
            / ".amplifier"
            / "projects"
            / project_slug
            / "sessions"
            / session_id
            / "settings.yaml"
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
        """Get currently active bundle name.

        Reads from bundle.active setting.
        """
        settings = self.get_merged_settings()
        bundle_settings = settings.get("bundle") or {}
        return (
            bundle_settings.get("active") if isinstance(bundle_settings, dict) else None
        )

    def set_active_bundle(self, name: str, scope: Scope = "global") -> None:
        """Set the active bundle at specified scope.

        Writes to bundle.active path for compatibility with bundle commands.
        """
        settings = self._read_scope(scope)
        if "bundle" not in settings:
            settings["bundle"] = {}
        settings["bundle"]["active"] = name
        self._write_scope(scope, settings)

    def clear_active_bundle(self, scope: Scope = "global") -> None:
        """Clear active bundle at specified scope.

        Only removes bundle.active, preserving bundle.added and bundle.app.
        This allows inheritance from lower-priority scopes while keeping
        user-added bundles intact.
        """
        settings = self._read_scope(scope)
        if "bundle" in settings and "active" in settings["bundle"]:
            del settings["bundle"]["active"]
            # Clean up empty bundle section
            if not settings["bundle"]:
                del settings["bundle"]
            self._write_scope(scope, settings)

    # ----- App bundle settings -----

    def get_app_bundles(self) -> list[str]:
        """Get list of app bundle URIs that are always composed.

        App bundles are composed onto every session AFTER the primary bundle.
        They enable team-wide or user-wide behaviors.

        Reads from bundle.app setting (list of URIs).
        """
        settings = self.get_merged_settings()
        bundle_settings = settings.get("bundle") or {}
        app_bundles = bundle_settings.get("app", [])
        return app_bundles if isinstance(app_bundles, list) else []

    def add_app_bundle(self, uri: str, scope: Scope = "global") -> None:
        """Add an app bundle URI at specified scope.

        Args:
            uri: Bundle URI (e.g., "git+https://github.com/org/bundle@main")
            scope: Where to store (global, project, local)
        """
        settings = self._read_scope(scope)
        if "bundle" not in settings:
            settings["bundle"] = {}
        if "app" not in settings["bundle"]:
            settings["bundle"]["app"] = []

        # Add if not already present
        if uri not in settings["bundle"]["app"]:
            settings["bundle"]["app"].append(uri)

        self._write_scope(scope, settings)

    def remove_app_bundle(self, uri: str, scope: Scope = "global") -> bool:
        """Remove an app bundle URI from specified scope.

        Returns True if found and removed, False otherwise.
        """
        settings = self._read_scope(scope)
        app_bundles = settings.get("bundle", {}).get("app", [])

        if uri in app_bundles:
            app_bundles.remove(uri)
            # Clean up empty structures
            if not app_bundles:
                settings.get("bundle", {}).pop("app", None)
            if not settings.get("bundle"):
                settings.pop("bundle", None)
            self._write_scope(scope, settings)
            return True
        return False

    # ----- Added bundle settings (bundle.added) -----
    # User-added bundles via `bundle add` - name → URI mappings
    # This replaces the separate bundle-registry.yaml file

    def get_added_bundles(self) -> dict[str, str]:
        """Get user-added bundle mappings (name → URI).

        Returns merged bundle.added from all scopes.
        Also migrates from legacy bundle-registry.yaml if present.
        """
        # First, check for legacy bundle-registry.yaml and migrate if needed
        self._migrate_legacy_registry()

        settings = self.get_merged_settings()
        bundle_settings = settings.get("bundle") or {}
        added = bundle_settings.get("added", {})
        return added if isinstance(added, dict) else {}

    def add_bundle(self, name: str, uri: str, scope: Scope = "global") -> None:
        """Add a bundle to bundle.added at specified scope.

        Args:
            name: Bundle name (e.g., "my-custom-bundle")
            uri: Bundle URI (e.g., "git+https://github.com/org/bundle@main")
            scope: Where to store (global, project, local)
        """
        settings = self._read_scope(scope)
        if "bundle" not in settings:
            settings["bundle"] = {}
        if "added" not in settings["bundle"]:
            settings["bundle"]["added"] = {}

        settings["bundle"]["added"][name] = uri
        self._write_scope(scope, settings)

    def remove_added_bundle(self, name: str, scope: Scope = "global") -> bool:
        """Remove a bundle from bundle.added at specified scope.

        Returns True if found and removed, False otherwise.
        """
        settings = self._read_scope(scope)
        added = settings.get("bundle", {}).get("added", {})

        if name in added:
            del added[name]
            # Clean up empty structures
            if not added:
                settings.get("bundle", {}).pop("added", None)
            if not settings.get("bundle"):
                settings.pop("bundle", None)
            self._write_scope(scope, settings)
            return True
        return False

    def _migrate_legacy_registry(self) -> None:
        """Migrate bundles from legacy bundle-registry.yaml to settings.yaml.

        This is a one-time migration that:
        1. Reads bundle-registry.yaml if it exists
        2. Adds entries to bundle.added in global settings.yaml
        3. Renames bundle-registry.yaml to bundle-registry.yaml.migrated

        Migration is idempotent - skipped if already migrated.
        """
        legacy_path = Path.home() / ".amplifier" / "bundle-registry.yaml"
        migrated_marker = Path.home() / ".amplifier" / "bundle-registry.yaml.migrated"

        # Skip if no legacy file or already migrated
        if not legacy_path.exists() or migrated_marker.exists():
            return

        try:
            import logging

            logger = logging.getLogger(__name__)

            with open(legacy_path, encoding="utf-8") as f:
                legacy_data = yaml.safe_load(f) or {}

            legacy_bundles = legacy_data.get("bundles", {})
            if not legacy_bundles:
                # Empty registry, just rename and done
                legacy_path.rename(migrated_marker)
                return

            # Migrate each bundle to settings.yaml
            migrated_count = 0
            for name, info in legacy_bundles.items():
                uri = info.get("uri") if isinstance(info, dict) else None
                if uri:
                    self.add_bundle(name, uri, scope="global")
                    migrated_count += 1

            # Rename legacy file to mark migration complete
            legacy_path.rename(migrated_marker)
            logger.info(
                f"Migrated {migrated_count} bundles from bundle-registry.yaml to settings.yaml"
            )

        except Exception as e:
            # Don't fail on migration errors - just log and continue
            import logging

            logging.getLogger(__name__).warning(
                f"Failed to migrate bundle-registry.yaml: {e}"
            )

    # ----- Provider settings -----

    def get_provider(self) -> dict[str, Any] | None:
        """Get active provider configuration."""
        settings = self.get_merged_settings()
        return settings.get("provider")

    def set_provider(
        self, provider_config: dict[str, Any], scope: Scope = "global"
    ) -> None:
        """Set active provider configuration."""
        self._update_setting("provider", provider_config, scope)

    def clear_provider(self, scope: Scope = "global") -> None:
        """Clear provider at specified scope."""
        self._remove_setting("provider", scope)

    # ----- Provider override settings (config.providers) -----

    def get_provider_overrides(self) -> list[dict[str, Any]]:
        """Return merged provider overrides from config.providers.

        This is the list of configured providers with their settings.
        """
        settings = self.get_merged_settings()
        providers = settings.get("config", {}).get("providers", [])
        return providers if isinstance(providers, list) else []

    def set_provider_override(
        self, provider_entry: dict[str, Any], scope: Scope = "global"
    ) -> None:
        """Persist provider override at a specific scope.

        Updates or adds the provider entry. The new/updated provider
        is moved to the front (becomes active). Other priority-1 providers
        are demoted to priority 10.
        """
        existing_providers = self.get_scope_provider_overrides(scope)
        module_id = provider_entry.get("module")
        other_providers = []

        for provider in existing_providers:
            if provider.get("module") == module_id:
                continue  # Skip - we'll add the new entry at the front
            else:
                # Demote any other priority-1 providers to priority 10
                config = provider.get("config", {})
                if isinstance(config, dict) and config.get("priority") == 1:
                    provider = {**provider, "config": {**config, "priority": 10}}
                other_providers.append(provider)

        # New provider goes first (becomes active)
        new_providers = [provider_entry] + other_providers

        settings = self._read_scope(scope)
        if "config" not in settings:
            settings["config"] = {}
        settings["config"]["providers"] = new_providers
        self._write_scope(scope, settings)

    def clear_provider_override(self, scope: Scope = "global") -> bool:
        """Clear provider override from a scope. Returns True if cleared."""
        settings = self._read_scope(scope)
        config_section = settings.get("config") or {}
        providers = config_section.get("providers")

        if isinstance(providers, list) and providers:
            config_section.pop("providers", None)
            if config_section:
                settings["config"] = config_section
            elif "config" in settings:
                settings.pop("config", None)
            self._write_scope(scope, settings)
            return True
        return False

    def get_scope_provider_overrides(self, scope: Scope) -> list[dict[str, Any]]:
        """Return provider overrides defined at a specific scope."""
        settings = self._read_scope(scope)
        config_section = settings.get("config") or {}
        providers = config_section.get("providers", [])
        return providers if isinstance(providers, list) else []

    # ----- Override settings (dev overrides) -----

    def get_overrides(self) -> dict[str, Any]:
        """Get development overrides section."""
        settings = self.get_merged_settings()
        return settings.get("overrides", {})

    # ----- Routing settings -----

    def get_routing_config(self) -> dict[str, Any]:
        """Get routing configuration section.

        Returns the 'routing' section from merged settings, used for
        model-class routing strategy configuration.
        """
        settings = self.get_merged_settings()
        return settings.get("routing", {})

    # ----- Source override settings -----

    def get_module_sources(self) -> dict[str, str]:
        """Get merged module source overrides from all scopes."""
        settings = self.get_merged_settings()
        return settings.get("sources", {}).get("modules", {})

    def add_source_override(
        self, identifier: str, source_uri: str, scope: Scope = "global"
    ) -> None:
        """Add a module source override at specified scope."""
        settings = self._read_scope(scope)
        if "sources" not in settings:
            settings["sources"] = {}
        if "modules" not in settings["sources"]:
            settings["sources"]["modules"] = {}
        settings["sources"]["modules"][identifier] = source_uri
        self._write_scope(scope, settings)

    def remove_source_override(self, identifier: str, scope: Scope = "global") -> bool:
        """Remove a module source override. Returns True if found and removed."""
        settings = self._read_scope(scope)
        modules = settings.get("sources", {}).get("modules", {})
        if identifier in modules:
            del modules[identifier]
            # Clean up empty dicts
            if not modules and "modules" in settings.get("sources", {}):
                del settings["sources"]["modules"]
            if not settings.get("sources"):
                settings.pop("sources", None)
            self._write_scope(scope, settings)
            return True
        return False

    def get_bundle_sources(self) -> dict[str, str]:
        """Get merged bundle source overrides from all scopes."""
        settings = self.get_merged_settings()
        return settings.get("sources", {}).get("bundles", {})

    def add_bundle_source_override(
        self, identifier: str, source_uri: str, scope: Scope = "global"
    ) -> None:
        """Add a bundle source override at specified scope."""
        settings = self._read_scope(scope)
        if "sources" not in settings:
            settings["sources"] = {}
        if "bundles" not in settings["sources"]:
            settings["sources"]["bundles"] = {}
        settings["sources"]["bundles"][identifier] = source_uri
        self._write_scope(scope, settings)

    def remove_bundle_source_override(
        self, identifier: str, scope: Scope = "global"
    ) -> bool:
        """Remove a bundle source override. Returns True if found and removed."""
        settings = self._read_scope(scope)
        bundles = settings.get("sources", {}).get("bundles", {})
        if identifier in bundles:
            del bundles[identifier]
            if not bundles and "bundles" in settings.get("sources", {}):
                del settings["sources"]["bundles"]
            if not settings.get("sources"):
                settings.pop("sources", None)
            self._write_scope(scope, settings)
            return True
        return False

    # ----- Notification settings (config.notifications) -----

    def get_notification_config(self) -> dict[str, Any]:
        """Return merged notification config from config.notifications.

        Expected structure:
            config:
              notifications:
                desktop:
                  enabled: true
                push:
                  enabled: true
        """
        settings = self.get_merged_settings()
        notifications = settings.get("config", {}).get("notifications", {})
        return notifications if isinstance(notifications, dict) else {}

    def get_notification_hook_overrides(self) -> list[dict[str, Any]]:
        """Return hook overrides derived from notification settings.

        Maps config.notifications.* settings to hook module configs.
        """
        notifications = self.get_notification_config()
        overrides: list[dict[str, Any]] = []

        # Desktop notifications (enabled by default)
        desktop_config = notifications.get("desktop", {})
        if desktop_config.get("enabled", True):
            hook_config: dict[str, Any] = {"enabled": True}
            for key in [
                "show_device",
                "show_project",
                "show_preview",
                "preview_length",
                "subtitle",
                "suppress_if_focused",
                "min_iterations",
                "show_iteration_count",
                "sound",
                "debug",
            ]:
                if key in desktop_config:
                    hook_config[key] = desktop_config[key]
            overrides.append({"module": "hooks-notify", "config": hook_config})

        # Push notifications (ntfy)
        ntfy_config = notifications.get("ntfy", {})
        push_config = notifications.get("push", {})
        combined_push = {**push_config, **ntfy_config}

        if combined_push and combined_push.get("enabled", False):
            hook_config = {"enabled": True, "service": "ntfy"}
            for key in ["server", "priority", "tags", "debug"]:
                if key in combined_push:
                    hook_config[key] = combined_push[key]
            overrides.append({"module": "hooks-notify-push", "config": hook_config})

        return overrides

    def set_notification_config(
        self, notification_type: str, config: dict[str, Any], scope: Scope = "global"
    ) -> None:
        """Set notification config at specified scope.

        Args:
            notification_type: "desktop" or "ntfy"
            config: Config dict (enabled, topic, etc.)
            scope: Where to save
        """
        settings = self._read_scope(scope)
        if "config" not in settings:
            settings["config"] = {}
        if "notifications" not in settings["config"]:
            settings["config"]["notifications"] = {}
        settings["config"]["notifications"][notification_type] = config
        self._write_scope(scope, settings)

    def clear_notification_config(
        self, notification_type: str | None, scope: Scope = "global"
    ) -> None:
        """Clear notification config at specified scope.

        Args:
            notification_type: "desktop", "ntfy", or None to clear all
            scope: Where to clear from
        """
        settings = self._read_scope(scope)
        notifications = settings.get("config", {}).get("notifications", {})
        if not notifications:
            return

        if notification_type:
            notifications.pop(notification_type, None)
        else:
            notifications.clear()

        # Clean up empty structures
        if not notifications:
            settings.get("config", {}).pop("notifications", None)
        if not settings.get("config"):
            settings.pop("config", None)
        self._write_scope(scope, settings)

    # ----- Scope availability -----

    def is_scope_available(self, scope: str) -> bool:
        """Check if a scope is available for use.

        Project and local scopes require a .amplifier directory in the current
        working directory. Global scope is always available.

        Args:
            scope: Scope name ("local", "project", "global", or "session")
        """
        if scope == "global":
            return True
        if scope == "session":
            return self.paths.session_settings is not None

        # For project and local scopes, check if .amplifier directory exists
        # or if we're not in the home directory
        cwd = Path.cwd()
        home = Path.home()

        # If we're in or under home directory without .amplifier, project/local not available
        amplifier_dir = cwd / ".amplifier"
        if amplifier_dir.exists():
            return True

        # Check if we're directly in home directory (no project context)
        if cwd == home:
            return False

        return True

    def get_scope_path(self, scope: Scope) -> Path:
        """Get settings file path for scope. Public wrapper for _get_scope_path."""
        return self._get_scope_path(scope)

    def scope_path(self, scope: Scope) -> Path:
        """Backward compatibility alias for get_scope_path()."""
        return self.get_scope_path(scope)

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
                        if (
                            isinstance(tool, dict)
                            and tool.get("module") == "tool-filesystem"
                        ):
                            paths_list = tool.get("config", {}).get(
                                "allowed_write_paths", []
                            )
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
            fs_tool = {
                "module": "tool-filesystem",
                "config": {"allowed_write_paths": []},
            }
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

    # ----- Tool override settings (modules.tools) -----

    def get_tool_overrides(
        self, session_id: str | None = None, project_slug: str | None = None
    ) -> list[dict[str, Any]]:
        """Return merged tool overrides from modules.tools.

        Tool overrides allow settings like allowed_write_paths for tool-filesystem.

        Args:
            session_id: Optional session ID to include session-scoped settings
            project_slug: Optional project slug (required if session_id provided)
        """
        settings = self.get_merged_settings()
        tools = settings.get("modules", {}).get("tools", [])

        # Also check session-scoped settings if session context provided
        if session_id and project_slug:
            session_settings_path = (
                Path.home()
                / ".amplifier"
                / "projects"
                / project_slug
                / "sessions"
                / session_id
                / "settings.yaml"
            )
            if session_settings_path.exists():
                try:
                    with open(session_settings_path, encoding="utf-8") as f:
                        session_settings = yaml.safe_load(f) or {}
                    session_tools = session_settings.get("modules", {}).get("tools", [])
                    if session_tools:
                        tools = self._merge_tool_lists(tools, session_tools)
                except Exception:
                    pass

        return tools if isinstance(tools, list) else []

    def _merge_tool_lists(
        self, base: list[dict[str, Any]], overlay: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge tool lists, with overlay taking precedence for matching modules."""
        result = list(base)
        base_modules = {
            t.get("module"): i for i, t in enumerate(base) if isinstance(t, dict)
        }

        for tool in overlay:
            if not isinstance(tool, dict):
                continue
            module_id = tool.get("module")
            if module_id and module_id in base_modules:
                idx = base_modules[module_id]
                base_config = result[idx].get("config", {}) or {}
                overlay_config = tool.get("config", {}) or {}
                merged_config = self._merge_tool_configs(base_config, overlay_config)
                result[idx] = {**result[idx], **tool, "config": merged_config}
            else:
                result.append(tool)

        return result

    def _merge_tool_configs(
        self, base: dict[str, Any], overlay: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge tool configs with special handling for permission lists."""
        result = {**base, **overlay}

        # Union permission fields instead of replacing
        permission_fields = [
            "allowed_write_paths",
            "allowed_read_paths",
            "denied_write_paths",
        ]
        for field in permission_fields:
            if field in base or field in overlay:
                base_paths = set(base.get(field, []))
                overlay_paths = set(overlay.get(field, []))
                result[field] = list(base_paths | overlay_paths)

        return result

    # ----- Module override settings (overrides section) -----

    def get_module_overrides(self) -> dict[str, dict[str, Any]]:
        """Return unified module overrides from overrides section.

        Expected structure:
            overrides:
              tool-task:
                source: /local/path/to/module
                config:
                  inherit_context: recent
        """
        settings = self.get_merged_settings()
        overrides = settings.get("overrides", {})
        return overrides if isinstance(overrides, dict) else {}

    def get_source_overrides(self) -> dict[str, str]:
        """Return source overrides only (module_id -> source_uri).

        Convenience method for Bundle.prepare(source_resolver=...).
        """
        overrides = self.get_module_overrides()
        return {
            module_id: override["source"]
            for module_id, override in overrides.items()
            if isinstance(override, dict) and "source" in override
        }

    def get_config_overrides(self) -> dict[str, dict[str, Any]]:
        """Return config overrides only (module_id -> config_dict)."""
        overrides = self.get_module_overrides()
        return {
            module_id: override.get("config", {})
            for module_id, override in overrides.items()
            if isinstance(override, dict) and "config" in override
        }

    def set_module_override(
        self,
        module_id: str,
        source: str | None = None,
        config: dict[str, Any] | None = None,
        scope: Scope = "project",
    ) -> None:
        """Set a module override at the specified scope."""
        settings = self._read_scope(scope)
        if "overrides" not in settings:
            settings["overrides"] = {}

        override: dict[str, Any] = {}
        if source is not None:
            override["source"] = source
        if config is not None:
            override["config"] = config

        if override:
            settings["overrides"][module_id] = override
        elif module_id in settings.get("overrides", {}):
            del settings["overrides"][module_id]

        self._write_scope(scope, settings)

    def remove_module_override(self, module_id: str, scope: Scope = "project") -> bool:
        """Remove a module override from specified scope. Returns True if removed."""
        settings = self._read_scope(scope)
        overrides = settings.get("overrides", {})

        if module_id not in overrides:
            return False

        del overrides[module_id]
        settings["overrides"] = overrides
        self._write_scope(scope, settings)
        return True

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
                raise ValueError(
                    "Session scope requires session_id to be set. Use with_session() first."
                )
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

    def _deep_merge(
        self, base: dict[str, Any], overlay: dict[str, Any]
    ) -> dict[str, Any]:
        """Deep merge two dicts, overlay wins."""
        result = base.copy()
        for key, value in overlay.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


# Convenience function for quick access
def get_settings() -> AppSettings:
    """Get a settings instance with default paths."""
    return AppSettings()
