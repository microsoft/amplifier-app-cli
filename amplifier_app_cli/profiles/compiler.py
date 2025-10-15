"""Profile compiler that converts profiles to Mount Plans."""

import logging
from typing import Any

from .schema import Profile

logger = logging.getLogger(__name__)


def compile_profile_to_mount_plan(base: Profile, overlays: list[Profile] | None = None) -> dict[str, Any]:
    """
    Compile a profile and its overlays into a Mount Plan.

    This function takes a base profile and optional overlay profiles and merges them
    into a single Mount Plan dictionary that can be passed to AmplifierSession.

    Merge strategy:
    1. Start with base profile
    2. Apply each overlay in order (increasing precedence)
    3. Module lists are merged by module ID (later definitions override earlier ones)
    4. Session config fields are overridden (not merged)

    Args:
        base: Base profile to compile
        overlays: Optional list of overlay profiles to merge (in precedence order)

    Returns:
        Mount Plan dictionary suitable for AmplifierSession

    Example:
        >>> base = Profile(...)
        >>> overlays = [team_overlay, user_overlay]
        >>> mount_plan = compile_profile_to_mount_plan(base, overlays)
        >>> session = AmplifierSession(mount_plan, loader)
    """
    if overlays is None:
        overlays = []

    # Start with base profile
    mount_plan: dict[str, Any] = {
        "session": {
            "orchestrator": base.session.orchestrator,
            "context": base.session.context,
        },
        "providers": [],
        "tools": [],
        "hooks": [],
        "agents": [],
    }

    # Add context config if present
    if base.has_context_config():
        mount_plan["context"] = {"config": base.get_context_config()}

    # Add orchestrator config if present
    if base.orchestrator and base.orchestrator.config:
        mount_plan["orchestrator"] = {"config": base.orchestrator.config}

    # Add base modules
    mount_plan["providers"] = [p.to_dict() for p in base.providers]
    mount_plan["tools"] = [t.to_dict() for t in base.tools]
    mount_plan["hooks"] = [h.to_dict() for h in base.hooks]
    mount_plan["agents"] = base.agents  # Dict of config overlays (app-layer data), not modules

    # Apply overlays
    for overlay in overlays:
        mount_plan = _merge_profile_into_mount_plan(mount_plan, overlay)

    # Load agents from directories if agents_config present
    if base.agents_config and base.agents_config.dirs:
        from pathlib import Path

        from amplifier_app_cli.agent_config import load_agent_configs_from_directory

        # Resolve agent dirs relative to bundled profiles directory
        bundled_base = Path(__file__).parent.parent.parent  # Points to amplifier-app-cli/

        for agent_dir in base.agents_config.dirs:
            # Resolve relative paths relative to bundled package location
            if agent_dir.startswith("./") or agent_dir.startswith("../"):
                resolved_dir = (bundled_base / agent_dir).resolve()
            else:
                resolved_dir = Path(agent_dir)

            # Load agents from this directory
            loaded_agents = load_agent_configs_from_directory(resolved_dir)

            # Merge loaded agents into mount plan agents dict
            if loaded_agents:
                mount_plan["agents"].update(loaded_agents)
                logger.debug(f"Loaded {len(loaded_agents)} agents from {resolved_dir}")

    # Inject profile-level config sections into specific modules
    mount_plan = _inject_profile_configs(mount_plan, base)

    logger.debug(f"Compiled profile '{base.profile.name}' with {len(overlays)} overlays")

    return mount_plan


