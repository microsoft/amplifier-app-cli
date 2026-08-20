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


@pytest.fixture(autouse=True)
def _entry_points_resolve_by_default():
    """Treat every mocked entry point as a module that actually resolves.

    `detect_provider_from_env()` no longer trusts a bare entry-point name --
    it confirms the entry point's module still imports, because a stranded
    `.dist-info` can advertise a provider whose files are gone. That check
    consults the real interpreter, which would otherwise make every mocked
    provider in this file look uninstalled regardless of the entry-point list
    each test sets up.

    Patching it True by default preserves each test's intent: the mocked
    entry-point list IS the set of installed providers. Tests that care about
    the stranded case patch this again locally, and the inner patch wins.
    """
    with patch(
        "amplifier_app_cli.provider_env_detect.is_provider_module_installed",
        return_value=True,
    ):
        yield


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

    def test_stranded_entry_point_is_treated_as_missing(self, monkeypatch):
        """An entry point that exists but whose module no longer imports must
        count as missing, not installed.

        Providers are installed editable, so removing the module cache while
        leaving site-packages intact strands the `.dist-info` -- the provider
        still advertises an entry point pointing at a directory that is gone.
        Reading entry-point names alone would select this provider and fail
        later at import time with an error that never mentions credentials.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with (
            patch(
                "amplifier_app_cli.provider_env_detect.entry_points",
                # The entry point IS registered -- this is the stranded case.
                return_value=_mock_entry_points(
                    ["provider-anthropic", "provider-ollama"]
                ),
            ),
            patch(
                "amplifier_app_cli.provider_env_detect.is_provider_module_installed",
                # ...but its module does not resolve.
                side_effect=lambda name: name != "provider-anthropic",
            ),
            pytest.raises(CredentialedProviderModuleMissingError) as excinfo,
        ):
            detect_provider_from_env()

        assert excinfo.value.provider_id == "provider-anthropic"

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


class TestAmbientCredentialsDoNotBlockFallback:
    """GITHUB_TOKEN is injected by the platform, not chosen by the user, so
    it must not escalate a missing module into a hard failure.

    GitHub Actions sets GITHUB_TOKEN in every job automatically. Combined
    with the non-TTY environment that triggers auto-init, that satisfies
    every GAP-003 raise condition by default in any workflow that doesn't
    happen to have provider-github-copilot installed -- which is most of
    them. Without the carve-out, a fix aimed at protecting a user's
    deliberately-set API key instead breaks CI runs nobody touched, and
    tells them to install a provider they never asked for.

    Note these tests deliberately set GITHUB_TOKEN *after* clearing the
    environment. The shared `_clear_all_provider_env_vars()` helper strips
    every var in PROVIDER_CREDENTIAL_VARS -- GITHUB_TOKEN included -- so a
    test relying on it alone can never observe this behavior, which is
    exactly why the regression went unnoticed.
    """

    def test_github_token_alone_still_falls_back_to_ollama(self, monkeypatch):
        """The CI shape: ambient GITHUB_TOKEN, Copilot module absent,
        Ollama installed. Must select Ollama quietly rather than raise."""
        _clear_all_provider_env_vars(monkeypatch)
        monkeypatch.setenv("GITHUB_TOKEN", "ghs-ambient-ci-token")
        with patch(
            "amplifier_app_cli.provider_env_detect.entry_points",
            # provider-github-copilot deliberately absent.
            return_value=_mock_entry_points(["provider-ollama"]),
        ):
            assert detect_provider_from_env() == "provider-ollama"

    def test_github_token_alone_returns_none_without_ollama(self, monkeypatch):
        """Same ambient token, nothing installed at all. Must return None --
        the pre-existing 'nothing configured' path -- not raise."""
        _clear_all_provider_env_vars(monkeypatch)
        monkeypatch.setenv("GITHUB_TOKEN", "ghs-ambient-ci-token")
        with patch(
            "amplifier_app_cli.provider_env_detect.entry_points",
            return_value=_mock_entry_points([]),
        ):
            assert detect_provider_from_env() is None

    def test_github_copilot_still_selected_when_module_installed(self, monkeypatch):
        """The carve-out must not disable the provider. When the module IS
        installed, a GITHUB_TOKEN-credentialed Copilot is still selectable."""
        _clear_all_provider_env_vars(monkeypatch)
        monkeypatch.setenv("GITHUB_TOKEN", "ghs-ambient-ci-token")
        with patch(
            "amplifier_app_cli.provider_env_detect.entry_points",
            return_value=_mock_entry_points(
                ["provider-github-copilot", "provider-ollama"]
            ),
        ):
            assert detect_provider_from_env() == "provider-github-copilot"

    def test_user_set_credential_still_raises_alongside_ambient_token(
        self, monkeypatch
    ):
        """The carve-out is scoped to the ambient var only. A deliberately-set
        ANTHROPIC_API_KEY must still raise even when GITHUB_TOKEN is also
        present -- otherwise the CI fix would silently undo GAP-003."""
        _clear_all_provider_env_vars(monkeypatch)
        monkeypatch.setenv("GITHUB_TOKEN", "ghs-ambient-ci-token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
        with (
            patch(
                "amplifier_app_cli.provider_env_detect.entry_points",
                return_value=_mock_entry_points(["provider-ollama"]),
            ),
            pytest.raises(CredentialedProviderModuleMissingError) as excinfo,
        ):
            detect_provider_from_env()

        assert excinfo.value.provider_id == "provider-anthropic"
