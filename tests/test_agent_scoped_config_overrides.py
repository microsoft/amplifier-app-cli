"""Tests for overrides.<module-id>.config propagation into agent-scoped modules.

Root cause: overrides.<id>.config was only ever applied to the ROOT bundle's
providers/tools/hooks lists (resolve_bundle_config()). A tool a sub-agent
introduces only in its own frontmatter (config["agents"][<name>]["tools"])
never received its override -- it was invisible to the mechanism because it
never appears in the root lists.

This module tests the shared pure helper `_apply_config_overrides_to_section`
that fixes this, applied to both the root sections and each agent's own
providers/tools/hooks sections.
"""

from amplifier_app_cli.agent_config import merge_configs
from amplifier_app_cli.lib.merge_utils import deep_merge
from amplifier_app_cli.runtime.config import _apply_config_overrides_to_section


class TestAgentOnlyDictEntry:
    """T1: agent-only dict-form tool entry (no config) + matching override."""

    def test_override_applied_and_other_keys_preserved(self):
        section = [
            {
                "module": "tool-context-intelligence-query",
                "source": "git+https://example.com/repo@main#subdirectory=modules/x",
            }
        ]
        overrides = {
            "tool-context-intelligence-query": {
                "sources": {
                    "team-shared": {
                        "url": "https://shared.example.com",
                        "auth_mode": "entra",
                    }
                }
            }
        }

        result = _apply_config_overrides_to_section(section, overrides)

        assert len(result) == 1
        entry = result[0]
        assert entry["config"]["sources"]["team-shared"]["auth_mode"] == "entra"
        # Other keys (source) must be preserved.
        assert (
            entry["source"]
            == "git+https://example.com/repo@main#subdirectory=modules/x"
        )
        assert entry["module"] == "tool-context-intelligence-query"


class TestBareStringEntry:
    """T2: bare-string agent tool entry + override -> normalized to dict, override applied."""

    def test_bare_string_normalized_and_override_applied(self):
        section = ["tool-context-intelligence-query"]
        overrides = {"tool-context-intelligence-query": {"timeout": 30}}

        result = _apply_config_overrides_to_section(section, overrides)

        assert len(result) == 1
        entry = result[0]
        assert isinstance(entry, dict)
        assert entry["module"] == "tool-context-intelligence-query"
        assert entry["config"] == {"timeout": 30}


class TestPrecedence:
    """T3: override wins on key conflicts, existing keys preserved."""

    def test_override_wins_existing_keys_preserved(self):
        section = [
            {
                "module": "tool-x",
                "config": {"timeout": 5, "retries": 3},
            }
        ]
        overrides = {"tool-x": {"timeout": 30}}

        result = _apply_config_overrides_to_section(section, overrides)

        assert result[0]["config"] == {"timeout": 30, "retries": 3}
        # Sanity: matches manual deep_merge expectation.
        assert result[0]["config"] == deep_merge(
            {"timeout": 5, "retries": 3}, {"timeout": 30}
        )


class TestNoOp:
    """T4: entries with no matching override are returned byte-identical."""

    def test_bare_string_no_override_stays_bare(self):
        section = ["tool-unrelated"]
        result = _apply_config_overrides_to_section(section, {"tool-x": {"a": 1}})

        assert result == ["tool-unrelated"]
        assert result[0] is section[0]  # identity preserved, not just equality

    def test_dict_no_override_stays_same_object(self):
        entry = {"module": "tool-y", "config": {"a": 1}}
        section = [entry]
        result = _apply_config_overrides_to_section(section, {"tool-x": {"a": 1}})

        assert result[0] is entry  # no gratuitous copy when nothing changes

    def test_empty_overrides_returns_section_unchanged(self):
        section = [{"module": "tool-z"}]
        result = _apply_config_overrides_to_section(section, {})
        assert result is section

    def test_empty_section_returns_as_is(self):
        assert _apply_config_overrides_to_section([], {"tool-x": {"a": 1}}) == []


class TestIntegrationWithRealMergeConfigs:
    """T5: full path -- root override application + real merge_configs() at spawn.

    Simulates the exact David scenario: root tools = [], the query tool is
    declared ONLY inside the graph-analyst agent's own frontmatter with no
    config block, and overrides.tool-context-intelligence-query.config.sources
    is set at the app level. After applying the override to both root AND
    agent sections (the fix) and then spawning via the real merge_configs(),
    the merged child tool entry must carry config.sources -- not fall through
    to the tier-3 env-var fallback.
    """

    def test_agent_only_tool_receives_override_after_spawn_merge(self):
        config_overrides = {
            "tool-context-intelligence-query": {
                "sources": {
                    "team-shared": {
                        "url": "${AMPLIFIER_CONTEXT_INTELLIGENCE_TEAM_SHARED_URL}",
                        "auth_mode": "entra",
                        "auth_resource": "${AMPLIFIER_CONTEXT_INTELLIGENCE_TEAM_SHARED_AUTH_RESOURCE}",
                    }
                }
            }
        }

        # Mount plan shape as produced by resolve_bundle_config(): root tools
        # empty, the query tool only declared inside the agent's own section.
        mount_plan = {
            "tools": [],
            "agents": {
                "graph-analyst": {
                    "tools": [{"module": "tool-context-intelligence-query"}],
                }
            },
        }

        # --- Apply the fix: helper applied to root AND agent sections ---
        agents_section = mount_plan.get("agents")
        assert isinstance(agents_section, dict)
        for agent_cfg in agents_section.values():
            for section_key in ("providers", "tools", "hooks"):
                section = agent_cfg.get(section_key)
                if not section:
                    continue
                agent_cfg[section_key] = _apply_config_overrides_to_section(
                    section, config_overrides
                )

        # --- Real spawn merge (session_spawner.py:265 equivalent) ---
        parent_config = {"tools": mount_plan["tools"], "agents": mount_plan["agents"]}
        agent_overlay = mount_plan["agents"]["graph-analyst"]

        merged_child_config = merge_configs(parent_config, agent_overlay)

        child_tools = merged_child_config["tools"]
        matches = [
            t
            for t in child_tools
            if isinstance(t, dict)
            and t.get("module") == "tool-context-intelligence-query"
        ]
        assert len(matches) == 1, f"Expected exactly one query tool, got: {child_tools}"
        tool_entry = matches[0]
        assert "config" in tool_entry, (
            "Agent-only tool must carry the override's config after spawn merge "
            f"-- got: {tool_entry}"
        )
        assert tool_entry["config"]["sources"]["team-shared"]["auth_mode"] == "entra", (
            f"sources.team-shared override must survive spawn merge -- got: {tool_entry}"
        )
