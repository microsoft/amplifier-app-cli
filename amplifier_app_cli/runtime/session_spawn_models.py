"""Typed request and dependency models for sub-session lifecycle helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from amplifier_core import AmplifierSession

from amplifier_app_cli.runtime.bundle_context import SerializedBundleContext
from amplifier_app_cli.ui.interaction_state import TrustState


@dataclass(frozen=True, slots=True)
class SpawnRequest:
    """Public spawn arguments grouped for internal runtime handoff."""

    agent_name: str
    instruction: str
    parent_session: AmplifierSession
    agent_configs: dict[str, dict]
    sub_session_id: str | None = None
    tool_inheritance: dict[str, list[str]] | None = None
    hook_inheritance: dict[str, list[str]] | None = None
    orchestrator_config: dict | None = None
    parent_messages: list[dict] | None = None
    provider_preferences: list | None = None
    self_delegation_depth: int = 0
    session_metadata: dict | None = None
    use_subprocess: bool = False


@dataclass(frozen=True, slots=True)
class PreparedSpawn:
    """Validated and merged state shared by the two spawn transports."""

    request: SpawnRequest
    agent_config: dict
    merged_config: dict
    sub_session_id: str
    parent_coordinator: object | None
    parent_trust_state: TrustState | None


@dataclass(frozen=True, slots=True)
class ResumeRequest:
    """Public resume arguments grouped for internal runtime handoff."""

    sub_session_id: str
    instruction: str
    parent_session: AmplifierSession | None = None


@dataclass(frozen=True, slots=True)
class SessionLifecycleServices:
    """Patch-preserving dependencies supplied by ``session_spawner``.

    Tests and integrations historically patch symbols on the public facade.
    Constructing this model for every call keeps those seams live while the
    implementation remains split across focused modules.
    """

    session_factory: Callable[..., AmplifierSession]
    merge_configs: Callable[[dict, dict], dict]
    generate_sub_session_id: Callable[..., str | None]
    bridge_child_cost: Callable[..., Awaitable[Any]]
    extract_bundle_context: Callable[[AmplifierSession], SerializedBundleContext | None]
    session_trust_state: Callable[[object], TrustState | None]
    session_bypass_permissions: Callable[[object], bool]
    propagate_task_status_tracker: Callable[[object, object], None]
    propagate_runtime_status_tracker: Callable[[object, object], None]
    spawn_sub_session: Callable[..., Awaitable[dict]]
    resume_sub_session: Callable[..., Awaitable[dict]]
    default_sys_paths: frozenset[str]


__all__ = [
    "PreparedSpawn",
    "ResumeRequest",
    "SessionLifecycleServices",
    "SpawnRequest",
]
