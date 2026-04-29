"""Tests for merge utilities and CLI policy functions."""

from pathlib import Path

from amplifier_app_cli.lib.merge_utils import _provider_key, merge_module_lists
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.runtime.config import _ensure_cwd_in_write_paths
from amplifier_app_cli.runtime.config import _ensure_default_skills_dirs


def _make_settings(tmp_path: Path) -> AppSettings:
    """Create AppSettings with isolated paths for testing."""
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
        session_settings=tmp_path / "session" / "settings.yaml",
    )
    return AppSettings(paths=paths)


def _write_providers_to_scope(
    settings: AppSettings, scope: str, providers: list
) -> None:
    """Helper: write a provider list into a scope's settings file."""
    scope_settings = settings._read_scope(scope)  # type: ignore[arg-type]
    scope_settings.setdefault("config", {})["providers"] = providers
    settings._write_scope(scope, scope_settings)  # type: ignore[arg-type]


class TestMergeModuleListsStringShorthand:
    """Tests for merge_module_lists() string-shorthand normalization.

    Agents may declare tools/hooks/providers using bare-string shorthand:
        tools: [terminal_inspector]
    instead of full dict form:
        tools: [{module: terminal_inspector}]

    Both forms must be accepted; strings are normalized to {key_field: <string>}.
    """

    def test_string_overlay_single_entry(self):
        """String-shorthand overlay entry is normalized to dict-form."""
        result = merge_module_lists([], ["bash"])
        assert result == [{"module": "bash"}]

    def test_string_overlay_multiple_entries(self):
        """Multiple string-shorthand overlay entries are all normalized."""
        result = merge_module_lists([], ["bash", "filesystem"])
        assert result == [{"module": "bash"}, {"module": "filesystem"}]

    def test_string_overlay_merges_into_existing_base(self):
        """String-shorthand overlay merges into matching dict-form base entry."""
        base = [{"module": "bash", "config": {"timeout": 30}}]
        result = merge_module_lists(base, ["bash"])
        # 'bash' string normalises to {"module": "bash"}, which matches the base entry
        assert len(result) == 1
        # base config preserved (string overlay carries no extra keys to clobber it)
        assert result[0]["config"]["timeout"] == 30

    def test_string_overlay_appends_new_entry(self):
        """String-shorthand overlay entry not in base is appended as dict-form."""
        base = [{"module": "bash", "config": {"timeout": 30}}]
        result = merge_module_lists(base, ["filesystem"])
        assert len(result) == 2
        modules = [r["module"] for r in result]
        assert "bash" in modules
        assert "filesystem" in modules

    def test_mixed_overlay_strings_and_dicts(self):
        """Overlay list may mix bare strings and full dicts; both are handled."""
        base: list[dict] = []
        overlay: list = [
            "bash",
            {"module": "filesystem", "config": {"allowed_write_paths": ["/tmp"]}},
        ]
        result = merge_module_lists(base, overlay)
        assert len(result) == 2
        bash_entry = next(r for r in result if r["module"] == "bash")
        assert bash_entry == {"module": "bash"}
        fs_entry = next(r for r in result if r["module"] == "filesystem")
        assert fs_entry["config"]["allowed_write_paths"] == ["/tmp"]

    def test_string_base_single_entry(self):
        """String-shorthand base entry is normalized to dict-form."""
        result = merge_module_lists(["bash"], [])
        assert result == [{"module": "bash"}]

    def test_string_base_multiple_entries(self):
        """Multiple string-shorthand base entries are all normalized."""
        result = merge_module_lists(["bash", "filesystem"], [])
        assert len(result) == 2
        modules = {r["module"] for r in result}
        assert modules == {"bash", "filesystem"}

    def test_mixed_base_strings_and_dicts(self):
        """Base list may mix bare strings and full dicts; both are handled."""
        base: list = [
            "bash",
            {"module": "filesystem", "config": {"allowed_write_paths": ["/tmp"]}},
        ]
        result = merge_module_lists(base, [])
        assert len(result) == 2
        bash_entry = next(r for r in result if r["module"] == "bash")
        assert bash_entry == {"module": "bash"}

    def test_string_base_merged_with_dict_overlay(self):
        """String base entry is normalized; dict overlay deep-merges into it."""
        base = ["bash"]
        overlay = [{"module": "bash", "config": {"timeout": 60}}]
        result = merge_module_lists(base, overlay)
        assert len(result) == 1
        assert result[0]["module"] == "bash"
        assert result[0]["config"]["timeout"] == 60

    def test_real_world_terminal_inspector(self):
        """Regression: tools: [terminal_inspector] in agent frontmatter does not crash."""
        # This is the exact value from reality-check/agents/terminal-tester.md
        base: list[dict] = []
        overlay = ["terminal_inspector"]
        result = merge_module_lists(base, overlay)
        assert result == [{"module": "terminal_inspector"}]

    def test_key_field_respected_for_string_normalization(self):
        """String shorthand respects key_field parameter (e.g., 'module' vs custom field)."""
        result = merge_module_lists([], ["my-hook"], key_field="module")
        assert result == [{"module": "my-hook"}]

    def test_empty_lists(self):
        """Empty base and overlay produce empty result without crash."""
        result = merge_module_lists([], [])
        assert result == []

    def test_garbage_entries_silently_dropped(self):
        """Non-string, non-dict entries (e.g., None, int) are silently dropped."""
        result = merge_module_lists([], [None, 42, "bash"])  # type: ignore[list-item]
        assert result == [{"module": "bash"}]


