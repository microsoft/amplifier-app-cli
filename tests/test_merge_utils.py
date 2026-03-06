"""Tests for merge utilities and CLI policy functions."""

from pathlib import Path

from amplifier_app_cli.lib.merge_utils import _provider_key
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.runtime.config import _ensure_cwd_in_write_paths


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
