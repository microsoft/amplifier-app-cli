"""Tests for agent_config module.

Covers merge_configs() correctness including isolation guarantees.
"""

from amplifier_app_cli.agent_config import merge_configs


class TestMergeConfigsIsolation:
    """Bug B (P0): merge_configs must deep-copy agents to prevent cross-session mutation.

    The routing hook (hooks-routing) calls on_session_start and mutates
    agent_cfg['provider_preferences'] in-place for every agent that has a
    model_role declaration in its frontmatter.

    Before the fix: merge_agent_dicts did a shallow copy of parent, so
    result["agents"] was the SAME Python object as parent["agents"]. A child
    session's routing hook mutating its own merged config would silently
    corrupt the parent's agent configs — a race condition with parallel spawns.

    After the fix: result["agents"] is a deep copy, so child-side mutations
    are fully isolated from the parent.
    """

    def test_agents_dict_is_different_object_after_merge(self):
        """result['agents'] must be a distinct Python object from parent['agents']."""
        parent = {
            "session": {"orchestrator": "loop-basic"},
            "agents": {
                "coder": {"description": "Coding agent"},
            },
        }
        result = merge_configs(parent, {})

        assert result["agents"] is not parent["agents"], (
            "merge_configs must deep-copy agents — result['agents'] must not alias parent['agents']"
        )

    def test_agent_cfg_dicts_are_different_objects_after_merge(self):
        """Individual agent_cfg dicts must be separate copies, not aliases."""
        parent = {
            "agents": {
                "coder": {
                    "description": "Coding agent",
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-sonnet-4-6"}
                    ],
                }
            }
        }
        result = merge_configs(parent, {})

        assert result["agents"]["coder"] is not parent["agents"]["coder"], (
            "merge_configs must deep-copy agent_cfg dicts — "
            "result['agents']['coder'] must not alias parent['agents']['coder']"
        )

    def test_routing_hook_mutation_does_not_corrupt_parent(self):
        """Simulates the routing hook mutating provider_preferences in-place.

        This is the exact Bug B scenario: child session routing hook fires
        session:start, resolves model_role, writes provider_preferences into
        agent_cfg dict. Without deep copy this corrupts the parent.
        """
        parent = {
            "session": {"orchestrator": "loop-basic"},
            "agents": {
                "coder": {
                    "description": "Coding agent",
                    "model_role": "coding",
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-sonnet-4-6"}
                    ],
                },
                "reviewer": {
                    "description": "Review agent",
                    "model_role": "critique",
                    "provider_preferences": [
                        {"provider": "openai", "model": "gpt-5.4"}
                    ],
                },
            },
        }
        original_coder_prefs = [{"provider": "anthropic", "model": "claude-sonnet-4-6"}]

        # Simulate child session creation via merge_configs
        child_merged = merge_configs(parent, {})

        # Simulate routing hook on child: mutates provider_preferences in-place
        # (hooks-routing calls agent_cfg["provider_preferences"] = [...])
        child_merged["agents"]["coder"]["provider_preferences"] = [
            {"provider": "openai", "model": "o3"}
        ]
        child_merged["agents"]["reviewer"]["provider_preferences"] = None

        # Parent's agent configs must be UNCHANGED
        assert (
            parent["agents"]["coder"]["provider_preferences"] == original_coder_prefs
        ), "Child routing hook mutation must not affect parent's agent configs (Bug B)"
        assert parent["agents"]["reviewer"]["provider_preferences"] == [
            {"provider": "openai", "model": "gpt-5.4"}
        ], "Child routing hook mutation must not affect parent's reviewer agent config"

    def test_parallel_spawns_do_not_interfere(self):
        """Two parallel child merges must each get isolated agent config copies.

        Simulates two agents spawned in parallel — both merging from the same
        parent. Each child's routing hook should mutate only its own copy.
        """
        parent = {
            "agents": {
                "coder": {
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-sonnet-4-6"}
                    ]
                }
            }
        }

        child1 = merge_configs(parent, {})
        child2 = merge_configs(parent, {})

        # Mutate child1's copy
        child1["agents"]["coder"]["provider_preferences"] = [
            {"provider": "openai", "model": "gpt-o3"}
        ]

        # child2 must be unaffected
        assert child2["agents"]["coder"]["provider_preferences"] == [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"}
        ], "Parallel child mutations must not affect sibling agent configs"

        # Parent must also be unaffected
        assert parent["agents"]["coder"]["provider_preferences"] == [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"}
        ], "Parallel child mutations must not affect parent agent configs"

    def test_merge_configs_no_agents_key_unaffected(self):
        """merge_configs must work normally when parent has no 'agents' key."""
        parent = {"session": {"orchestrator": "loop-basic"}}
        result = merge_configs(parent, {})
        # No crash, no agents key inserted
        assert "agents" not in result

    def test_agent_filter_list_still_isolated(self):
        """agent_filter=['coder'] selects a subset but parent must still be unmodified.

        This catches the bug where the list-filter branch re-reads from the
        original parent dict instead of the deep-copied result — voiding
        the deep-copy protection.
        """
        parent = {
            "agents": {
                "coder": {
                    "description": "Coding agent",
                    "model_role": "coding",
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-sonnet-4-6"}
                    ],
                },
                "reviewer": {"description": "Review agent"},
            }
        }
        overlay = {"agents": ["coder"]}
        result = merge_configs(parent, overlay)

        # Result should only have "coder" (filtered)
        assert "coder" in result["agents"]
        assert "reviewer" not in result["agents"]

        # Mutate the child's copy (simulating routing hook)
        result["agents"]["coder"]["provider_preferences"] = [
            {"provider": "openai", "model": "gpt-o3"}
        ]

        # Parent must be UNCHANGED — deep-copy protects even with list filter
        assert parent["agents"]["coder"]["provider_preferences"] == [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"}
        ], "List-filter branch must deep-copy: child mutation must not corrupt parent"

    def test_agent_filter_none_still_isolated(self):
        """agent_filter='none' empties agents but parent must still be unmodified."""
        parent = {
            "agents": {
                "coder": {
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-opus-4-6"}
                    ]
                }
            }
        }
        overlay = {"agents": "none"}
        result = merge_configs(parent, overlay)

        # Result has empty agents (filter applied)
        assert result["agents"] == {}

        # Parent unchanged
        assert "coder" in parent["agents"]
        assert parent["agents"]["coder"]["provider_preferences"] == [
            {"provider": "anthropic", "model": "claude-opus-4-6"}
        ]