class TestMergeModuleLists:
    """Tests for merge_module_lists() multi-instance provider behavior."""

    def test_merge_preserves_multi_instance_providers(self):
        """Base with two entries sharing a module but different ids should both be kept."""
        base = [
            {"module": "provider-openai", "config": {"model": "gpt-5.2"}},
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"model": "gpt-5.4"},
            },
        ]
        overlay: list[dict] = []
        result = merge_module_lists(base, overlay)
        assert len(result) == 2

    def test_merge_updates_correct_instance_by_id(self):
        """Overlay entry with id should update only the matching base entry."""
        base = [
            {"module": "provider-openai", "config": {"model": "gpt-5.2"}},
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"model": "gpt-5.4"},
            },
        ]
        overlay = [
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"model": "gpt-5.5"},
            },
        ]
        result = merge_module_lists(base, overlay)
        assert len(result) == 2

        openai2 = next(r for r in result if r.get("id") == "openai-2")
        assert openai2["config"]["model"] == "gpt-5.5"

        unnamed = next(r for r in result if r.get("id") is None)
        assert unnamed["config"]["model"] == "gpt-5.2"

    def test_merge_standard_module_behavior_unchanged(self):
        """Regression: same-module entries (no id) should still merge normally."""
        base = [{"module": "tool-bash", "config": {"timeout": 30}}]
        overlay = [{"module": "tool-bash", "config": {"timeout": 60}}]
        result = merge_module_lists(base, overlay)
        assert len(result) == 1
        assert result[0]["config"]["timeout"] == 60


class TestProviderKey:
    """Tests for _provider_key() identity helper."""

    def test_provider_key_returns_id_when_present(self):
        """Should return 'id' when both 'id' and 'module' are present."""
        entry = {"module": "provider-openai", "id": "openai-2"}
        assert _provider_key(entry) == "openai-2"

    def test_provider_key_returns_module_when_no_id(self):
        """Should fall back to 'module' when 'id' is absent."""
        entry = {"module": "provider-anthropic"}
        assert _provider_key(entry) == "provider-anthropic"

    def test_provider_key_returns_module_when_id_is_none(self):
        """Should fall back to 'module' when 'id' is explicitly None."""
        entry = {"module": "provider-openai", "id": None}
        assert _provider_key(entry) == "provider-openai"

    def test_provider_key_returns_empty_string_for_empty_dict(self):
        """Should return empty string when dict has neither 'id' nor 'module'."""
        entry = {}
        assert _provider_key(entry) == ""

    def test_provider_key_prefers_id_over_module(self):
        """'id' should always win over 'module' when both are truthy."""
        entry = {"module": "provider-openai", "id": "my-custom-openai"}
        assert _provider_key(entry) == "my-custom-openai"


