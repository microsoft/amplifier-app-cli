"""Tests for merge utilities and CLI policy functions."""

import pytest
from amplifier_app_cli.runtime.config import _ensure_cwd_in_write_paths


class TestEnsureCwdInWritePaths:
    """Tests for _ensure_cwd_in_write_paths CLI policy function."""

    def test_injects_cwd_when_missing(self):
        """CWD should be injected when not present in allowed_write_paths."""
        tools = [
            {
                "module": "tool-filesystem",
                "config": {
                    "allowed_write_paths": ["/some/path", "/other/path"]
                }
            }
        ]
        result = _ensure_cwd_in_write_paths(tools)
        assert result[0]["config"]["allowed_write_paths"][0] == "."
        assert "/some/path" in result[0]["config"]["allowed_write_paths"]
        assert "/other/path" in result[0]["config"]["allowed_write_paths"]

    def test_preserves_cwd_when_present(self):
        """CWD should not be duplicated if already present."""
        tools = [
            {
                "module": "tool-filesystem",
                "config": {
                    "allowed_write_paths": [".", "/some/path"]
                }
            }
        ]
        result = _ensure_cwd_in_write_paths(tools)
        paths = result[0]["config"]["allowed_write_paths"]
        assert paths.count(".") == 1

    def test_handles_empty_config(self):
        """Should handle tool-filesystem with no config."""
        tools = [{"module": "tool-filesystem"}]
        result = _ensure_cwd_in_write_paths(tools)
        assert result[0]["config"]["allowed_write_paths"] == ["."]

    def test_handles_empty_allowed_write_paths(self):
        """Should handle empty allowed_write_paths list."""
        tools = [
            {
                "module": "tool-filesystem",
                "config": {"allowed_write_paths": []}
            }
        ]
        result = _ensure_cwd_in_write_paths(tools)
        assert result[0]["config"]["allowed_write_paths"] == ["."]

    def test_ignores_other_tools(self):
        """Should not modify tools that aren't tool-filesystem."""
        tools = [
            {"module": "tool-bash", "config": {"some_key": "value"}},
            {"module": "tool-filesystem", "config": {"allowed_write_paths": ["/path"]}},
        ]
        result = _ensure_cwd_in_write_paths(tools)
        # tool-bash unchanged
        assert result[0] == {"module": "tool-bash", "config": {"some_key": "value"}}
        # tool-filesystem has cwd injected
        assert "." in result[1]["config"]["allowed_write_paths"]

    def test_does_not_mutate_input(self):
        """Should not mutate the original tools list."""
        original_paths = ["/some/path"]
        tools = [
            {
                "module": "tool-filesystem",
                "config": {"allowed_write_paths": original_paths}
            }
        ]
        _ensure_cwd_in_write_paths(tools)
        # Original should be unchanged
        assert original_paths == ["/some/path"]


class TestMergeModuleListsStringItems:
    """Tests for merge_module_lists handling of string items in overlay.

    Agent YAML frontmatter may express tool intent as bare strings:
        tools:
          - generate_image
          - load_skill

    These are not module specs and must not crash the merge; parent modules
    should be inherited unchanged.
    """

    def test_string_overlay_inherits_parent_tools(self):
        """String items in overlay are skipped; parent tools are kept."""
        from amplifier_app_cli.lib.merge_utils import merge_module_lists

        parent_tools = [
            {"module": "tool-comic-image-gen", "source": "git+https://example.com"},
            {"module": "tool-skills", "source": "git+https://example.com"},
            {"module": "tool-filesystem", "config": {}},
        ]
        # Agent lists tools as shorthand strings
        agent_tools = ["generate_image", "load_skill"]

        result = merge_module_lists(parent_tools, agent_tools)

        # All parent tools must be preserved
        module_ids = [t["module"] for t in result]
        assert "tool-comic-image-gen" in module_ids
        assert "tool-skills" in module_ids
        assert "tool-filesystem" in module_ids
        # No spurious string-keyed entries
        assert all(isinstance(t, dict) for t in result)

    def test_mixed_overlay_handles_strings_and_dicts(self):
        """Mixed overlay (some dicts, some strings) merges dicts and skips strings."""
        from amplifier_app_cli.lib.merge_utils import merge_module_lists

        parent_tools = [
            {"module": "tool-filesystem", "config": {"allowed_write_paths": ["/old"]}},
        ]
        overlay = [
            "some-string-name",  # string item — must be skipped
            {"module": "tool-filesystem", "config": {"allowed_write_paths": ["/new"]}},  # dict — merged
        ]

        result = merge_module_lists(parent_tools, overlay)
        assert len(result) == 1
        assert result[0]["config"]["allowed_write_paths"] == ["/new"]

    def test_empty_string_overlay_inherits_parent(self):
        """All-string overlay keeps parent tools intact."""
        from amplifier_app_cli.lib.merge_utils import merge_module_lists

        parent_tools = [{"module": "tool-bash", "config": {}}]
        result = merge_module_lists(parent_tools, ["tool-bash"])
        assert result == [{"module": "tool-bash", "config": {}}]

    def test_no_crash_on_all_string_overlay(self):
        """Pure string overlay must not raise AttributeError."""
        from amplifier_app_cli.lib.merge_utils import merge_module_lists

        # Should not raise
        result = merge_module_lists([], ["generate_image", "load_skill"])
        assert result == []


class TestMergeAgentDictsStringTools:
    """Tests for merge_agent_dicts with agent configs that have string tool lists."""

    def test_merge_configs_does_not_crash_for_string_tools(self):
        """merge_configs must not crash when agent has string tool names."""
        from amplifier_app_cli.agent_config import merge_configs

        parent_config = {
            "providers": [
                {"module": "provider-anthropic", "config": {"api_key": "test"}},
            ],
            "tools": [
                {"module": "tool-comic-image-gen", "source": "git+https://example.com"},
                {"module": "tool-skills", "source": "git+https://example.com"},
            ],
            "session": {"orchestrator": {"module": "loop-streaming"}},
            "agents": {},
        }
        agent_config = {
            "tools": ["generate_image", "load_skill"],
            "provider_preferences": [{"provider": "anthropic", "model": "claude-sonnet-*"}],
        }

        # Must not raise
        result = merge_configs(parent_config, agent_config)

        # Providers must be inherited from parent
        assert result["providers"] == parent_config["providers"]

        # Parent tools must be inherited (string items in overlay are skipped)
        module_ids = [t["module"] for t in result["tools"]]
        assert "tool-comic-image-gen" in module_ids
        assert "tool-skills" in module_ids

    def test_providers_survive_string_tool_agent_spawn(self):
        """Provider list must reach child config when agent has string tool names."""
        from amplifier_app_cli.agent_config import merge_configs

        providers = [
            {"module": "provider-anthropic", "config": {"api_key": "test-key"}},
            {"module": "provider-openai", "config": {"api_key": "test-key"}},
        ]
        parent_config = {
            "providers": providers,
            "tools": [{"module": "tool-comic-image-gen", "source": "git+https://example.com"}],
            "session": {"orchestrator": {"module": "loop-streaming"}},
            "agents": {},
        }
        # Comic agent with string tool list (panel-artist / character-designer pattern)
        agent_config = {
            "tools": ["generate_image"],
            "provider_preferences": [{"provider": "anthropic", "model": "claude-sonnet-*"}],
        }

        result = merge_configs(parent_config, agent_config)

        # Providers must survive the merge intact
        assert result["providers"] == providers
        # generate_image backend will be discovered from inherited providers
        assert "provider-anthropic" in [p["module"] for p in result["providers"]]
