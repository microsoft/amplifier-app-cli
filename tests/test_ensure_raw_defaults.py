"""Tests for _ensure_raw_defaults() in runtime/config.py.

Verifies the function that injects the ``raw`` flag into provider configs
and strips stale ``debug``/``raw_debug`` keys from the old 3-tier verbosity
system (CP-V collapse).
"""

from amplifier_app_cli.runtime.config import _ensure_raw_defaults


class TestEnsureRawDefaults:
    """Unit tests for _ensure_raw_defaults()."""

    # ------------------------------------------------------------------
    # Core behaviour: raw injection
    # ------------------------------------------------------------------

    def test_injects_raw_true_when_not_present(self):
        """Provider config without raw gets raw: True by default."""
        providers = [
            {"module": "provider-anthropic", "config": {"api_key": "sk-test"}},
        ]
        result = _ensure_raw_defaults(providers)
        assert result[0]["config"]["raw"] is True

    def test_respects_explicit_raw_false(self):
        """Explicit raw: False is NOT overridden."""
        providers = [
            {"module": "provider-anthropic", "config": {"api_key": "sk-test", "raw": False}},
        ]
        result = _ensure_raw_defaults(providers)
        assert result[0]["config"]["raw"] is False

    def test_respects_explicit_raw_true(self):
        """Explicit raw: True is preserved unchanged."""
        providers = [
            {"module": "provider-anthropic", "config": {"api_key": "sk-test", "raw": True}},
        ]
        result = _ensure_raw_defaults(providers)
        assert result[0]["config"]["raw"] is True

    # ------------------------------------------------------------------
    # Stale flag removal
    # ------------------------------------------------------------------

    def test_removes_debug_flag(self):
        """``debug`` key from the old 3-tier system is stripped unconditionally."""
        providers = [
            {"module": "provider-anthropic", "config": {"debug": True}},
        ]
        result = _ensure_raw_defaults(providers)
        assert "debug" not in result[0]["config"]

    def test_removes_raw_debug_flag(self):
        """``raw_debug`` key from the old 3-tier system is stripped unconditionally."""
        providers = [
            {"module": "provider-anthropic", "config": {"raw_debug": True}},
        ]
        result = _ensure_raw_defaults(providers)
        assert "raw_debug" not in result[0]["config"]

    def test_removes_both_stale_flags_and_injects_raw(self):
        """Realistic user settings.yaml config: both stale flags removed, raw injected."""
        providers = [
            {
                "module": "provider-anthropic",
                "config": {
                    "api_key": "sk-ant-secret",
                    "base_url": "https://api.anthropic.com",
                    "default_model": "claude-opus-4-6",
                    "debug": True,
                    "raw_debug": True,
                    "priority": 1,
                },
            }
        ]
        result = _ensure_raw_defaults(providers)
        cfg = result[0]["config"]
        # Stale flags gone
        assert "debug" not in cfg
        assert "raw_debug" not in cfg
        # New flag injected
        assert cfg["raw"] is True
        # Unrelated keys preserved
        assert cfg["api_key"] == "sk-ant-secret"
        assert cfg["base_url"] == "https://api.anthropic.com"
        assert cfg["priority"] == 1

    def test_removes_debug_false_and_injects_raw(self):
        """debug: False is also stripped (the key itself is stale, value irrelevant)."""
        providers = [
            {"module": "provider-openai", "config": {"debug": False, "raw_debug": False}},
        ]
        result = _ensure_raw_defaults(providers)
        cfg = result[0]["config"]
        assert "debug" not in cfg
        assert "raw_debug" not in cfg
        assert cfg["raw"] is True

    # ------------------------------------------------------------------
    # Multiple providers
    # ------------------------------------------------------------------

    def test_processes_multiple_providers(self):
        """All providers in the list are processed, not just the first."""
        providers = [
            {"module": "provider-anthropic", "config": {"debug": True, "raw_debug": True}},
            {"module": "provider-openai", "config": {"debug": True}},
            {"module": "provider-gemini", "config": {}},
        ]
        result = _ensure_raw_defaults(providers)
        for entry in result:
            cfg = entry["config"]
            assert "debug" not in cfg, f"{entry['module']} still has debug"
            assert "raw_debug" not in cfg, f"{entry['module']} still has raw_debug"
            assert cfg.get("raw") is True, f"{entry['module']} missing raw: True"

    # ------------------------------------------------------------------
    # Non-dict providers (passthrough safety)
    # ------------------------------------------------------------------

    def test_non_dict_provider_passed_through_unchanged(self):
        """Non-dict entries (shouldn't exist but defensive) are passed through as-is."""
        providers = ["unexpected-string-entry"]
        result = _ensure_raw_defaults(providers)
        assert result == ["unexpected-string-entry"]

    # ------------------------------------------------------------------
    # Empty inputs
    # ------------------------------------------------------------------

    def test_empty_list_returns_empty_list(self):
        """Empty provider list returns an empty list."""
        assert _ensure_raw_defaults([]) == []

    def test_provider_with_no_config_key_gets_config_with_raw(self):
        """Provider dict with no ``config`` key gets one added with raw: True."""
        providers = [{"module": "provider-anthropic"}]
        result = _ensure_raw_defaults(providers)
        # No config key in original — function should skip (only processes if config present)
        # The function only modifies config if isinstance(config, dict), and {} is falsy
        # but config.copy() on {} still works — let's verify what actually happens:
        cfg = result[0].get("config", {})
        # config defaults to {} in the function, so raw gets injected into an empty dict
        assert cfg.get("raw") is True

    # ------------------------------------------------------------------
    # Immutability: original not mutated
    # ------------------------------------------------------------------

    def test_does_not_mutate_original_provider_list(self):
        """Original provider dicts are not mutated — copies are returned."""
        original_config = {"debug": True, "raw_debug": True, "api_key": "sk-test"}
        providers = [{"module": "provider-anthropic", "config": original_config}]
        _ensure_raw_defaults(providers)
        # Original config dict should be unchanged
        assert "debug" in original_config
        assert "raw_debug" in original_config
        assert "raw" not in original_config