class TestEnsureCwdInWritePaths:
    """Tests for _ensure_cwd_in_write_paths CLI policy function."""

    def test_injects_cwd_when_missing(self):
        """CWD should be injected when not present in allowed_write_paths."""
        tools = [
            {
                "module": "tool-filesystem",
                "config": {"allowed_write_paths": ["/some/path", "/other/path"]},
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
                "config": {"allowed_write_paths": [".", "/some/path"]},
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
        tools = [{"module": "tool-filesystem", "config": {"allowed_write_paths": []}}]
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
                "config": {"allowed_write_paths": original_paths},
            }
        ]
        _ensure_cwd_in_write_paths(tools)
        # Original should be unchanged
        assert original_paths == ["/some/path"]


class TestEnsureDefaultSkillsDirs:
    """Tests for _ensure_default_skills_dirs CLI policy function."""

    def test_injects_default_paths_when_missing(self):
        """Default skill dirs should be appended when not present."""
        tools = [
            {
                "module": "tool-skills",
                "config": {"skills": ["git+https://example.com/skills"]},
            }
        ]
        result = _ensure_default_skills_dirs(tools)
        skills = result[0]["config"]["skills"]
        assert "git+https://example.com/skills" in skills
        assert ".amplifier/skills" in skills
        assert "~/.amplifier/skills" in skills

    def test_preserves_existing_paths(self):
        """Default paths should not be duplicated if already present."""
        tools = [
            {
                "module": "tool-skills",
                "config": {
                    "skills": [
                        "git+https://example.com/skills",
                        ".amplifier/skills",
                        "~/.amplifier/skills",
                    ]
                },
            }
        ]
        result = _ensure_default_skills_dirs(tools)
        skills = result[0]["config"]["skills"]
        assert skills.count(".amplifier/skills") == 1
        assert skills.count("~/.amplifier/skills") == 1

    def test_handles_empty_config(self):
        """Should handle tool-skills with no config."""
        tools = [{"module": "tool-skills"}]
        result = _ensure_default_skills_dirs(tools)
        skills = result[0]["config"]["skills"]
        assert ".amplifier/skills" in skills
        assert "~/.amplifier/skills" in skills

    def test_handles_empty_skills_list(self):
        """Should handle empty skills list."""
        tools = [{"module": "tool-skills", "config": {"skills": []}}]
        result = _ensure_default_skills_dirs(tools)
        skills = result[0]["config"]["skills"]
        assert ".amplifier/skills" in skills
        assert "~/.amplifier/skills" in skills

    def test_ignores_other_tools(self):
        """Should not modify tools that aren't tool-skills."""
        tools = [
            {"module": "tool-bash", "config": {"some_key": "value"}},
            {"module": "tool-skills", "config": {"skills": ["url1"]}},
        ]
        result = _ensure_default_skills_dirs(tools)
        assert result[0] == {"module": "tool-bash", "config": {"some_key": "value"}}
        assert ".amplifier/skills" in result[1]["config"]["skills"]

    def test_does_not_mutate_input(self):
        """Should not mutate the original tools list."""
        original_skills = ["git+https://example.com/skills"]
        tools = [
            {
                "module": "tool-skills",
                "config": {"skills": original_skills},
            }
        ]
        _ensure_default_skills_dirs(tools)
        assert original_skills == ["git+https://example.com/skills"]

    def test_default_paths_appended_after_configured(self):
        """Default paths should come after explicitly configured sources."""
        tools = [
            {
                "module": "tool-skills",
                "config": {"skills": ["url1", "url2"]},
            }
        ]
        result = _ensure_default_skills_dirs(tools)
        skills = result[0]["config"]["skills"]
        assert skills == ["url1", "url2", ".amplifier/skills", "~/.amplifier/skills"]


