"""Issue #233 regression tests: runtime overlay propagation in spawn_sub_session.

Verifies that mode-contributed agents and skill capabilities in the parent's
LIVE coordinator state are visible to spawned child sessions.

ROOT CAUSE (confirmed by bug-hunter):
  - merge_configs(parent_session.config, agent_config) reads from the STATIC
    session-init snapshot. Mode-contributed agents live only in
    parent_session.coordinator.config["agents"] (the live registry).
  - Without the fix, same-mode siblings cannot delegate to each other even
    if they were contributed by the same mode activation.

SCENARIOS:
  S1 — top-level baseline (regression guard): parent has bundle-declared
       agents in session.config; child must see them (never broken).
  S2 — same-mode siblings (headline bug): mode contributes agents A and B
       in coordinator.config (not in session.config); spawning A must give
       child visibility of B.
  S3 — mixed: parent has X in session.config, mode contributes A in
       coordinator.config; spawning A; child sees both X and A.
  S4 — caller-independence: spawn called programmatically (not via
       tool-delegate); same expectations as S2 — fix must not rely on
       caller passing extra context.
  S5 — skill capability propagation: parent's coordinator has
       runtime_skill_overlay capability; child coordinator inherits it.
  S6 — parent isolation: propagation writes into the child's config only,
       never mutates the parent's.
  S7 — access-control declaration honored (fix/honor-agents-declaration):
       the spawned agent's OWN `agents:` Smart Single Value declaration
       ("none" | list-of-names | "all"/absent) must be respected when the
       live registry is unioned in, not silently overridden by the #233
       propagation. Covers all five rows of the target-behavior table:
       "none" -> {}, ["sibling_b"] -> {sibling_b} (fixes PR #178's
       under-delivery), ["explorer"] -> {explorer}, "all" -> full union,
       absent -> full union (both unchanged from #233 behavior).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_foundation import RUNTIME_SKILL_OVERLAY_CAPABILITY

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_parent_session(
    session_config_agents: dict | None = None,
    coordinator_config_agents: dict | None = None,
    coordinator_skills_capability: list | None = None,
) -> MagicMock:
    """Build a MagicMock parent session with configurable agent visibility.

    Args:
        session_config_agents:     Agents in the STATIC session.config snapshot.
        coordinator_config_agents: Agents in the LIVE coordinator.config registry.
                                   These are mode-contributed agents that are NOT
                                   in the static snapshot.
        coordinator_skills_capability: Skills registered in coordinator via
                                       runtime_skill_overlay capability.
    """
    parent = MagicMock()

    # Static session config snapshot (what merge_configs reads from)
    static_agents = dict(session_config_agents or {})
    parent.config = {
        "agents": static_agents,
        "session": {"orchestrator": "loop-basic"},
    }

    # Live coordinator config (what RuntimeOverlay writes to)
    live_agents = dict(coordinator_config_agents or {})
    parent.coordinator = MagicMock()
    parent.coordinator.config = {"agents": live_agents}

    # Capability store on coordinator
    cap_store: dict = {}
    if coordinator_skills_capability is not None:
        cap_store[RUNTIME_SKILL_OVERLAY_CAPABILITY] = list(
            coordinator_skills_capability
        )

    def _get_capability(name: str):
        return cap_store.get(name)

    parent.coordinator.get_capability = MagicMock(side_effect=_get_capability)

    # Other attributes spawn_sub_session accesses
    parent.session_id = "parent-session-id"
    parent.coordinator.display_system = MagicMock()
    parent.coordinator.approval_system = MagicMock()
    parent.coordinator.cancellation = MagicMock()
    parent.coordinator.cancellation.register_child = MagicMock()
    parent.coordinator.cancellation.unregister_child = MagicMock()
    parent.coordinator.get = MagicMock(return_value=None)
    parent.loader = None

    return parent


def _make_child_session_mock() -> MagicMock:
    """Build a MagicMock child session for AmplifierSession() return value."""
    child = MagicMock()
    child.session_id = "child-session-id"

    # Capability store on child coordinator
    cap_store: dict = {}

    def _register_capability(name: str, value) -> None:
        cap_store[name] = value

    def _get_capability(name: str):
        return cap_store.get(name)

    child.coordinator = MagicMock()
    child.coordinator.register_capability = MagicMock(side_effect=_register_capability)
    child.coordinator.get_capability = MagicMock(side_effect=_get_capability)
    child.coordinator.get = MagicMock(return_value=None)
    child.coordinator.cancellation = MagicMock()
    child.coordinator.mount = AsyncMock()
    child.coordinator.display_system = MagicMock()
    child.coordinator.hooks = MagicMock()
    child.coordinator.hooks.emit = AsyncMock()
    child.coordinator.hooks.register = MagicMock(return_value=MagicMock())
    child.initialize = AsyncMock()
    child.execute = AsyncMock(return_value="agent output")
    child.cleanup = AsyncMock()
    child.coordinator.approval_system = MagicMock()

    return child


async def _run_spawn(
    parent_session: MagicMock,
    agent_configs: dict,
    child_session_mock: MagicMock,
    agent_name: str = "mode_agent_A",
    captured_config: dict | None = None,
    post_initialize_callback=None,
    bridge_mock: AsyncMock | None = None,
    instruction: str = "Do something",
    expand_instruction_mentions: bool = True,
    hook_inheritance: dict[str, list[str]] | None = None,
) -> MagicMock:
    """Run spawn_sub_session with all heavy dependencies mocked.

    Returns the child_session mock for inspection.
    Optionally captures the merged_config passed to AmplifierSession constructor.
    """
    from amplifier_app_cli.session_spawner import spawn_sub_session

    def _make_session(config, **kwargs):
        if captured_config is not None:
            captured_config.clear()
            captured_config.update(config)
        return child_session_mock

    # Patch all heavy dependencies so the test runs fast and deterministically.
    # Key: we capture 'config' in _make_session to inspect agents propagation.
    # AmplifierSession is imported at module level in session_spawner.py, so
    # we patch it in the session_spawner module namespace.
    cost_bridge = bridge_mock or AsyncMock()
    with (
        patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            side_effect=_make_session,
        ),
        patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value="child-session-id",
        ),
        patch(
            "amplifier_app_cli.session_spawner.bridge_child_cost",
            new=cost_bridge,
        ),
        patch(
            "amplifier_app_cli.session_spawner._extract_bundle_context",
            return_value=None,
        ),
        patch("amplifier_app_cli.session_store.SessionStore"),
        patch("amplifier_app_cli.lib.mention_loading.app_resolver.AppMentionResolver"),
        patch(
            "amplifier_app_cli.paths.create_foundation_resolver",
            return_value=MagicMock(),
        ),
        patch("amplifier_foundation.mentions.ContentDeduplicator"),
    ):
        await spawn_sub_session(
            agent_name=agent_name,
            instruction=instruction,
            parent_session=parent_session,
            agent_configs=agent_configs,
            expand_instruction_mentions=expand_instruction_mentions,
            hook_inheritance=hook_inheritance,
            post_initialize_callback=post_initialize_callback,
        )

    return child_session_mock


# ---------------------------------------------------------------------------
# S1 — Top-level baseline (regression guard)
# ---------------------------------------------------------------------------


class TestS1Baseline:
    """Bundle-declared agents in session.config must be visible in child."""

    @pytest.mark.asyncio
    async def test_bundle_agent_in_session_config_visible_in_child(self) -> None:
        """Agents declared in session.config (bundle baseline) must reach child."""
        parent = _make_parent_session(
            session_config_agents={
                "baseline_agent": {"description": "bundle-declared agent"},
                "mode_agent_A": {"description": "Agent A"},
            },
            coordinator_config_agents={
                "baseline_agent": {"description": "bundle-declared agent"},
                "mode_agent_A": {"description": "Agent A"},
            },
        )
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {"mode_agent_A": {"description": "Agent A"}},
            child,
            "mode_agent_A",
            captured,
        )

        assert "baseline_agent" in captured.get("agents", {}), (
            "Agents in session.config must be visible in child merged_config. "
            f"agents in child config: {list(captured.get('agents', {}).keys())}"
        )


# ---------------------------------------------------------------------------
# S2 — Same-mode siblings (headline bug #233)
# ---------------------------------------------------------------------------


class TestS2SameModeSiblings:
    """Mode-contributed agents in coordinator.config must be visible in child."""

    @pytest.mark.asyncio
    async def test_mode_contributed_sibling_visible_in_child(self) -> None:
        """mode_agent_B contributed by a mode must be reachable from mode_agent_A's sub-session.

        This is the headline bug: mode_agent_B is in coordinator.config["agents"]
        (the live registry, written by RuntimeOverlay._mount) but NOT in
        parent_session.config["agents"] (the static init snapshot).

        Without the fix: merge_configs(parent_session.config, ...) reads the
        static snapshot → mode_agent_B absent → delegation fails.

        With the fix: propagation block reads coordinator.config["agents"] →
        mode_agent_B is copied into merged_config → delegation works.
        """
        parent = _make_parent_session(
            # Static snapshot: only baseline + A; mode_agent_B is NOT in snapshot
            session_config_agents={
                "baseline_agent": {"description": "bundle agent"},
                "mode_agent_A": {"description": "Agent A"},
            },
            # Live registry: both A and B (mode contributed both)
            coordinator_config_agents={
                "baseline_agent": {"description": "bundle agent"},
                "mode_agent_A": {"description": "Agent A"},
                "mode_agent_B": {"description": "Agent B — mode-contributed sibling"},
            },
        )
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {
                "mode_agent_A": {"description": "Agent A"},
                "mode_agent_B": {"description": "Agent B — mode-contributed sibling"},
            },
            child,
            "mode_agent_A",
            captured,
        )

        assert "mode_agent_B" in captured.get("agents", {}), (
            "mode_agent_B is a mode-contributed sibling. It exists in "
            "coordinator.config['agents'] (live registry) but NOT in "
            "parent_session.config['agents'] (static snapshot). "
            "After the issue #233 fix, spawn must propagate live registry agents "
            "into child merged_config so same-mode siblings can delegate. "
            f"agents in child config: {list(captured.get('agents', {}).keys())}"
        )


# ---------------------------------------------------------------------------
# S3 — Mixed: session baseline + mode contribution
# ---------------------------------------------------------------------------


class TestS3Mixed:
    """Child must see both static and live-registry agents."""

    @pytest.mark.asyncio
    async def test_child_sees_both_static_and_mode_contributed_agents(self) -> None:
        """After propagation, child sees bundle-declared X AND mode-contributed A."""
        parent = _make_parent_session(
            # Static snapshot: only X
            session_config_agents={
                "agent_X": {"description": "Bundle-declared agent X"},
            },
            # Live registry: X AND mode-contributed A
            coordinator_config_agents={
                "agent_X": {"description": "Bundle-declared agent X"},
                "mode_agent_A": {"description": "Mode-contributed agent A"},
            },
        )
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {"mode_agent_A": {"description": "Mode-contributed agent A"}},
            child,
            "mode_agent_A",
            captured,
        )

        assert "agent_X" in captured.get("agents", {}), (
            "Bundle-declared agent X must remain visible in child after propagation. "
            f"agents in child config: {list(captured.get('agents', {}).keys())}"
        )
        assert "mode_agent_A" in captured.get("agents", {}), (
            "Mode-contributed agent A must be visible in child after propagation. "
            f"agents in child config: {list(captured.get('agents', {}).keys())}"
        )


# ---------------------------------------------------------------------------
# S4 — Caller-independence
# ---------------------------------------------------------------------------


class TestS4CallerIndependence:
    """Fix must work regardless of how spawn is invoked (not caller-specific)."""

    @pytest.mark.asyncio
    async def test_propagation_works_without_extra_caller_context(self) -> None:
        """Propagation reads coordinator.config directly, not from caller parameters.

        This test verifies the architectural decision: the fix reads from
        parent_session.coordinator.config (source of truth) not from any
        caller-supplied parameter. The fix-report's symptom fix relied on
        a caller-supplied 'agent_configs' parameter which would silently
        regress when spawn is called from a non-tool-delegate path.
        """
        parent = _make_parent_session(
            # Static snapshot: only the spawned agent (not the sibling)
            session_config_agents={
                "agent_spawned": {"description": "The agent being spawned"},
            },
            # Live registry: both spawned agent AND sibling
            coordinator_config_agents={
                "agent_spawned": {"description": "The agent being spawned"},
                "mode_sibling": {"description": "Sibling from same mode"},
            },
        )
        child = _make_child_session_mock()
        captured: dict = {}

        # Note: agent_configs only contains the spawned agent,
        # NOT the sibling. The fix must discover the sibling from coordinator.config.
        await _run_spawn(
            parent,
            {"agent_spawned": {"description": "The agent being spawned"}},
            child,
            "agent_spawned",
            captured,
        )

        assert "mode_sibling" in captured.get("agents", {}), (
            "mode_sibling exists in coordinator.config['agents'] (live registry) "
            "but was NOT passed in agent_configs. "
            "The fix must read from coordinator.config directly — not rely on "
            "the caller passing all relevant agents. "
            f"agents in child config: {list(captured.get('agents', {}).keys())}"
        )


# ---------------------------------------------------------------------------
# S5 — Skill capability propagation
# ---------------------------------------------------------------------------


class TestS5SkillCapabilityPropagation:
    """runtime_skill_overlay capability must be propagated to child coordinator."""

    @pytest.mark.asyncio
    async def test_skill_capability_propagated_to_child_coordinator(self) -> None:
        """Child coordinator inherits runtime_skill_overlay capability from parent.

        Without this propagation, a mode-contributed agent running in a child
        session cannot discover the skills contributed by the same mode — the
        child's tool-skills module reads from its OWN coordinator's capability,
        which is empty unless we copy it.
        """
        skills_list = ["@modes:skills/mode-design-discipline"]
        parent = _make_parent_session(
            session_config_agents={
                "mode_agent_A": {"description": "Agent A"},
            },
            coordinator_config_agents={
                "mode_agent_A": {"description": "Agent A"},
            },
            coordinator_skills_capability=skills_list,
        )
        child = _make_child_session_mock()

        await _run_spawn(
            parent,
            {"mode_agent_A": {"description": "Agent A"}},
            child,
            "mode_agent_A",
        )

        # Inspect child coordinator capability registrations
        registered = child.coordinator.get_capability(RUNTIME_SKILL_OVERLAY_CAPABILITY)

        assert registered is not None, (
            f"Child coordinator must have '{RUNTIME_SKILL_OVERLAY_CAPABILITY}' "
            "capability registered after spawn. Without this, tool-skills in "
            "the child session cannot discover mode-contributed skills. "
            "All registered capabilities: "
            f"{[c.args[0] for c in child.coordinator.register_capability.call_args_list if c.args]}"
        )
        assert set(registered) == set(skills_list), (
            f"Child coordinator's '{RUNTIME_SKILL_OVERLAY_CAPABILITY}' capability "
            f"must contain the same skills as the parent. "
            f"Expected {skills_list!r}, got {registered!r}"
        )


# ---------------------------------------------------------------------------
# S6 — Isolation: propagation must not reach back into the parent
# ---------------------------------------------------------------------------


class TestS6ParentIsolation:
    """The propagation writes into the CHILD's config only, never the parent's."""

    @pytest.mark.asyncio
    async def test_propagation_does_not_mutate_parent_config(self) -> None:
        """An empty `agents` dict in session.config must not become the
        parent's copy of the live registry.

        merge_configs() deep-copies the merged `agents` dict only when it is
        non-empty, and merge_agent_dicts() starts from a shallow parent.copy()
        — so when the parent's session.config carries an EMPTY agents dict
        (e.g. it was itself spawned with `agents: none`), the merged config's
        "agents" value IS the parent's own dict object. Filling that dict in
        place would silently rewrite the running parent session's config and
        hand the child the same object, so a later mutation on either side is
        visible to the other.
        """
        parent = _make_parent_session(
            session_config_agents={},  # present but empty — the aliasing trigger
            coordinator_config_agents={"mode_agent_A": {"description": "Agent A"}},
        )
        parent_agents_before = parent.config["agents"]
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {"mode_agent_A": {"description": "Agent A"}},
            child,
            "mode_agent_A",
            captured,
        )

        # Child sees the live registry (issue #233 behavior, unchanged).
        assert "mode_agent_A" in captured.get("agents", {})

        # ...and the parent's own config is untouched.
        assert parent.config["agents"] == {}, (
            "spawn_sub_session mutated the PARENT session's config['agents'] "
            f"in place: {parent.config['agents']!r}"
        )
        assert parent_agents_before == {}
        assert captured["agents"] is not parent.config["agents"], (
            "child and parent share the same agents dict object — a later "
            "mutation on either side would leak across the session boundary"
        )
        assert captured["agents"] is not parent.coordinator.config["agents"], (
            "child's agents dict is the parent's LIVE registry object"
        )


# ---------------------------------------------------------------------------
# S7 — Access-control declaration honored (fix/honor-agents-declaration)
# ---------------------------------------------------------------------------


class TestS7AccessControlDeclaration:
    """The spawned agent's OWN `agents:` declaration gates propagation.

    Fixture shared across all five rows (matches the issue's target-behavior
    table exactly):

        Parent STATIC agents  (session.config):     {explorer, builder}
        Parent LIVE registry  (coordinator.config):  {explorer, builder, sibling_b}
                                                      (sibling_b is mode-contributed
                                                       only -- absent from the static
                                                       snapshot)

    The agent "explorer" is spawned with a varying `agents:` overlay value.
    Each assertion checks the EXACT resulting child agent name set, not mere
    membership -- both over-delivery (declared "none"/allowlist but child
    gets more) and under-delivery (declared allowlist but child gets less,
    PR #178's original bug) are real failure modes here.
    """

    def _make_fixture_parent(self) -> MagicMock:
        return _make_parent_session(
            session_config_agents={
                "explorer": {"description": "Explorer agent"},
                "builder": {"description": "Builder agent"},
            },
            coordinator_config_agents={
                "explorer": {"description": "Explorer agent"},
                "builder": {"description": "Builder agent"},
                "sibling_b": {"description": "Mode-contributed sibling B"},
            },
        )

    @pytest.mark.asyncio
    async def test_agents_none_disables_all_delegation(self) -> None:
        """`agents: "none"` must yield an EMPTY child agents dict.

        Before the fix: the #233 propagation block unconditionally unioned
        in the full live registry, reopening delegation the overlay
        explicitly disabled.
        """
        parent = self._make_fixture_parent()
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {"explorer": {"description": "Explorer agent", "agents": "none"}},
            child,
            "explorer",
            captured,
        )

        assert captured.get("agents", {}) == {}, (
            "agents: 'none' must disable ALL sub-agent delegation. "
            f"Got: {sorted(captured.get('agents', {}).keys())}"
        )

    @pytest.mark.asyncio
    async def test_agents_list_of_live_only_name_is_honored_from_live_registry(
        self,
    ) -> None:
        """`agents: ["sibling_b"]` must yield EXACTLY {sibling_b}.

        sibling_b is mode-contributed-only (absent from the static parent
        snapshot). merge_configs() filters the STATIC dict and resolves this
        to {} (PR #178's documented under-delivery). The spawner must apply
        the same allowlist against the LIVE registry so sibling_b still
        reaches the child -- and nothing else does.
        """
        parent = self._make_fixture_parent()
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {"explorer": {"description": "Explorer agent", "agents": ["sibling_b"]}},
            child,
            "explorer",
            captured,
        )

        assert captured.get("agents", {}).keys() == {"sibling_b"}, (
            "agents: ['sibling_b'] must yield EXACTLY {sibling_b} -- honored "
            "from the live registry even though sibling_b is absent from "
            f"the static snapshot. Got: {sorted(captured.get('agents', {}).keys())}"
        )

    @pytest.mark.asyncio
    async def test_agents_list_of_static_name_is_honored_as_allowlist(self) -> None:
        """`agents: ["explorer"]` must yield EXACTLY {explorer}.

        Before the fix: the #233 propagation block would blow the allowlist
        open by unioning in the full live registry (builder, sibling_b too).
        """
        parent = self._make_fixture_parent()
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {"explorer": {"description": "Explorer agent", "agents": ["explorer"]}},
            child,
            "explorer",
            captured,
        )

        assert captured.get("agents", {}).keys() == {"explorer"}, (
            "agents: ['explorer'] must yield EXACTLY {explorer} -- the "
            "allowlist must not be blown open by live-registry propagation. "
            f"Got: {sorted(captured.get('agents', {}).keys())}"
        )

    @pytest.mark.asyncio
    async def test_agents_all_yields_full_union(self) -> None:
        """`agents: "all"` must yield the FULL union: {explorer, builder, sibling_b}.

        Unchanged from #233 behavior -- explicit "all" inherits everything,
        static and live-registry alike.
        """
        parent = self._make_fixture_parent()
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {"explorer": {"description": "Explorer agent", "agents": "all"}},
            child,
            "explorer",
            captured,
        )

        assert captured.get("agents", {}).keys() == {
            "explorer",
            "builder",
            "sibling_b",
        }, (
            "agents: 'all' must yield the full union of static + live agents. "
            f"Got: {sorted(captured.get('agents', {}).keys())}"
        )

    @pytest.mark.asyncio
    async def test_agents_absent_yields_full_union(self) -> None:
        """No `agents:` key at all must yield the FULL union (unrestricted).

        Unchanged from #233 behavior -- absence of the declaration means
        "unrestricted," identical to explicit "all".
        """
        parent = self._make_fixture_parent()
        child = _make_child_session_mock()
        captured: dict = {}

        await _run_spawn(
            parent,
            {"explorer": {"description": "Explorer agent"}},  # no "agents" key
            child,
            "explorer",
            captured,
        )

        assert captured.get("agents", {}).keys() == {
            "explorer",
            "builder",
            "sibling_b",
        }, (
            "Absent agents: declaration must be unrestricted (full union), "
            "identical to explicit 'all'. "
            f"Got: {sorted(captured.get('agents', {}).keys())}"
        )


class TestHookInheritance:
    """Child hook filters must be applied without mutating the parent."""

    @pytest.mark.asyncio
    async def test_routing_hooks_removed_before_initialize_parent_unchanged(
        self,
    ) -> None:
        parent = _make_parent_session()
        parent_hooks = [
            {"module": "hooks-routing", "config": {"default_matrix": "parent"}},
            {"module": "hooks-matrix-guard", "config": {"strict": True}},
            {"module": "hooks-logging", "config": {"level": "info"}},
        ]
        parent.config["hooks"] = parent_hooks
        child = _make_child_session_mock()
        captured: dict = {}

        def assert_filtered_before_initialize() -> None:
            assert [hook["module"] for hook in captured["hooks"]] == ["hooks-logging"]

        child.initialize.side_effect = assert_filtered_before_initialize

        await _run_spawn(
            parent,
            {"mode_agent_A": {"agents": "none"}},
            child,
            captured_config=captured,
            hook_inheritance={"exclude_hooks": ["hooks-routing", "hooks-matrix-guard"]},
        )

        child.initialize.assert_awaited_once()
        assert [hook["module"] for hook in captured["hooks"]] == ["hooks-logging"]
        assert parent.config["hooks"] is parent_hooks
        assert parent.config["hooks"] == [
            {"module": "hooks-routing", "config": {"default_matrix": "parent"}},
            {"module": "hooks-matrix-guard", "config": {"strict": True}},
            {"module": "hooks-logging", "config": {"level": "info"}},
        ]


class TestPostInitializeLifecycle:
    """The callback seam must preserve ordering, cleanup, and cost accounting."""

    @pytest.mark.asyncio
    async def test_preexpanded_instruction_skips_child_mention_resolution(self) -> None:
        parent = _make_parent_session()
        child = _make_child_session_mock()
        mention_resolver = MagicMock()
        child.coordinator.get_capability.side_effect = lambda name: (
            mention_resolver if name == "mention_resolver" else None
        )
        instruction = (
            '<context_file paths="@testbundle:task.md">'
            "ONE_TIME_MENTION_SNAPSHOT"
            "</context_file>\n\n"
            "Review @testbundle:task.md."
        )

        await _run_spawn(
            parent,
            {"mode_agent_A": {}},
            child,
            instruction=instruction,
            expand_instruction_mentions=False,
        )

        mention_resolver.resolve.assert_not_called()
        child.execute.assert_awaited_once_with(instruction)

    @pytest.mark.asyncio
    async def test_callback_runs_after_initialize_and_before_execute(self) -> None:
        parent = _make_parent_session()
        child = _make_child_session_mock()
        events: list[str] = []
        child.initialize.side_effect = lambda: events.append("initialize")
        child.execute.side_effect = lambda _instruction: (
            events.append("execute") or "output"
        )

        def callback(_child) -> None:
            events.append("callback")

        bridge = AsyncMock(side_effect=lambda **_kwargs: events.append("bridge"))
        child.cleanup.side_effect = lambda: events.append("cleanup")

        await _run_spawn(
            parent,
            {"mode_agent_A": {}},
            child,
            post_initialize_callback=callback,
            bridge_mock=bridge,
        )

        assert events == ["initialize", "callback", "execute", "bridge", "cleanup"]
        bridge.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "expected_exception"),
        [
            (RuntimeError("provider failed"), RuntimeError),
            (asyncio.CancelledError(), asyncio.CancelledError),
        ],
    )
    async def test_execute_failure_or_cancellation_bridges_once_and_cleans_up(
        self,
        failure,
        expected_exception,
    ) -> None:
        parent = _make_parent_session()
        child = _make_child_session_mock()
        child.execute.side_effect = failure
        bridge = AsyncMock()

        with pytest.raises(expected_exception):
            await _run_spawn(
                parent,
                {"mode_agent_A": {}},
                child,
                bridge_mock=bridge,
            )

        bridge.assert_awaited_once()
        child.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "expected_exception"),
        [
            (RuntimeError("pin rejected"), RuntimeError),
            (asyncio.CancelledError(), asyncio.CancelledError),
        ],
    )
    async def test_callback_failure_or_cancellation_never_executes_and_cleans_up(
        self,
        failure,
        expected_exception,
    ) -> None:
        parent = _make_parent_session()
        child = _make_child_session_mock()
        bridge = AsyncMock()

        def callback(_child) -> None:
            raise failure

        with pytest.raises(expected_exception):
            await _run_spawn(
                parent,
                {"mode_agent_A": {}},
                child,
                post_initialize_callback=callback,
                bridge_mock=bridge,
            )

        child.execute.assert_not_awaited()
        bridge.assert_awaited_once()
        child.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cross_vendor_callback_fails_before_child_or_parent_request(
        self,
    ) -> None:
        from amplifier_app_cli.deep_plan import (
            DeepPlanTarget,
            _prepare_child_provider,
        )

        parent = _make_parent_session()
        parent.execute = AsyncMock()
        child = _make_child_session_mock()
        pin = MagicMock()
        pin.pin.side_effect = ValueError(
            "Cannot pin Anthropic provider 'fable' while current provider is OpenAI."
        )
        provider = MagicMock()
        provider.get_info.return_value = SimpleNamespace(
            id="anthropic",
            defaults={"model": "claude-fable-5"},
        )
        child.coordinator.get.side_effect = lambda name: (
            {"fable": provider} if name == "providers" else None
        )
        child.coordinator.get_capability.side_effect = lambda name: (
            pin if name == "conversation.provider_pin" else None
        )
        target = DeepPlanTarget(
            provider="fable",
            model="claude-fable-5",
            vendor="anthropic",
            effort="max",
            provider_preferences=(),
        )

        with pytest.raises(ValueError, match="current provider is OpenAI"):
            await _run_spawn(
                parent,
                {"mode_agent_A": {}},
                child,
                post_initialize_callback=lambda session: _prepare_child_provider(
                    session, target, []
                ),
            )

        child.execute.assert_not_awaited()
        parent.execute.assert_not_awaited()
        child.cleanup.assert_awaited_once()
