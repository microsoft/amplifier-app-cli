"""Hook configuration loading.

Loads hook configurations from settings.yaml files with support for
the 3-scope configuration system (user, project, local).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import HookConfig, HookMatcher, HookType

logger = logging.getLogger(__name__)


@dataclass
class HooksConfig:
    """Hooks configuration from settings.

    Attributes:
        hooks: List of hook configurations
        disabled_hooks: Names of hooks to disable
        global_timeout: Default timeout for all hooks
    """

    hooks: list[HookConfig] = field(default_factory=list)
    disabled_hooks: list[str] = field(default_factory=list)
    global_timeout: float = 30.0

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> HooksConfig:
        """Load hooks config from settings dictionary.

        Settings format:
        ```yaml
        hooks:
          timeout: 30  # Global timeout
          disabled:
            - hook-name-to-disable
          definitions:
            - name: my-pre-hook
              type: command
              command: ./scripts/validate.sh
              matcher:
                events: [PreToolUse]
                tools: [write, edit]
              timeout: 10
              priority: 50
              
            - name: notify-on-bash
              type: command
              script: scripts/notify.py
              matcher:
                events: [PostToolUse]
                tools: [bash]
                
            - name: llm-reviewer
              type: llm
              prompt: "Review this tool call for safety: {{tool}} with args {{args}}"
              matcher:
                events: [PreToolUse]
        ```

        Args:
            settings: Settings dictionary

        Returns:
            HooksConfig instance
        """
        hooks_settings = settings.get("hooks", {})

        # Parse global timeout
        global_timeout = hooks_settings.get("timeout", 30.0)

        # Parse disabled hooks
        disabled_hooks = hooks_settings.get("disabled", [])

        # Parse hook definitions
        hooks: list[HookConfig] = []
        for hook_dict in hooks_settings.get("definitions", []):
            try:
                hook = cls._parse_hook_config(hook_dict, global_timeout)
                if hook.name not in disabled_hooks:
                    hooks.append(hook)
                else:
                    logger.debug(f"Hook '{hook.name}' is disabled")
            except (KeyError, ValueError) as e:
                logger.warning(f"Invalid hook configuration: {e}")

        return cls(
            hooks=hooks,
            disabled_hooks=disabled_hooks,
            global_timeout=global_timeout,
        )

    @staticmethod
    def _parse_hook_config(data: dict[str, Any], default_timeout: float) -> HookConfig:
        """Parse a single hook configuration.

        Args:
            data: Hook configuration dictionary
            default_timeout: Default timeout if not specified

        Returns:
            HookConfig instance

        Raises:
            KeyError: If required field is missing
            ValueError: If configuration is invalid
        """
        name = data.get("name")
        if not name:
            raise KeyError("Hook requires 'name' field")

        # Determine hook type
        hook_type = HookType(data.get("type", "internal"))

        # Parse matcher
        matcher_data = data.get("matcher", {})
        matcher = HookMatcher.from_dict(matcher_data)

        # Build config
        return HookConfig(
            name=name,
            type=hook_type,
            matcher=matcher,
            command=data.get("command"),
            script=data.get("script"),
            prompt=data.get("prompt"),
            timeout=data.get("timeout", default_timeout),
            priority=data.get("priority", 100),
            enabled=data.get("enabled", True),
            description=data.get("description"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for saving."""
        return {
            "timeout": self.global_timeout,
            "disabled": self.disabled_hooks,
            "definitions": [hook.to_dict() for hook in self.hooks],
        }

    def get_hooks_for_event(self, event: str) -> list[HookConfig]:
        """Get hooks that should fire for an event.

        Args:
            event: Event name

        Returns:
            List of matching hook configs, sorted by priority
        """
        matching = []
        for hook in self.hooks:
            if not hook.enabled:
                continue
            if hook.matcher.events and event not in hook.matcher.events:
                continue
            matching.append(hook)

        # Sort by priority (lower = earlier)
        return sorted(matching, key=lambda h: h.priority)


def load_hooks_config(config_manager) -> HooksConfig:
    """Load hooks configuration from the config manager.

    Merges hook settings from all scopes (user → project → local).

    Args:
        config_manager: ConfigManager instance

    Returns:
        Merged HooksConfig
    """
    merged_settings = config_manager.get_merged_settings()
    return HooksConfig.from_settings(merged_settings)


def get_default_hooks() -> list[HookConfig]:
    """Get default hook configurations.

    These hooks are always available but can be disabled.
    """
    return [
        # Logging hook for tool usage
        HookConfig(
            name="tool-logger",
            type=HookType.INTERNAL,
            matcher=HookMatcher(events=["PostToolUse"]),
            description="Log tool usage for debugging",
            priority=1000,  # Low priority = late execution
        ),
    ]


def discover_hook_scripts(search_paths: list[Path]) -> list[HookConfig]:
    """Discover hook scripts in search paths.

    Looks for executable scripts in hooks/ directories:
    - pre-<tool>.sh/py: PreToolUse hooks for specific tool
    - post-<tool>.sh/py: PostToolUse hooks for specific tool
    - on-<event>.sh/py: Hooks for specific events

    Args:
        search_paths: Paths to search for hooks

    Returns:
        List of discovered HookConfig
    """
    discovered = []

    for base_path in search_paths:
        hooks_dir = base_path / "hooks"
        if not hooks_dir.exists():
            continue

        for script in hooks_dir.iterdir():
            if not script.is_file():
                continue

            # Skip non-executable scripts (except on Windows)
            import sys
            if sys.platform != "win32" and not script.stat().st_mode & 0o111:
                continue

            # Parse script name
            name = script.stem
            suffix = script.suffix.lower()

            if suffix not in (".sh", ".py", ".ps1"):
                continue

            # Determine event and tool from name
            event = None
            tools = []

            if name.startswith("pre-"):
                event = "PreToolUse"
                tool = name[4:]
                if tool:
                    tools = [tool]
            elif name.startswith("post-"):
                event = "PostToolUse"
                tool = name[5:]
                if tool:
                    tools = [tool]
            elif name.startswith("on-"):
                event = name[3:].replace("-", ":")  # on-session-start -> session:start
            else:
                continue  # Unknown naming convention

            # Create config
            discovered.append(HookConfig(
                name=f"script:{script.name}",
                type=HookType.COMMAND,
                matcher=HookMatcher(
                    events=[event] if event else [],
                    tools=tools,
                ),
                script=str(script),
                description=f"Auto-discovered from {script}",
            ))

    return discovered