class TestProviderScopeMerge:
    """Tests for get_provider_overrides() scope-merge-by-key behavior."""

    def test_global_only_returns_all_providers(self, tmp_path: Path) -> None:
        """When only global scope has providers, all 3 should be returned."""
        settings = _make_settings(tmp_path)
        providers = [
            {"module": "provider-openai", "config": {"default_model": "gpt-4o"}},
            {
                "module": "provider-anthropic",
                "config": {"default_model": "claude-3-5-sonnet"},
            },
            {"module": "provider-azure", "config": {"default_model": "gpt-4"}},
        ]
        _write_providers_to_scope(settings, "global", providers)

        result = settings.get_provider_overrides()

        assert len(result) == 3
        modules = [p["module"] for p in result]
        assert "provider-openai" in modules
        assert "provider-anthropic" in modules
        assert "provider-azure" in modules

    def test_local_override_merges_not_replaces(self, tmp_path: Path) -> None:
        """Local scope with 1 matching provider should merge, not replace the global 3."""
        settings = _make_settings(tmp_path)
        global_providers = [
            {
                "module": "provider-openai",
                "config": {"default_model": "gpt-4o", "source": "openai-direct"},
            },
            {
                "module": "provider-anthropic",
                "config": {"default_model": "claude-3-5-sonnet"},
            },
            {"module": "provider-azure", "config": {"default_model": "gpt-4"}},
        ]
        local_providers = [
            {
                "module": "provider-openai",
                "config": {"default_model": "gpt-4o-mini"},
            },
        ]
        _write_providers_to_scope(settings, "global", global_providers)
        _write_providers_to_scope(settings, "local", local_providers)

        result = settings.get_provider_overrides()

        # All 3 providers should still be present
        assert len(result) == 3

        # Find the openai entry
        openai_entry = next(p for p in result if p["module"] == "provider-openai")
        # Local's default_model wins
        assert openai_entry["config"]["default_model"] == "gpt-4o-mini"
        # Global's source field is retained
        assert openai_entry["config"]["source"] == "openai-direct"

    def test_multi_instance_preserved_across_scopes(self, tmp_path: Path) -> None:
        """Two providers with same module but different ids should be treated independently."""
        settings = _make_settings(tmp_path)
        global_providers = [
            {
                "module": "provider-openai",
                "config": {"default_model": "gpt-4o", "source": "primary"},
            },
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"default_model": "gpt-4o", "source": "secondary"},
            },
        ]
        local_providers = [
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"default_model": "gpt-4-turbo"},
            },
        ]
        _write_providers_to_scope(settings, "global", global_providers)
        _write_providers_to_scope(settings, "local", local_providers)

        result = settings.get_provider_overrides()

        # Both entries should be present
        assert len(result) == 2

        # Find openai-2 (by id)
        openai2 = next(p for p in result if p.get("id") == "openai-2")
        assert openai2["config"]["default_model"] == "gpt-4-turbo"

        # Primary (no id) should be unchanged
        primary = next(p for p in result if p.get("id") is None)
        assert primary["config"]["default_model"] == "gpt-4o"
        assert primary["config"]["source"] == "primary"

    def test_local_new_provider_appended(self, tmp_path: Path) -> None:
        """A new provider in local scope (not in global) should be appended."""
        settings = _make_settings(tmp_path)
        global_providers = [
            {"module": "provider-openai", "config": {"default_model": "gpt-4o"}},
            {
                "module": "provider-anthropic",
                "config": {"default_model": "claude-3-5-sonnet"},
            },
        ]
        local_providers = [
            {"module": "provider-ollama", "config": {"default_model": "llama3.2"}},
        ]
        _write_providers_to_scope(settings, "global", global_providers)
        _write_providers_to_scope(settings, "local", local_providers)

        result = settings.get_provider_overrides()

        assert len(result) == 3
        modules = [p["module"] for p in result]
        assert "provider-openai" in modules
        assert "provider-anthropic" in modules
        assert "provider-ollama" in modules

    def test_project_and_local_both_applied(self, tmp_path: Path) -> None:
        """Global, project, and local scopes should all contribute with correct priority."""
        settings = _make_settings(tmp_path)
        global_providers = [
            {
                "module": "provider-openai",
                "config": {"default_model": "gpt-4o", "source": "global"},
            },
            {
                "module": "provider-anthropic",
                "config": {"default_model": "claude-3-5-sonnet"},
            },
        ]
        project_providers = [
            {
                "module": "provider-openai",
                "config": {"default_model": "gpt-4o-mini", "source": "project"},
            },
            {
                "module": "provider-gemini",
                "config": {"default_model": "gemini-1.5-pro"},
            },
        ]
        local_providers = [
            {
                "module": "provider-openai",
                "config": {"default_model": "o1"},
            },
        ]
        _write_providers_to_scope(settings, "global", global_providers)
        _write_providers_to_scope(settings, "project", project_providers)
        _write_providers_to_scope(settings, "local", local_providers)

        result = settings.get_provider_overrides()

        # global(2) + project adds gemini(1) = 3 total
        assert len(result) == 3

        # openai: local wins for default_model, project wins for source
        openai_entry = next(p for p in result if p["module"] == "provider-openai")
        assert openai_entry["config"]["default_model"] == "o1"
        assert openai_entry["config"]["source"] == "project"

        # anthropic from global is still present
        anthropic = next(p for p in result if p["module"] == "provider-anthropic")
        assert anthropic["config"]["default_model"] == "claude-3-5-sonnet"

        # gemini added by project is present
        gemini = next(p for p in result if p["module"] == "provider-gemini")
        assert gemini["config"]["default_model"] == "gemini-1.5-pro"


