"""Configuration preparation and inheritance policy for child sessions."""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping

from amplifier_app_cli.runtime.session_spawn_models import PreparedSpawn
from amplifier_app_cli.runtime.session_spawn_models import SessionLifecycleServices
from amplifier_app_cli.runtime.session_spawn_models import SpawnRequest

logger = logging.getLogger(__name__)


def filter_tools(
    config: dict,
    tool_inheritance: dict[str, list[str]],
    agent_explicit_tools: list[str] | None = None,
) -> dict:
    """Apply an allowlist or blocklist while preserving agent-declared tools."""
    tools = config.get("tools", [])
    if not tools:
        return config

    excluded = tool_inheritance.get("exclude_tools", [])
    inherited = tool_inheritance.get("inherit_tools")
    explicit = set(agent_explicit_tools or [])
    if inherited is not None:
        filtered = [
            tool
            for tool in tools
            if tool.get("module") in inherited or tool.get("module") in explicit
        ]
    elif excluded:
        filtered = [
            tool
            for tool in tools
            if tool.get("module") not in excluded or tool.get("module") in explicit
        ]
    else:
        return config

    updated = dict(config)
    updated["tools"] = filtered
    logger.debug(
        "Filtered tools: %d -> %d (exclude=%s, inherit=%s)",
        len(tools),
        len(filtered),
        excluded,
        inherited,
    )
    return updated


def filter_hooks(
    config: dict,
    hook_inheritance: dict[str, list[str]],
    agent_explicit_hooks: list[str] | None = None,
) -> dict:
    """Apply an allowlist or blocklist while preserving agent-declared hooks."""
    hooks = config.get("hooks", [])
    if not hooks:
        return config

    excluded = hook_inheritance.get("exclude_hooks", [])
    inherited = hook_inheritance.get("inherit_hooks")
    explicit = set(agent_explicit_hooks or [])
    if inherited is not None:
        filtered = [
            hook
            for hook in hooks
            if hook.get("module") in inherited or hook.get("module") in explicit
        ]
    elif excluded:
        filtered = [
            hook
            for hook in hooks
            if hook.get("module") not in excluded or hook.get("module") in explicit
        ]
    else:
        return config

    updated = dict(config)
    updated["hooks"] = filtered
    logger.debug(
        "Filtered hooks: %d -> %d (exclude=%s, inherit=%s)",
        len(hooks),
        len(filtered),
        excluded,
        inherited,
    )
    return updated


def _inherit_live_agents(merged_config: dict, parent_coordinator: object) -> None:
    """Snapshot mode-contributed agents from the live parent registry."""
    try:
        live_agents = (parent_coordinator.config or {}).get("agents") or {}  # type: ignore[attr-defined]
    except AttributeError:
        live_agents = {}
    if not isinstance(live_agents, Mapping) or not live_agents:
        return

    child_agents = merged_config.setdefault("agents", {})
    for name, config in live_agents.items():
        if name not in child_agents:
            child_agents[name] = copy.deepcopy(config)


def _apply_orchestrator_override(merged_config: dict, override: dict) -> None:
    session_config = merged_config.setdefault("session", {})
    orchestrator = session_config.setdefault("orchestrator", {})
    orchestrator.setdefault("config", {}).update(override)
    logger.debug(
        "Applied orchestrator config override to session.orchestrator.config: %s",
        override,
    )


async def prepare_spawn(
    request: SpawnRequest,
    services: SessionLifecycleServices,
) -> PreparedSpawn:
    """Validate a spawn request and resolve its effective child config."""
    if request.agent_name == "self":
        agent_config: dict = {}
        logger.debug("Self-delegation: using parent config without agent overlay")
    elif request.agent_name not in request.agent_configs:
        raise ValueError(f"Agent '{request.agent_name}' not found in configuration")
    else:
        agent_config = request.agent_configs[request.agent_name]

    merged_config = services.merge_configs(request.parent_session.config, agent_config)
    parent_coordinator = getattr(request.parent_session, "coordinator", None)
    parent_trust_state = services.session_trust_state(request.parent_session)
    if parent_coordinator is not None:
        _inherit_live_agents(merged_config, parent_coordinator)

    if request.tool_inheritance and "tools" in merged_config:
        explicit_tools = [tool.get("module") for tool in agent_config.get("tools", [])]
        merged_config = filter_tools(
            merged_config,
            request.tool_inheritance,
            explicit_tools,
        )
    if request.hook_inheritance and "hooks" in merged_config:
        explicit_hooks = [hook.get("module") for hook in agent_config.get("hooks", [])]
        merged_config = filter_hooks(
            merged_config,
            request.hook_inheritance,
            explicit_hooks,
        )

    provider_preferences = request.provider_preferences
    if not provider_preferences:
        raw_preferences = agent_config.get("provider_preferences")
        if raw_preferences:
            from amplifier_foundation.spawn_utils import ProviderPreference

            provider_preferences = [
                ProviderPreference.from_dict(item) if isinstance(item, dict) else item
                for item in raw_preferences
            ]
            logger.debug(
                "Using routing-resolved provider_preferences from agent config "
                "for agent '%s' (%d preference(s))",
                request.agent_name,
                len(provider_preferences),
            )
    if provider_preferences:
        from amplifier_foundation import apply_provider_preferences_with_resolution

        merged_config = await apply_provider_preferences_with_resolution(
            merged_config,
            provider_preferences,
            request.parent_session.coordinator,
        )

    if request.orchestrator_config:
        _apply_orchestrator_override(merged_config, request.orchestrator_config)
    if request.session_metadata:
        merged_config.setdefault("session", {})["metadata"] = request.session_metadata
        logger.debug(
            "Injected session_metadata into child session config: %s",
            request.session_metadata,
        )

    sub_session_id = request.sub_session_id
    if not sub_session_id:
        sub_session_id = services.generate_sub_session_id(
            agent_name=request.agent_name,
            parent_session_id=request.parent_session.session_id,
            parent_trace_id=getattr(request.parent_session, "trace_id", None),
        )
    if sub_session_id is None:
        raise RuntimeError("Failed to generate a child session ID")

    return PreparedSpawn(
        request=request,
        agent_config=agent_config,
        merged_config=merged_config,
        sub_session_id=sub_session_id,
        parent_coordinator=parent_coordinator,
        parent_trust_state=parent_trust_state,
    )


__all__ = ["filter_hooks", "filter_tools", "prepare_spawn"]
