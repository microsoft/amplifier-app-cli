"""Tests for amplifier_app_cli.provider_env_detect.

GAP-003: `detect_provider_from_env()` must distinguish two very different
situations that were previously handled identically:

1. A provider has NO credentials in the environment at all -- silence is
   correct, fall through to the next candidate (and eventually Ollama).
2. A provider DOES have credentials in the environment, but its module is
   not installed/importable -- this must be loud
   (`CredentialedProviderModuleMissingError`), not a silent fall-through
   to a different, unrequested provider.

These tests exercise `detect_provider_from_env()` directly (not mocked),
with `entry_points` patched to control which provider modules appear
"installed", so the real priority-loop logic is under test.
"""

from unittest.mock import MagicMock, patch

import pytest
from amplifier_app_cli.provider_env_detect import (
    PROVIDER_CREDENTIAL_VARS,
    CredentialedProviderModuleMissingError,
    detect_provider_from_env,
)


def _mock_entry_points(names: list[str]):
    """Build a fake entry_points() return value with the given module names."""
    eps = []
    for name in names:
        ep = MagicMock()
        ep.name = name
        eps.append(ep)
    return eps


def _clear_all_provider_env_vars(monkeypatch):
    """Strip every credential env var this module knows about, so tests are
    isolated from whatever happens to be set in the ambient environment
    (e.g. a real GITHUB_TOKEN or ANTHROPIC_API_KEY on the machine running
    the suite)."""
    for env_vars in PROVIDER_CREDENTIAL_VARS.values():
        for var in env_vars:
            monkeypatch.delenv(var, raising=False)


class TestDetectProviderFromEnvNoCredentials:
    """The genuinely-no-cloud-credentials case must stay quiet and correct."""

    def test_no_env_vars_no_installed_providers_returns_none(self, monkeypatch):
        _clear_all_provider_env_vars(monkeypatch)
        with patch(
            "amplifier_app_cli.provider_env_detect.entry_points",
            return_value=_mock_entry_points([]),
        ):
            assert detect_provider_from_env() is None

    def test_no_credentials_falls_through_to_ollama(self, monkeypatch):
        """No cloud credentials set, but provider-ollama IS installed ->
        quietly select Ollama. This is the legitimate case the fix must
        not disturb."""
        _clear_all_provider_env_vars(monkeypatch)
        with patch(
            "amplifier_app_cli.provider_env_detect.entry_points",
            return_value=_mock_entry_points(["provider-ollama"]),
        ):
            assert detect_provider_from_env() == "provider-ollama"


class TestDetectProviderFromEnvCredentialedAndInstalled:
    """The normal, working case: credentials present, module installed."""

    def test_anthropic_credentials_and_module_installed(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        with patch(
            "amplifier_app_cli.provider_env_detect.entry_points",
            return_value=_mock_entry_points(["provider-anthropic", "provider-ollama"]),
        ):
            assert detect_provider_from_env() == "provider-anthropic"


class TestDetectProviderFromEnvCredentialedButModuleMissing:
    """GAP-003: the fixed behavior. Credentials present, module NOT
    installed -- must raise loudly, never silently pick Ollama."""

    def test_raises_instead_of_falling_back_to_ollama(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        with patch(
            "amplifier_app_cli.provider_env_detect.entry_points",
            # provider-anthropic is NOT in this list -- module missing.
            # provider-ollama IS installed -- this is exactly the shape
            # that used to silently produce "provider-ollama".
            return_value=_mock_entry_points(["provider-ollama"]),
        ):
            with pytest.raises(CredentialedProviderModuleMissingError) as excinfo:
                detect_provider_from_env()

            assert excinfo.value.provider_id == "provider-anthropic"
            assert "ANTHROPIC_API_KEY" in str(excinfo.value)
            assert "provider-anthropic" in str(excinfo.value)

    def test_raises_even_when_ollama_not_installed_either(self, monkeypatch):
        """Same defect, no Ollama fallback available at all (would have
        previously returned None with no explanation)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        with (
            patch(
                "amplifier_app_cli.provider_env_detect.entry_points",
                return_value=_mock_entry_points([]),
            ),
            pytest.raises(CredentialedProviderModuleMissingError),
        ):
            detect_provider_from_env()

    def test_falls_through_to_second_credentialed_installed_provider(self, monkeypatch):
        """If a higher-priority provider's module is missing but a
        lower-priority provider is both credentialed AND installed, that
        lower-priority provider should still be selected -- the missing
        higher-priority one is recorded but doesn't block a real,
        installed, working alternative."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        monkeypatch.setenv("OPENAI_API_KEY", "dummy-not-a-real-key")
        with patch(
            "amplifier_app_cli.provider_env_detect.entry_points",
            # anthropic (higher priority) missing; openai (lower priority)
            # installed and credentialed.
            return_value=_mock_entry_points(["provider-openai"]),
        ):
            assert detect_provider_from_env() == "provider-openai"

    def test_does_not_reach_ollama_when_credentialed_provider_missing(
        self, monkeypatch
    ):
        """Decisive regression guard for the exact GAP-003 symptom: with
        ANTHROPIC_API_KEY set and provider-anthropic's module missing,
        the function must never return "provider-ollama" even though
        Ollama's module is installed."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with (
            patch(
                "amplifier_app_cli.provider_env_detect.entry_points",
                return_value=_mock_entry_points(["provider-ollama"]),
            ),
            pytest.raises(CredentialedProviderModuleMissingError),
        ):
            result = detect_provider_from_env()
            # Should never get here, but if the exception handling
            # regresses, fail loudly on the actual returned value too.
            assert result != "provider-ollama"