class TestRuntimeConfigMerge:
    """Tests for _merge_module_lists() and _apply_provider_overrides() in runtime/config.py."""

    def test_runtime_merge_module_lists_preserves_multi_instance(self) -> None:
        """Base list with two entries sharing same module but different ids must both survive."""
        from amplifier_app_cli.runtime.config import _merge_module_lists

        base = [
            {"module": "provider-openai", "config": {"model": "gpt-5.2"}},
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"model": "gpt-5.4"},
            },
        ]
        result = _merge_module_lists(base, [])

        assert len(result) == 2

    def test_runtime_merge_module_lists_updates_by_id(self) -> None:
        """Overlay entry keyed by id must update matching base entry, not overwrite unrelated one."""
        from amplifier_app_cli.runtime.config import _merge_module_lists

        base = [
            {"module": "provider-openai", "config": {"model": "gpt-5.2"}},
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"model": "gpt-5.4"},
            },
        ]
        overlay = [
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"model": "gpt-5.5"},
            },
        ]
        result = _merge_module_lists(base, overlay)

        assert len(result) == 2
        openai2 = next(r for r in result if r.get("id") == "openai-2")
        assert openai2["config"]["model"] == "gpt-5.5"

    def test_apply_provider_overrides_preserves_multi_instance(self) -> None:
        """Both override entries (same module, different ids) must be in override_map independently."""
        from amplifier_app_cli.runtime.config import _apply_provider_overrides

        providers = [
            {"module": "provider-openai", "config": {"model": "base"}},
        ]
        overrides = [
            {"module": "provider-openai", "config": {"model": "gpt-5.2"}},
            {
                "module": "provider-openai",
                "id": "openai-2",
                "config": {"model": "gpt-5.4"},
            },
        ]
        # _apply_provider_overrides only merges into existing bundle providers.
        # The unnamed override matches the single bundle provider; openai-2 has no bundle match.
        # The key assertion: the unnamed provider's config is updated (not silently overwritten
        # by the openai-2 override because they shared the same map key before the fix).
        result = _apply_provider_overrides(providers, overrides)

        assert len(result) == 1
        assert result[0]["config"]["model"] == "gpt-5.2"


