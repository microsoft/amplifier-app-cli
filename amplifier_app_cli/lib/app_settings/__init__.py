"""Shared application-level settings helpers.

These helpers live in the CLI repo for now so we can finalize their shape
before extracting them into a dedicated library for other front ends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Literal

from amplifier_app_cli.lib.config_compat import ConfigManager
from amplifier_app_cli.lib.config_compat import Scope

# LEGACY: Profile and ModuleConfig types are no longer available
# Profile mode is deprecated - use bundles instead
# The apply_provider_overrides_to_profile method will raise NotImplementedError

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

    def set_provider_override(
        self, provider_entry: dict[str, Any], scope: ScopeType
    ) -> None:
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

    def get_notification_config(self) -> dict[str, Any]:
        """Return merged notification policy config (local > project > global).

        Notifications are an app-level policy that gets composed onto bundles
        at runtime, similar to how providers work. Unlike bundle behaviors,
        notification hooks only fire for root sessions (not sub-agents).

        Expected structure in settings.yaml:
            config:
              notifications:
                desktop:
                  enabled: true
                  title: "Amplifier"
                  subtitle: "cwd"
                  suppress_if_focused: true
                push:
                  enabled: true
                  service: ntfy
                  topic: "my-topic"
                min_iterations: 1
                show_iteration_count: true

        Returns:
            Dict with notification config, or empty dict if not configured.
        """
        merged = self._config.get_merged_settings()
        notifications = merged.get("config", {}).get("notifications", {})
        return notifications if isinstance(notifications, dict) else {}

    def get_notification_hook_overrides(self) -> list[dict[str, Any]]:
        """Return hook overrides derived from notification settings.

        Maps config.notifications.* settings to hook module configs.

        SECURITY NOTE: ntfy topic is NOT passed from settings.yaml.
        The topic MUST be set via AMPLIFIER_NTFY_TOPIC env var (in keys.env).
        This ensures the topic (which is essentially a password for ntfy.sh)
        is never stored in plain config files.

        Expected structure in settings.yaml:
            config:
              notifications:
                desktop:
                  enabled: true
                  show_device: true
                  show_project: true
                  show_preview: true
                  preview_length: 100
                ntfy:
                  enabled: true
                  server: https://ntfy.sh  # topic comes from keys.env

        Returns:
            List of hook override dicts ready for _apply_hook_overrides().
        """
        notifications = self.get_notification_config()

        overrides: list[dict[str, Any]] = []

        # Map desktop notification settings to hooks-notify module
        # Desktop notifications are ENABLED by default (set enabled: false to disable)
        desktop_config = notifications.get("desktop", {})
        if desktop_config.get("enabled", True):
            # Build config dict from desktop settings
            hook_config: dict[str, Any] = {"enabled": True}
            # Map known desktop settings
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

        # Map ntfy/push notification settings to hooks-notify-push module
        # Support both "ntfy:" and "push:" config keys
        ntfy_config = notifications.get("ntfy", {})
        push_config = notifications.get("push", {})
        # Merge with ntfy taking precedence (more specific)
        combined_push = {**push_config, **ntfy_config}

        if combined_push and combined_push.get("enabled", False):
            hook_config = {"enabled": True, "service": "ntfy"}
            # Map known push/ntfy settings
            # SECURITY: "topic" is intentionally NOT included here
            # Topic must come from AMPLIFIER_NTFY_TOPIC env var (keys.env)
            for key in ["server", "priority", "tags", "debug"]:
                if key in combined_push:
                    hook_config[key] = combined_push[key]

            overrides.append({"module": "hooks-notify-push", "config": hook_config})

        return overrides

    def set_notification_config(
        self,
        notification_type: str,
        config: dict[str, Any],
        scope: ScopeType,
    ) -> None:
        """Set notification config at specified scope.

        Args:
            notification_type: "desktop" or "ntfy"
            config: Config dict (enabled, topic, show_preview, etc.)
            scope: Where to save ("local", "project", "global")

        Example:
            set_notification_config("desktop", {"enabled": True}, "global")
            set_notification_config("ntfy", {"enabled": True, "topic": "my-topic"}, "global")
        """
        scope_path = self.scope_path(scope)
        if not scope_path:
            raise ValueError(f"Scope '{scope}' is not available")

        # Read existing settings
        settings = self._config._read_yaml(scope_path) or {}  # type: ignore[attr-defined]

        # Ensure config.notifications structure exists
        if "config" not in settings:
            settings["config"] = {}
        if "notifications" not in settings["config"]:
            settings["config"]["notifications"] = {}

        # Update the specific notification type
        settings["config"]["notifications"][notification_type] = config

        # Write back
        self._config._write_yaml(scope_path, settings)  # type: ignore[attr-defined]

    def clear_notification_config(
        self,
        notification_type: str | None,
        scope: ScopeType,
    ) -> None:
        """Clear notification config at specified scope.

        Args:
            notification_type: "desktop", "ntfy", or None to clear all
            scope: Where to clear from
        """
        scope_path = self.scope_path(scope)
        if not scope_path:
            raise ValueError(f"Scope '{scope}' is not available")

        settings = self._config._read_yaml(scope_path) or {}  # type: ignore[attr-defined]

        notifications = settings.get("config", {}).get("notifications", {})
        if not notifications:
            return  # Nothing to clear

        if notification_type:
            # Clear specific type
            if notification_type in notifications:
                del notifications[notification_type]
        else:
            # Clear all notifications
            notifications.clear()

        # Clean up empty structures
        if not notifications:
            settings.get("config", {}).pop("notifications", None)
        if not settings.get("config"):
            settings.pop("config", None)

        self._config._write_yaml(scope_path, settings)  # type: ignore[attr-defined]

    def get_tool_overrides(
        self, session_id: str | None = None, project_slug: str | None = None
    ) -> list[dict[str, Any]]:
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
                        # Merge session tools with other scopes (session wins)
                        tools = self._merge_tool_lists(tools, session_tools)
                except Exception:
                    pass  # Skip malformed session settings
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
        self, profile: Any, overrides: list[dict[str, Any]] | None = None
    ) -> Any:
        """Return a copy of `profile` with provider overrides applied.

        DEPRECATED: Profile mode is no longer supported. Use bundles instead.
        This method will raise NotImplementedError.

        For bundle-based workflows, use resolve_bundle_config() in runtime/config.py
        which handles provider overrides through the bundle preparation flow.
        """
        raise NotImplementedError(
            "Profile mode is deprecated. Use bundles instead: 'amplifier bundle use <bundle-name>'\n"
            "For bundle workflows, provider overrides are applied via resolve_bundle_config()."
        )

    # ----- Unified Module Overrides -----

    def get_module_overrides(self) -> dict[str, dict[str, Any]]:
        """Return unified module overrides from settings.yaml.

        This is the single source of truth for all module overrides.
        Merges overrides from all scopes (global < project < local).

        Expected structure in settings.yaml:
            overrides:
              tool-task:
                source: /local/path/to/module
                config:
                  inherit_context: recent
              tool-filesystem:
                config:
                  allowed_write_paths: ["/extra/path"]

        Returns:
            Dict mapping module_id -> {"source": str, "config": dict}
            Both source and config are optional per module.
        """
        merged = self._config.get_merged_settings()
        overrides = merged.get("overrides", {})
        return overrides if isinstance(overrides, dict) else {}

    def get_source_overrides(self) -> dict[str, str]:
        """Return source overrides only (module_id -> source_uri).

        Convenience method for passing to Bundle.prepare(source_resolver=...).
        """
        overrides = self.get_module_overrides()
        return {
            module_id: override["source"]
            for module_id, override in overrides.items()
            if isinstance(override, dict) and "source" in override
        }

    def get_config_overrides(self) -> dict[str, dict[str, Any]]:
        """Return config overrides only (module_id -> config_dict).

        Convenience method for applying config overrides after prepare().
        """
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
        scope: ScopeType = "project",
    ) -> None:
        """Set a module override at the specified scope.

        Args:
            module_id: The module to override (e.g., "tool-task")
            source: Optional source path/URI override
            config: Optional config override dict
            scope: Where to save ("local", "project", "global")
        """
        scope_path = self.scope_path(scope)
        if not scope_path:
            raise ValueError(f"Scope '{scope}' is not available")

        import yaml

        # Read existing settings
        if scope_path.exists():
            with open(scope_path, encoding="utf-8") as f:
                settings = yaml.safe_load(f) or {}
        else:
            settings = {}

        # Ensure overrides section exists
        if "overrides" not in settings:
            settings["overrides"] = {}

        # Build override entry
        override: dict[str, Any] = {}
        if source is not None:
            override["source"] = source
        if config is not None:
            override["config"] = config

        if override:
            settings["overrides"][module_id] = override
        elif module_id in settings["overrides"]:
            # Remove if both source and config are None
            del settings["overrides"][module_id]

        # Write back
        scope_path.parent.mkdir(parents=True, exist_ok=True)
        with open(scope_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(settings, f, default_flow_style=False, sort_keys=False)

    def remove_module_override(
        self, module_id: str, scope: ScopeType = "project"
    ) -> bool:
        """Remove a module override from the specified scope.

        Returns:
            True if override was removed, False if it didn't exist.
        """
        scope_path = self.scope_path(scope)
        if not scope_path or not scope_path.exists():
            return False

        import yaml

        with open(scope_path, encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}

        overrides = settings.get("overrides", {})
        if module_id not in overrides:
            return False

        del overrides[module_id]
        settings["overrides"] = overrides

        with open(scope_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(settings, f, default_flow_style=False, sort_keys=False)

        return True
