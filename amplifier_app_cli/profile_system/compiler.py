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

    # Extract from ModuleConfig objects directly (no branching)
    orchestrator = base.session.orchestrator
    orchestrator_id = orchestrator.module
    orchestrator_source = orchestrator.source
    orchestrator_config = orchestrator.config or {}

    context = base.session.context
    context_id = context.module
    context_source = context.source
    context_config = context.config or {}

    # Start with base profile
    mount_plan: dict[str, Any] = {
        "session": {
            "orchestrator": orchestrator_id,
            "context": context_id,
        },
        "providers": [],
        "tools": [],
        "hooks": [],
        "agents": [],
    }

    # Add sources if present
    if orchestrator_source:
        mount_plan["session"]["orchestrator_source"] = orchestrator_source
    if context_source:
        mount_plan["session"]["context_source"] = context_source

    # Add config sections if present
    if orchestrator_config:
        mount_plan["orchestrator"] = {"config": orchestrator_config}
    if context_config:
        mount_plan["context"] = {"config": context_config}

    # Add base modules
    mount_plan["providers"] = [p.to_dict() for p in base.providers]
    mount_plan["tools"] = [t.to_dict() for t in base.tools]
    mount_plan["hooks"] = [h.to_dict() for h in base.hooks]
    mount_plan["agents"] = {}  # Will be populated from agents config

    # Apply overlays
    for overlay in overlays:
        mount_plan = _merge_profile_into_mount_plan(mount_plan, overlay)

    # Load agents using new agent loading system
    if base.agents:
        from .agent_loader import AgentLoader
        from .agent_schema import Agent

        agent_loader = AgentLoader()
        agents_dict = {}

        # Load inline agents first (highest priority for these names)
        if base.agents.inline:
            for name, config_dict in base.agents.inline.items():
                try:
                    # Validate inline agent as Agent model
                    agent = Agent(**config_dict)
                    agents_dict[name] = agent.to_mount_plan_fragment()
                    logger.debug(f"Loaded inline agent: {name}")
                except Exception as e:
                    logger.warning(f"Failed to load inline agent '{name}': {e}")

        # Determine which agents to load
        if base.agents.include:
            # Explicit include list - load only these agents
            agent_names_to_load = base.agents.include
        elif base.agents.dirs:
            # Dirs specified without include - load all available agents
            agent_names_to_load = agent_loader.list_agents()
        else:
            # No dirs, no include - just inline agents
            agent_names_to_load = []

        # Load agents from standard search locations
        for agent_name in agent_names_to_load:
            if agent_name in agents_dict:
                # Inline already defined this agent, skip
                continue

            try:
                # AgentLoader uses AgentResolver to find agent file
                agent = agent_loader.load_agent(agent_name)
                agents_dict[agent_name] = agent.to_mount_plan_fragment()
                logger.debug(f"Loaded agent '{agent_name}' from search path")
            except Exception as e:
                logger.warning(f"Failed to load agent '{agent_name}': {e}")

        mount_plan["agents"] = agents_dict

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
        # Extract from ModuleConfig directly
        mount_plan["session"]["orchestrator"] = overlay.session.orchestrator.module
        if overlay.session.orchestrator.source:
            mount_plan["session"]["orchestrator_source"] = overlay.session.orchestrator.source
        else:
            mount_plan["session"].pop("orchestrator_source", None)
        if overlay.session.orchestrator.config:
            if "orchestrator" not in mount_plan:
                mount_plan["orchestrator"] = {}
            mount_plan["orchestrator"]["config"] = overlay.session.orchestrator.config

    if overlay.session.context:
        # Extract from ModuleConfig directly
        mount_plan["session"]["context"] = overlay.session.context.module
        if overlay.session.context.source:
            mount_plan["session"]["context_source"] = overlay.session.context.source
        else:
            mount_plan["session"].pop("context_source", None)
        if overlay.session.context.config:
            if "context" not in mount_plan:
                mount_plan["context"] = {}
            mount_plan["context"]["config"] = overlay.session.context.config

    # Merge module lists
    mount_plan["providers"] = _merge_module_list(mount_plan["providers"], overlay.providers)
    mount_plan["tools"] = _merge_module_list(mount_plan["tools"], overlay.tools)
    mount_plan["hooks"] = _merge_module_list(mount_plan["hooks"], overlay.hooks)

    # Agents are handled by loading from directories + inline, not merged here

    return mount_plan


def _inject_profile_configs(mount_plan: dict[str, Any], profile: Profile) -> dict[str, Any]:
    """
    Inject profile-level config sections into specific modules.

    This passes profile.task to task-tool module,
    profile.ui to streaming-ui hook module, etc.

    Args:
        mount_plan: Mount plan to update
        profile: Profile containing config sections

    Returns:
        Updated mount plan
    """
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
                # Pass all UIConfig fields automatically
                hook["config"]["ui"] = profile.ui.model_dump()

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