class TestSettingsIdToInstanceId:
    """Tests for settings 'id' → mount plan 'instance_id' mapping."""

    def test_settings_id_becomes_mount_plan_instance_id(self) -> None:
        """Settings entry with 'id' should have instance_id set in the mount plan."""
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        providers = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "config": {"default_model": "claude-sonnet-4-6", "priority": 1},
            }
        ]
        result = _map_id_to_instance_id(providers)
        assert result[0]["instance_id"] == "anthropic-sonnet"

    def test_settings_no_id_no_instance_id(self) -> None:
        """Settings entry without 'id' should NOT get instance_id (backward compat)."""
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        providers = [
            {
                "module": "provider-anthropic",
                "config": {"default_model": "claude-3-5-sonnet"},
            }
        ]
        result = _map_id_to_instance_id(providers)
        assert "instance_id" not in result[0]

    def test_multi_instance_settings_both_get_instance_id(self) -> None:
        """Two settings entries for same module with different ids both get instance_id."""
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        providers = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "config": {"default_model": "claude-sonnet-4-6", "priority": 1},
            },
            {
                "module": "provider-anthropic",
                "id": "anthropic-haiku",
                "config": {"default_model": "claude-haiku-3-5", "priority": 2},
            },
        ]
        result = _map_id_to_instance_id(providers)
        assert result[0]["instance_id"] == "anthropic-sonnet"
        assert result[1]["instance_id"] == "anthropic-haiku"

    def test_existing_instance_id_not_overwritten(self) -> None:
        """If instance_id already present, it should NOT be overwritten by id."""
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        providers = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "instance_id": "already-set",
                "config": {},
            }
        ]
        result = _map_id_to_instance_id(providers)
        assert result[0]["instance_id"] == "already-set"

    def test_does_not_mutate_input(self) -> None:
        """Should return new dicts, not mutate the originals."""
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        original = {
            "module": "provider-anthropic",
            "id": "anthropic-sonnet",
            "config": {},
        }
        providers = [original]
        _map_id_to_instance_id(providers)
        assert "instance_id" not in original

    def test_single_instance_no_auto_assign(self) -> None:
        """Single provider with no 'id' should NOT get auto-assigned instance_id.

        Backward compat: single-instance providers don't need instance_id.
        Auto-assign only triggers when multiple entries share the same module.
        """
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        providers = [
            {
                "module": "provider-anthropic",
                "config": {"default_model": "claude-3-5-sonnet"},
            }
        ]
        result = _map_id_to_instance_id(providers)
        assert "instance_id" not in result[0]

    def test_both_have_id_no_auto_assign(self) -> None:
        """Two providers both with 'id' get instance_id from their id — no auto-assign."""
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        providers = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-opus",
                "config": {"default_model": "claude-opus-4-6", "priority": 1},
            },
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "config": {"default_model": "claude-sonnet-4-6", "priority": 2},
            },
        ]
        result = _map_id_to_instance_id(providers)
        assert result[0]["instance_id"] == "anthropic-opus"
        assert result[1]["instance_id"] == "anthropic-sonnet"

    def test_default_entry_no_id_not_auto_assigned_instance_id(self) -> None:
        """Entry without 'id' should NOT get auto-assigned instance_id even in multi-instance.

        The original entry (no 'id') is the "default" instance — it mounts under the
        provider's default name and needs no instance_id. Only the explicitly-named
        second entry should get instance_id from its 'id' field.

        This is a regression test for PR #134's over-eager auto-assign that causes
        the snapshot overwrite bug: when instance_id == default_name, the kernel
        skips remapping and the second mount silently overwrites the first instance.
        """
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        providers = [
            {
                "module": "provider-anthropic",
                # No 'id' — original entry, should stay as the "default" instance
                "config": {"default_model": "claude-opus-4-6", "priority": 2},
            },
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "config": {"default_model": "claude-sonnet-4-6", "priority": 6},
            },
        ]
        result = _map_id_to_instance_id(providers)
        # First entry: NO instance_id — it's the default, the kernel mounts it as "anthropic"
        assert "instance_id" not in result[0], (
            f"Default entry should NOT have instance_id, got: {result[0].get('instance_id')}"
        )
        # Second entry: gets instance_id from its explicit 'id'
        assert result[1]["instance_id"] == "anthropic-sonnet"
