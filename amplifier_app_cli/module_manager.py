"""Module configuration management."""

import logging
from dataclasses import dataclass
from typing import Any
from typing import Literal

from .settings import SettingsManager

logger = logging.getLogger(__name__)

ScopeType = Literal["local", "project", "global"]
ModuleType = Literal["tool", "hook", "agent"]


@dataclass
class ModuleInfo:
    """Information about a loaded module."""

    module_id: str
    module_type: str
    source: str


@dataclass
class AddModuleResult:
    """Result of adding a module."""

    module_id: str
    module_type: str
    scope: str
    file: str


@dataclass
class RemoveModuleResult:
    """Result of removing a module."""

    module_id: str
    scope: str


class ModuleManager:
    """Manage module configuration."""

    def __init__(self, settings: SettingsManager | None = None):
        """Initialize module manager.

        Args:
            settings: Settings manager instance (creates new if None)
        """
        self.settings = settings or SettingsManager()

    def add_module(
        self,
        module_id: str,
        module_type: ModuleType,
        scope: ScopeType,
        config: dict | None = None,
    ) -> AddModuleResult:
        """Add module to configuration at scope.

        Args:
            module_id: Module identifier
            module_type: Type of module (tool/hook/agent)
            scope: Where to save (local/project/global)
            config: Optional module configuration

        Returns:
            AddModuleResult with details
        """
        module_entry: dict[str, Any] = {"module": module_id}
        if config:
            module_entry["config"] = config

        # Map module type to settings key (tools/hooks/agents)
        type_to_key = {"tool": "tools", "hook": "hooks", "agent": "agents"}
        module_list_key = type_to_key[module_type]

        # Get current modules list
        scope_map = {"local": "local", "project": "project", "global": "user"}
        settings_scope = scope_map[scope]
        target_file = self._get_file_for_scope(settings_scope)

        settings = self.settings._read_settings(target_file) or {}
        if "modules" not in settings:
            settings["modules"] = {}
        if module_list_key not in settings["modules"]:
            settings["modules"][module_list_key] = []

        # Add module (avoid duplicates)
        existing_ids = {m.get("module") for m in settings["modules"][module_list_key] if isinstance(m, dict)}
        if module_id not in existing_ids:
            settings["modules"][module_list_key].append(module_entry)
            self.settings._write_settings(target_file, settings)
            logger.info(f"Added {module_type} '{module_id}' at {scope} scope")
        else:
            logger.warning(f"Module '{module_id}' already exists at {scope} scope")

        return AddModuleResult(module_id=module_id, module_type=module_type, scope=scope, file=str(target_file))

    def remove_module(
        self,
        module_id: str,
        scope: ScopeType,
    ) -> RemoveModuleResult:
        """Remove module from configuration at scope.

        Args:
            module_id: Module identifier
            scope: Which scope to remove from

        Returns:
            RemoveModuleResult with details
        """
        scope_map = {"local": "local", "project": "project", "global": "user"}
        settings_scope = scope_map[scope]
        target_file = self._get_file_for_scope(settings_scope)

        settings = self.settings._read_settings(target_file)
        if not settings or "modules" not in settings:
            logger.warning(f"No modules configured at {scope} scope")
            return RemoveModuleResult(module_id=module_id, scope=scope)

        # Remove from all module types (tools/hooks/agents)
        removed = False
        for module_type in ["tools", "hooks", "agents"]:
            if module_type in settings["modules"]:
                original_len = len(settings["modules"][module_type])
                settings["modules"][module_type] = [
                    m for m in settings["modules"][module_type] if m.get("module") != module_id
                ]
                if len(settings["modules"][module_type]) < original_len:
                    removed = True

                # Clean up empty list
                if not settings["modules"][module_type]:
                    del settings["modules"][module_type]

        # Clean up empty modules section
        if not settings["modules"]:
            del settings["modules"]

        if removed:
            self.settings._write_settings(target_file, settings)
            logger.info(f"Removed module '{module_id}' from {scope} scope")
        else:
            logger.warning(f"Module '{module_id}' not found at {scope} scope")

        return RemoveModuleResult(module_id=module_id, scope=scope)

    def get_current_modules(self) -> list[ModuleInfo]:
        """Get currently configured modules from merged settings.

        Returns:
            List of ModuleInfo objects
        """
        merged = self.settings.get_merged_settings()
        modules = []

        if "modules" in merged:
            module_config = merged["modules"]

            # Collect tools
            if "tools" in module_config:
                for tool in module_config["tools"]:
                    if isinstance(tool, dict) and "module" in tool:
                        modules.append(ModuleInfo(module_id=tool["module"], module_type="tool", source="settings"))

            # Collect hooks
            if "hooks" in module_config:
                for hook in module_config["hooks"]:
                    if isinstance(hook, dict) and "module" in hook:
                        modules.append(ModuleInfo(module_id=hook["module"], module_type="hook", source="settings"))

            # Collect agents
            if "agents" in module_config:
                for agent in module_config["agents"]:
                    if isinstance(agent, dict) and "module" in agent:
                        modules.append(ModuleInfo(module_id=agent["module"], module_type="agent", source="settings"))

        return modules

    def _get_file_for_scope(self, scope: str):
        """Get settings file path for scope."""
        if scope == "user":
            return self.settings.user_settings_file
        if scope == "project":
            return self.settings.project_settings_file
        # local
        return self.settings.local_settings_file
