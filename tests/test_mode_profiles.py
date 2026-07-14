from types import SimpleNamespace

import pytest

from amplifier_app_cli.ui.interaction_state import TrustState
from amplifier_app_cli.ui.mode_profiles import ModeName
from amplifier_app_cli.ui.mode_profiles import ModeProfileRegistry
from amplifier_app_cli.ui.mode_profiles import ModeRuntimeBinding
from amplifier_app_cli.ui.mode_profiles import ReasoningEffort
from amplifier_app_cli.ui.mode_profiles import RenderProfile


def test_registry_exposes_normative_five_modes_and_cycles_postures() -> None:
    registry = ModeProfileRegistry()

    assert registry.names == (
        "chat",
        "plan",
        "brainstorm",
        "build",
        "auto",
    )
    assert registry.cycle("chat").name == ModeName.BUILD
    assert registry.cycle("build").name == ModeName.PLAN
    assert registry.cycle("plan").name == ModeName.AUTO
    assert registry.cycle("auto").name == ModeName.BRAINSTORM
    assert registry.cycle("brainstorm").name == ModeName.CHAT
    assert registry.cycle("chat", -1).name == ModeName.BRAINSTORM


def test_shift_tab_cycle_requires_an_explicit_bypass_step() -> None:
    from amplifier_app_cli.main import _next_shift_tab_state

    registry = ModeProfileRegistry()

    assert _next_shift_tab_state("plan", "plan", registry) == ("auto", "auto")
    assert _next_shift_tab_state("auto", "auto", registry) == (
        "auto",
        "bypass",
    )
    assert _next_shift_tab_state("auto", "bypass", registry) == (
        "brainstorm",
        "brainstorm",
    )


def test_profiles_bind_runtime_and_render_semantics() -> None:
    registry = ModeProfileRegistry()

    plan = registry.get("plan")
    assert plan.render_profile == RenderProfile.PLAN
    assert plan.reasoning_effort == ReasoningEffort.HIGH
    assert plan.trust_preset == "plan"

    auto = registry.get("auto")
    assert auto.render_profile == RenderProfile.OPERATIONAL
    assert auto.reasoning_effort == ReasoningEffort.XHIGH
    assert auto.trust_preset == "auto"
    assert auto.color == "#e0a458"

    assert registry.get("bypass").name == ModeName.CHAT


def test_unknown_or_missing_mode_falls_back_to_chat() -> None:
    registry = ModeProfileRegistry()

    assert registry.get(None).name == ModeName.CHAT
    assert registry.get("bundle-custom").name == ModeName.CHAT


class _Coordinator:
    def __init__(self) -> None:
        self.session_state = {}
        self.orchestrator = SimpleNamespace(config={})
        self.provider = SimpleNamespace(default_model="old", config={})
        self.resolver = SimpleNamespace(
            resolve=self._resolve,
        )

    async def _resolve(self, role):
        return [SimpleNamespace(provider="openai", model=f"model-for-{role}")]

    def get(self, name):
        return {
            "orchestrator": self.orchestrator,
            "providers": {"openai": self.provider},
        }.get(name)

    def get_capability(self, name):
        return self.resolver if name == "model_role_resolver" else None


@pytest.mark.asyncio
async def test_runtime_binding_applies_all_mode_dimensions() -> None:
    coordinator = _Coordinator()
    trust = TrustState()
    binding = ModeRuntimeBinding(coordinator, ModeProfileRegistry())

    snapshot = await binding.apply("build")

    assert trust.active.name == "chat"
    assert coordinator.orchestrator.config["reasoning_effort"] == "high"
    assert coordinator.provider.default_model == "model-for-coding"
    assert coordinator.provider.config["default_model"] == "model-for-coding"
    assert snapshot.render_profile == RenderProfile.OPERATIONAL
    assert coordinator.session_state["ui.mode_profile"] == {
        "mode": "build",
        "render_profile": "operational",
        "model_role": "coding",
        "reasoning_effort": "high",
        "provider": "openai",
        "model": "model-for-coding",
    }


def test_runtime_binding_still_applies_local_profile_without_routing() -> None:
    coordinator = _Coordinator()
    coordinator.resolver = None
    binding = ModeRuntimeBinding(coordinator, ModeProfileRegistry())

    snapshot = binding.apply_local("brainstorm")

    assert snapshot.render_profile == RenderProfile.DIVERGENT
    assert coordinator.orchestrator.config["reasoning_effort"] == "high"


def test_runtime_binding_always_leaves_permission_posture_independent() -> None:
    coordinator = _Coordinator()
    trust = TrustState(initial="build")
    binding = ModeRuntimeBinding(
        coordinator,
        ModeProfileRegistry(),
    )

    binding.apply_local("brainstorm")

    assert trust.active.name == "build"