def _merge_profile_into_mount_plan(mount_plan: dict[str, Any], overlay: Profile) -> dict[str, Any]:
    """
    Merge an overlay profile into an existing mount plan.

    Overlay rules:
    - Session fields: override if present
    - Context config: merge (overlay fields override)
    - Module lists: merge by module ID (overlay modules override base modules)

    Args:
        mount_plan: Existing mount plan to merge into
        overlay: Overlay profile to merge

    Returns:
        Updated mount plan
    """
    # Override session fields if present in overlay
    if overlay.session.orchestrator:
        mount_plan["session"]["orchestrator"] = overlay.session.orchestrator
    if overlay.session.context:
        mount_plan["session"]["context"] = overlay.session.context

    # Merge context config if present in overlay
    if overlay.has_context_config():
        if "context" not in mount_plan:
            mount_plan["context"] = {"config": {}}
        if "config" not in mount_plan["context"]:
            mount_plan["context"]["config"] = {}

        overlay_context_config = overlay.get_context_config()
        mount_plan["context"]["config"].update(overlay_context_config)

    # Merge orchestrator config if present in overlay
    if overlay.orchestrator and overlay.orchestrator.config:
        if "orchestrator" not in mount_plan:
            mount_plan["orchestrator"] = {"config": {}}
        if "config" not in mount_plan["orchestrator"]:
            mount_plan["orchestrator"]["config"] = {}

        mount_plan["orchestrator"]["config"].update(overlay.orchestrator.config)

    # Merge module lists
    mount_plan["providers"] = _merge_module_list(mount_plan["providers"], overlay.providers)
    mount_plan["tools"] = _merge_module_list(mount_plan["tools"], overlay.tools)
    mount_plan["hooks"] = _merge_module_list(mount_plan["hooks"], overlay.hooks)

    # Merge agents dict (config overlays, not modules)
    if overlay.agents:
        if "agents" not in mount_plan:
            mount_plan["agents"] = {}
        mount_plan["agents"].update(overlay.agents)  # Dict merge, overlay wins

    return mount_plan


def _inject_profile_configs(mount_plan: dict[str, Any], profile: Profile) -> dict[str, Any]:
    """
    Inject profile-level config sections into specific modules.

    This passes profile.agents_config to agent-registry module,
    profile.task to task-tool module, etc.

    Args:
        mount_plan: Mount plan to update
        profile: Profile containing config sections

    Returns:
        Updated mount plan
    """
    # Inject agents_config into agent-registry module
    if profile.agents_config:
        for hook in mount_plan.get("hooks", []):
            if hook.get("module") == "agent-registry":
                if "config" not in hook:
                    hook["config"] = {}
                hook["config"]["agents"] = {"dirs": profile.agents_config.dirs}

    # Inject task config into task-tool module
    if profile.task:
        for tool in mount_plan.get("tools", []):
            if tool.get("module") == "tool-task":
                if "config" not in tool:
                    tool["config"] = {}
                tool["config"]["max_recursion_depth"] = profile.task.max_recursion_depth

    # Inject UI config into streaming-ui hook module
    if profile.ui:
        for hook in mount_plan.get("hooks", []):
            if hook.get("module") == "hooks-streaming-ui":
                if "config" not in hook:
                    hook["config"] = {}
                hook["config"]["ui"] = {
                    "show_thinking_stream": profile.ui.show_thinking_stream,
                    "show_tool_lines": profile.ui.show_tool_lines,
                }

    return mount_plan


def _merge_module_list(base_modules: list[dict[str, Any]], overlay_modules: list) -> list[dict[str, Any]]:
    """
    Merge two module lists, with overlay modules overriding base modules.

    Modules are matched by their 'module' ID. If a module appears in both lists,
    the overlay version replaces the base version entirely (including config).

    If a module appears only in the overlay, it's appended to the result.

    Args:
        base_modules: Existing module list (already in dict format)
        overlay_modules: Overlay module list (ModuleConfig objects)

    Returns:
        Merged module list
    """
    # Convert overlay modules to dict format
    overlay_dicts = [m.to_dict() for m in overlay_modules]

    # Create a dict of base modules by ID for easy lookup
    base_by_id = {m["module"]: m for m in base_modules}

    # Create a dict of overlay modules by ID
    overlay_by_id = {m["module"]: m for m in overlay_dicts}

    # Merge: start with base modules, override with overlay modules
    merged = base_by_id.copy()
    merged.update(overlay_by_id)

    # Return as list, preserving order (base first, then new overlay modules)
    result = []

    # Add base modules (potentially overridden)
    for base_module in base_modules:
        module_id = base_module["module"]
        result.append(merged[module_id])

    # Add new overlay modules (not in base)
    for overlay_module in overlay_dicts:
        module_id = overlay_module["module"]
        if module_id not in base_by_id:
            result.append(overlay_module)

    return result
