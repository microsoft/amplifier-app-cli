"""Hook, tool, and CLI-specific runtime configuration policies."""

from __future__ import annotations

from typing import Any

from ..lib.merge_utils import merge_module_items, merge_tool_configs
from .config_merge import deep_merge


def _apply_hook_overrides(
    hooks: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge hooks by module and append overrides absent from the bundle."""
    if not overrides:
        return hooks

    override_map = {
        override["module"]: override
        for override in overrides
        if isinstance(override, dict) and "module" in override
    }
    result = []
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("module") in override_map:
            override = override_map[hook["module"]]
            merged = merge_module_items(hook, override)
            base_config = hook.get("config", {}) or {}
            override_config = override.get("config", {}) or {}
            if base_config or override_config:
                merged["config"] = deep_merge(base_config, override_config)
            result.append(merged)
        else:
            result.append(hook)

    existing_modules = {h.get("module") for h in hooks if isinstance(h, dict)}
    for override in overrides:
        if (
            isinstance(override, dict)
            and override.get("module") not in existing_modules
        ):
            result.append(override)
    return result


def _ensure_cli_hook_policies(
    hooks: list[dict[str, Any]], config_overrides: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Apply CLI-level hook display policies."""
    return _ensure_streaming_ui_thinking_default(hooks, config_overrides or {})


def _ensure_streaming_ui_thinking_default(
    hooks: list[dict[str, Any]], config_overrides: dict[str, Any]
) -> list[dict[str, Any]]:
    """Hide thinking transcripts unless the user explicitly opts in."""
    explicit_ui = config_overrides.get("hooks-streaming-ui", {}).get("ui", {})
    if isinstance(explicit_ui, dict) and "show_thinking_stream" in explicit_ui:
        return hooks

    result = []
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("module") == "hooks-streaming-ui":
            hook = hook.copy()
            config = (hook.get("config") or {}).copy()
            ui_config = (config.get("ui") or {}).copy()
            ui_config["show_thinking_stream"] = False
            config["ui"] = ui_config
            hook["config"] = config
        result.append(hook)
    return result


def _apply_tool_overrides(
    tools: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge tool overrides and apply CLI permission/default policies."""
    if not overrides:
        return _ensure_cli_tool_policies(tools)

    override_map = {
        override["module"]: override
        for override in overrides
        if isinstance(override, dict) and "module" in override
    }
    result = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("module") in override_map:
            override = override_map[tool["module"]]
            merged = merge_module_items(tool, override)
            base_config = tool.get("config", {}) or {}
            override_config = override.get("config", {}) or {}
            if base_config or override_config:
                merged["config"] = merge_tool_configs(base_config, override_config)
            result.append(merged)
        else:
            result.append(tool)

    existing_modules = {t.get("module") for t in tools if isinstance(t, dict)}
    for override in overrides:
        if (
            isinstance(override, dict)
            and override.get("module") not in existing_modules
        ):
            result.append(override)
    return _ensure_cli_tool_policies(result)


def _ensure_cli_tool_policies(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply every CLI-owned tool policy in a stable order."""
    return _ensure_default_skills_dirs(_ensure_cwd_in_write_paths(tools))


def _ensure_cwd_in_write_paths(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure the project directory remains writable by tool-filesystem."""
    result = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("module") == "tool-filesystem":
            tool = tool.copy()
            config = (tool.get("config") or {}).copy()
            paths = list(config.get("allowed_write_paths", []))
            if "." not in paths:
                paths.insert(0, ".")
            config["allowed_write_paths"] = paths
            tool["config"] = config
        result.append(tool)
    return result


def _ensure_default_skills_dirs(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure workspace and user skill directories remain discoverable."""
    default_paths = [".amplifier/skills", "~/.amplifier/skills"]
    result = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("module") == "tool-skills":
            tool = tool.copy()
            config = (tool.get("config") or {}).copy()
            skills = list(config.get("skills", []))
            for path in default_paths:
                if path not in skills:
                    skills.append(path)
            config["skills"] = skills
            tool["config"] = config
        result.append(tool)
    return result
