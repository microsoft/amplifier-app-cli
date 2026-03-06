"""Tests for merge utilities and CLI policy functions."""

from amplifier_app_cli.lib.merge_utils import _provider_key
from amplifier_app_cli.runtime.config import _ensure_cwd_in_write_paths


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
