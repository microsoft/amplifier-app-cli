"""Tests for round-tripping reserved, user-owned provider-config keys.

Provider modules are adopting an ``extra_request_params`` config key -- an
owner-beware dict merged verbatim into every API request, for parameters the
module itself doesn't wrap. Users maintain this key by hand in
settings.yaml; it is never declared as a ``ConfigField`` by any provider
module and the wizard never prompts for it.

Bug: every edit/reconfigure flow rebuilds a provider's config purely from the
module's declared ``ConfigField`` schema answers (``configure_provider()``
in ``provider_config_utils.py``), then callers assign the rebuilt dict
wholesale over the old one. Any key not in the schema -- including
``extra_request_params`` -- is silently DROPPED on edit, defeating the whole
point of a hand-maintained passthrough bag.

Fix: ``_preserve_reserved_keys(old, new)`` in provider_config_utils.py
carries ``extra_request_params`` forward verbatim from the prior config into
the rebuilt one, if present, at every seam that replaces an EXISTING
provider instance's config:

    * ``provider_edit()``          (commands/provider.py)
    * ``_manage_edit_provider()``  (commands/provider.py, interactive loop)
    * ``provider_add()``           (commands/provider.py, same-module
                                     replace-without-id branch)
    * ``_manage_add_provider()``   (commands/provider.py, interactive loop,
                                     same branch)

Covers:
    (a) edit flow preserves extra_request_params verbatim
    (b) edit flow still drops an arbitrary non-schema key (deliberately narrow)
    (c) the wizard never prompts for extra_request_params, even with an
        empty config_fields schema
    (d) a fresh add (no prior config) never invents the key
    (e) unit coverage of _preserve_reserved_keys() itself
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

import amplifier_app_cli.provider_config_utils as pcu
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


def _make_settings(tmp_path: Path) -> AppSettings:
    """Create AppSettings with isolated paths for testing."""
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    return AppSettings(paths=paths)


def _seed_provider(
    settings: AppSettings,
    module: str,
    config: dict,
    priority: int = 1,
    provider_id: str | None = None,
) -> None:
    """Seed a provider entry into global settings for testing.

    Bypasses the plaintext-secret normalization that ``_write_scope`` now
    performs on every provider write, by making the provider module
    unresolvable for the duration of the seed write -- these tests seed
    literal config values purely for test setup. Mirrors the helper in
    test_provider_commands.py.
    """
    entry = {
        "module": module,
        "config": {**config, "priority": priority},
    }
    if provider_id is not None:
        entry["id"] = provider_id
    with patch(
        "amplifier_app_cli.provider_config_utils.get_provider_info",
        return_value=None,
    ):
        settings.set_provider_override(entry, scope="global")


# ============================================================
# (a)/(b): provider edit round-trips extra_request_params, drops other
# non-schema keys
# ============================================================


class TestProviderEditPreservesExtraRequestParams:
    """`amplifier provider edit` must round-trip extra_request_params
    verbatim while continuing to drop unrelated non-schema keys."""

    def test_edit_preserves_extra_request_params_verbatim(self, tmp_path):
        """(a) An existing config carrying extra_request_params survives a
        full reconfigure -- verbatim, not just present."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {
                "default_model": "claude-sonnet-4-6",
                "extra_request_params": {"service_tier": "fast"},
            },
            priority=1,
        )

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                # Simulates a full wizard rebuild that only knows about
                # schema-declared fields -- no knowledge of
                # extra_request_params at all.
                return_value={"default_model": "claude-opus-4-6"},
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            result = runner.invoke(provider, ["edit", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_scope_provider_overrides("global")
        assert len(providers) == 1
        config = providers[0]["config"]
        assert config.get("extra_request_params") == {"service_tier": "fast"}, (
            f"extra_request_params was dropped or altered on edit: {config}"
        )
        assert config["default_model"] == "claude-opus-4-6"

    def test_edit_still_drops_unrelated_non_schema_key(self, tmp_path):
        """(b) The preservation is deliberately narrow: some_stale_key (not
        a reserved key, not a schema field) must still be dropped on edit,
        exactly as before this fix."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {
                "default_model": "claude-sonnet-4-6",
                "some_stale_key": "leftover-value",
            },
            priority=1,
        )

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={"default_model": "claude-opus-4-6"},
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            result = runner.invoke(provider, ["edit", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_scope_provider_overrides("global")
        config = providers[0]["config"]
        assert "some_stale_key" not in config, (
            "some_stale_key is not a reserved key and must still be dropped "
            f"on edit (preservation must stay narrow): {config}"
        )

    def test_manage_edit_preserves_extra_request_params_verbatim(self, tmp_path):
        """(a), interactive-manage-loop variant: `_manage_edit_provider()`
        must apply the same preservation as `provider_edit()`."""
        from amplifier_app_cli.commands.provider import _manage_edit_provider

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {
                "default_model": "claude-sonnet-4-6",
                "extra_request_params": {"service_tier": "fast"},
            },
            priority=1,
        )
        providers = settings.get_provider_overrides()

        with (
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={"default_model": "claude-opus-4-6"},
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            _manage_edit_provider(settings, "e1", providers, scope="global")

        updated = settings.get_scope_provider_overrides("global")
        config = updated[0]["config"]
        assert config.get("extra_request_params") == {"service_tier": "fast"}, (
            f"extra_request_params was dropped by _manage_edit_provider: {config}"
        )


# ============================================================
# (c): the wizard never prompts for extra_request_params
# ============================================================


class TestWizardNeverPromptsForExtraRequestParams:
    """extra_request_params must never become a ConfigField -- it is never
    displayed, never prompted for, never validated -- even in a
    pathological case where a module declares no config_fields at all."""

    def test_configure_provider_never_prompts_with_empty_schema(self, monkeypatch):
        monkeypatch.setenv("TESTPROV_API_KEY", "sk-existing")
        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value={"display_name": "Test Provider", "config_fields": []},
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                return_value=[],
            ),
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch("amplifier_app_cli.provider_config_utils.Prompt.ask") as mock_ask,
        ):
            mock_ask.side_effect = lambda *a, **kw: kw.get("default", "")
            result = pcu.configure_provider(
                "test-provider",
                MagicMock(),
                existing_config={
                    "default_model": "m",
                    "extra_request_params": {"service_tier": "fast"},
                },
            )

        assert result is not None
        assert "extra_request_params" not in result, (
            "configure_provider() must never surface extra_request_params "
            f"as a collected/prompted field: {result}"
        )
        # Every prompt call's field label must never mention it either.
        for call in mock_ask.call_args_list:
            rendered = " ".join(str(a) for a in call.args) + " ".join(
                str(v) for v in call.kwargs.values()
            )
            assert "extra_request_params" not in rendered


# ============================================================
# (d): fresh add never invents the key
# ============================================================


class TestFreshAddNeverInventsExtraRequestParams:
    """A brand-new provider instance (no prior config) must never gain an
    extra_request_params key out of nowhere."""

    def test_provider_add_fresh_no_prior_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as mock_pm_cls,
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={"default_model": "claude-opus-4-6"},
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            mock_pm_cls.return_value.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "desc")
            ]
            result = runner.invoke(provider, ["add", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_scope_provider_overrides("global")
        assert len(providers) == 1
        config = providers[0]["config"]
        assert "extra_request_params" not in config, (
            f"Fresh add must never invent extra_request_params: {config}"
        )

    def test_manage_add_provider_fresh_no_prior_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import _manage_add_provider

        settings = _make_settings(tmp_path)

        with (
            patch("amplifier_app_cli.commands.provider.ProviderManager") as mock_pm_cls,
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={"default_model": "claude-opus-4-6"},
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                return_value="1",
            ),
        ):
            mock_pm_cls.return_value.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "desc")
            ]
            _manage_add_provider(settings, scope="global")

        providers = settings.get_scope_provider_overrides("global")
        assert len(providers) == 1
        config = providers[0]["config"]
        assert "extra_request_params" not in config, (
            f"Fresh add (manage loop) must never invent extra_request_params: {config}"
        )


# ============================================================
# (e): unit coverage of the helper itself
# ============================================================


class TestPreserveReservedKeysHelper:
    """Direct unit coverage of _preserve_reserved_keys()."""

    def test_preserves_extra_request_params_when_present_in_old(self):
        old = {"default_model": "x", "extra_request_params": {"a": 1}}
        new = {"default_model": "y"}
        result = pcu._preserve_reserved_keys(old, new)
        assert result["extra_request_params"] == {"a": 1}
        assert result["default_model"] == "y"

    def test_does_not_override_if_new_already_has_it(self):
        """If new already carries the key (e.g. a future module surfaces it
        deliberately), the old value must not clobber it."""
        old = {"extra_request_params": {"a": 1}}
        new = {"extra_request_params": {"b": 2}}
        result = pcu._preserve_reserved_keys(old, new)
        assert result["extra_request_params"] == {"b": 2}

    def test_no_old_config_is_a_noop(self):
        new = {"default_model": "y"}
        result = pcu._preserve_reserved_keys(None, new)
        assert result == {"default_model": "y"}
        assert "extra_request_params" not in result

    def test_old_without_reserved_key_adds_nothing(self):
        old = {"default_model": "x"}
        new = {"default_model": "y"}
        result = pcu._preserve_reserved_keys(old, new)
        assert result == {"default_model": "y"}

    def test_only_the_declared_reserved_keys_are_preserved(self):
        """Narrow by construction: an arbitrary non-schema key in old is
        never carried forward, only entries in RESERVED_PROVIDER_CONFIG_KEYS."""
        old = {"extra_request_params": {"a": 1}, "some_stale_key": "leftover"}
        new = {}
        result = pcu._preserve_reserved_keys(old, new)
        assert result == {"extra_request_params": {"a": 1}}
        assert "some_stale_key" not in result
