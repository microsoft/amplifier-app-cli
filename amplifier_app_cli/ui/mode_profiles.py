"""TUI and runtime profiles for Amplifier's five interaction modes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ModeName(str, Enum):
    CHAT = "chat"
    PLAN = "plan"
    BRAINSTORM = "brainstorm"
    BUILD = "build"
    AUTO = "auto"


class RenderProfile(str, Enum):
    CONVERSATIONAL = "conversational"
    PLAN = "plan"
    DIVERGENT = "divergent"
    OPERATIONAL = "operational"


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


@dataclass(frozen=True, slots=True)
class ModeProfile:
    name: ModeName
    autonomy: str
    render_profile: RenderProfile
    model_role: str
    reasoning_effort: ReasoningEffort
    trust_preset: str
    color: str


DEFAULT_MODE_PROFILES: tuple[ModeProfile, ...] = (
    ModeProfile(
        ModeName.CHAT,
        "answer-first; ask before consequential tools",
        RenderProfile.CONVERSATIONAL,
        "default",
        ReasoningEffort.MEDIUM,
        "chat",
        "#6b7487",
    ),
    ModeProfile(
        ModeName.PLAN,
        "read-only analysis and implementation planning",
        RenderProfile.PLAN,
        "reasoning",
        ReasoningEffort.HIGH,
        "plan",
        "#7aa2f7",
    ),
    ModeProfile(
        ModeName.BRAINSTORM,
        "no tools; divergent exploration",
        RenderProfile.DIVERGENT,
        "reasoning",
        ReasoningEffort.HIGH,
        "brainstorm",
        "#6fc3c3",
    ),
    ModeProfile(
        ModeName.BUILD,
        "execute within explicit trust boundaries",
        RenderProfile.OPERATIONAL,
        "coding",
        ReasoningEffort.HIGH,
        "build",
        "#7ec699",
    ),
    ModeProfile(
        ModeName.AUTO,
        "classifier-gated autonomous execution",
        RenderProfile.OPERATIONAL,
        "coding",
        ReasoningEffort.XHIGH,
        "auto",
        "#e0a458",
    ),
)

_SHIFT_TAB_CYCLE = (
    ModeName.CHAT,
    ModeName.BUILD,
    ModeName.PLAN,
    ModeName.AUTO,
    ModeName.BRAINSTORM,
)


class ModeProfileRegistry:
    def __init__(
        self, profiles: tuple[ModeProfile, ...] = DEFAULT_MODE_PROFILES
    ) -> None:
        self._profiles = profiles
        self._by_name = {profile.name.value: profile for profile in profiles}
        if len(self._by_name) != len(profiles):
            raise ValueError("mode profile names must be unique")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(profile.name.value for profile in self._profiles)

    def get(self, name: str | None) -> ModeProfile:
        return self._by_name.get(name or "chat", self._by_name[ModeName.CHAT.value])

    def cycle(self, current: str | None, offset: int = 1) -> ModeProfile:
        names = tuple(name.value for name in _SHIFT_TAB_CYCLE)
        try:
            index = names.index(current or ModeName.CHAT.value)
        except ValueError:
            return self._by_name[names[0 if offset >= 0 else -1]]
        return self._by_name[names[(index + offset) % len(names)]]


@dataclass(frozen=True, slots=True)
class ModeRuntimeSnapshot:
    mode: ModeName
    render_profile: RenderProfile
    model_role: str
    reasoning_effort: ReasoningEffort
    provider: str = ""
    model: str = ""


class ModeRuntimeBinding:
    """Apply a UI mode to the live Amplifier coordinator and its modules."""

    def __init__(
        self,
        coordinator: Any,
        registry: ModeProfileRegistry,
    ) -> None:
        self._coordinator = coordinator
        self._registry = registry
        self._snapshot: ModeRuntimeSnapshot | None = None

    @property
    def snapshot(self) -> ModeRuntimeSnapshot | None:
        return self._snapshot

    def apply_local(self, name: str | None) -> ModeRuntimeSnapshot:
        profile = self._registry.get(name)
        self._set_reasoning_effort(profile.reasoning_effort)
        snapshot = ModeRuntimeSnapshot(
            profile.name,
            profile.render_profile,
            profile.model_role,
            profile.reasoning_effort,
            self._snapshot.provider if self._snapshot is not None else "",
            self._snapshot.model if self._snapshot is not None else "",
        )
        self._snapshot = snapshot
        state = self._coordinator.session_state
        state["ui.mode_profile"] = {
            "mode": profile.name.value,
            "render_profile": profile.render_profile.value,
            "model_role": profile.model_role,
            "reasoning_effort": profile.reasoning_effort.value,
            "provider": snapshot.provider,
            "model": snapshot.model,
        }
        return snapshot

    async def apply(self, name: str | None) -> ModeRuntimeSnapshot:
        snapshot = self.apply_local(name)
        preference = await self._resolve_preference(snapshot.model_role)
        if preference is None:
            return snapshot
        provider_name = str(getattr(preference, "provider", "") or "")
        model = str(getattr(preference, "model", "") or "")
        providers = self._coordinator.get("providers") or {}
        provider = providers.get(provider_name)
        if provider is None or not model:
            return snapshot
        setattr(provider, "default_model", model)
        provider_config = getattr(provider, "config", None)
        if isinstance(provider_config, dict):
            provider_config["default_model"] = model
        resolved = ModeRuntimeSnapshot(
            snapshot.mode,
            snapshot.render_profile,
            snapshot.model_role,
            snapshot.reasoning_effort,
            provider_name,
            model,
        )
        self._snapshot = resolved
        self._coordinator.session_state["ui.mode_profile"].update(
            {"provider": provider_name, "model": model}
        )
        return resolved

    def _set_reasoning_effort(self, effort: ReasoningEffort) -> None:
        orchestrator = self._coordinator.get("orchestrator")
        config = getattr(orchestrator, "config", None)
        if isinstance(config, dict):
            config["reasoning_effort"] = effort.value

    async def _resolve_preference(self, model_role: str) -> Any | None:
        resolver = self._coordinator.get_capability("model_role_resolver")
        if resolver is None or not hasattr(resolver, "resolve"):
            return None
        try:
            preferences = await resolver.resolve(model_role)
        except Exception:
            logger.debug("Could not resolve mode model role", exc_info=True)
            return None
        return preferences[0] if preferences else None


__all__ = [
    "DEFAULT_MODE_PROFILES",
    "ModeName",
    "ModeProfile",
    "ModeProfileRegistry",
    "ModeRuntimeBinding",
    "ModeRuntimeSnapshot",
    "ReasoningEffort",
    "RenderProfile",
]
