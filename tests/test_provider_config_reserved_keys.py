"""Tests for round-tripping reserved, user-owned provider-config keys.

Provider modules are adopting an ``extra_request_params`` config key -- an
owner-beware dict merged verbatim into every API request, for parameters the
module itself doesn't wrap. Users maintain this key and optional
``auto_continue`` truncation-continuation overrides by hand in settings.yaml,
outside the wizard's ``ConfigField`` schema.

Bug: every edit/reconfigure flow rebuilds a provider's config purely from the
module's declared ``ConfigField`` schema answers (``configure_provider()``
in ``provider_config_utils.py``), then callers assign the rebuilt dict
wholesale over the old one. Any key not in the schema -- including
``extra_request_params`` and ``auto_continue`` -- is silently DROPPED on edit,
defeating settings-only overrides.

Fix: ``_preserve_reserved_keys(old, new)`` in provider_config_utils.py
carries reserved keys forward verbatim from the prior config into the rebuilt
one, if present, at every seam that replaces an EXISTING provider instance's
config:

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
    (f) native False and legacy string values retain their exact types
    (g) all four CLI rewrite seams preserve a settings-only auto_continue
    (h) real interactive/non-interactive wizard returns leave reserved keys
        to the caller's preservation seam, without inventing defaults
"""

from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
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

    @pytest.mark.parametrize("value", [False, "false", True])
    def test_preserves_auto_continue_verbatim_without_mutating_inputs(self, value):
        old = {
            "default_model": "old-model",
            "auto_continue": value,
            "extra_request_params": {"service_tier": "fast"},
            "ghost": "obsolete",
        }
        new = {"default_model": "new-model"}
        old_snapshot, new_snapshot = deepcopy(old), deepcopy(new)

        result = pcu._preserve_reserved_keys(old, new)

        assert result == {
            "default_model": "new-model",
            "auto_continue": value,
            "extra_request_params": {"service_tier": "fast"},
        }
        assert type(result["auto_continue"]) is type(value)
        assert "ghost" not in result
        assert old == old_snapshot
        assert new == new_snapshot

    @pytest.mark.parametrize("old_value", [False, "false", True])
    @pytest.mark.parametrize("new_value", [False, "false", True])
    def test_explicit_new_auto_continue_wins_without_mutating_inputs(
        self, old_value, new_value
    ):
        old = {"auto_continue": old_value, "extra_request_params": {"a": 1}}
        new = {"auto_continue": new_value}
        old_snapshot, new_snapshot = deepcopy(old), deepcopy(new)

        result = pcu._preserve_reserved_keys(old, new)

        assert result == {"auto_continue": new_value, "extra_request_params": {"a": 1}}
        assert type(result["auto_continue"]) is type(new_value)
        assert old == old_snapshot
        assert new == new_snapshot

    @pytest.mark.parametrize("old", [None, {}, {"default_model": "old-model"}])
    def test_missing_auto_continue_is_not_defaulted(self, old):
        new = {"default_model": "new-model"}
        result = pcu._preserve_reserved_keys(old, new)
        assert result == new
        assert "auto_continue" not in result


@pytest.fixture
def settings_only_provider_info():
    """Four ordinary optional fields, no credentials or settings-only fields."""
    return {
        "display_name": "Test Provider",
        "capabilities": [],
        "config_fields": [
            {
                "id": "base_url",
                "display_name": "API Base URL",
                "prompt": "API base URL",
                "field_type": "text",
                "required": False,
            },
            {
                "id": "max_tokens",
                "display_name": "Max Output Tokens",
                "prompt": "Max output tokens",
                "field_type": "text",
                "required": False,
                "requires_model": True,
            },
            {
                "id": "temperature",
                "display_name": "Temperature",
                "prompt": "Sampling temperature",
                "field_type": "text",
                "required": False,
                "requires_model": True,
            },
            {
                "id": "streaming",
                "display_name": "Streaming",
                "prompt": "Stream responses?",
                "field_type": "boolean",
                "required": False,
                "requires_model": True,
            },
        ],
    }


@pytest.fixture
def isolated_provider_commands(tmp_path, monkeypatch, settings_only_provider_info):
    """Use real persistence/preservation, isolating discovery and credentials."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / ".amplifier"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    settings = _make_settings(tmp_path)

    with (
        patch(
            "amplifier_app_cli.commands.provider._get_settings",
            return_value=settings,
        ),
        patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        patch("amplifier_app_cli.commands.provider.KeyManager"),
        patch("amplifier_app_cli.commands.provider.ProviderManager") as manager,
        patch.object(pcu, "get_provider_info", return_value=settings_only_provider_info),
    ):
        manager.return_value.list_providers.return_value = [
            ("provider-test-provider", "Test Provider", "Test metadata")
        ]
        yield settings


class TestAutoContinueRewriteSeams:
    @pytest.mark.parametrize(
        ("args", "user_input", "replace_during_wizard"),
        [
            pytest.param(["edit", "test-provider"], "", False, id="edit"),
            pytest.param(["manage"], "e1\nd\n", False, id="manage-edit"),
            pytest.param(["add", "test-provider"], "", True, id="add-replace"),
            pytest.param(["manage"], "a\n1\nd\n", True, id="manage-add-replace"),
        ],
    )
    def test_cli_rewrites_preserve_native_false(
        self, isolated_provider_commands, args, user_input, replace_during_wizard
    ):
        from amplifier_app_cli.commands.provider import provider

        settings = isolated_provider_commands
        old = {
            "default_model": "old-model",
            "auto_continue": False,
            "extra_request_params": {"service_tier": "fast"},
            "ghost": "obsolete",
        }
        old_snapshot = deepcopy(old)
        if not replace_during_wizard:
            _seed_provider(settings, "provider-test-provider", old)
        wizard_answer = {"default_model": "new-model"}

        def rebuild_config(*args, **kwargs):
            if replace_during_wizard:
                # Exercise the real add-without-id replacement seam: a
                # concurrent same-module entry lands after the pre-lock read.
                # Normal same-module adds instead create a separate instance.
                _seed_provider(settings, "provider-test-provider", old)
            else:
                assert kwargs["existing_config"]["auto_continue"] is False
            return wizard_answer

        with patch(
            "amplifier_app_cli.commands.provider.configure_provider",
            side_effect=rebuild_config,
        ) as configure:
            result = CliRunner().invoke(provider, args, input=user_input)

        assert result.exit_code == 0, result.output
        configure.assert_called_once()
        entries = settings.get_scope_provider_overrides("global")
        assert len(entries) == 1
        assert entries[0]["module"] == "provider-test-provider"
        assert "id" not in entries[0]
        config = entries[0]["config"]
        assert config["default_model"] == "new-model"
        assert config["auto_continue"] is False
        assert config["extra_request_params"] == {"service_tier": "fast"}
        assert "ghost" not in config
        assert wizard_answer == {"default_model": "new-model"}
        assert old == old_snapshot

    @pytest.mark.parametrize(
        ("args", "user_input"),
        [
            pytest.param(["add", "test-provider"], "", id="add"),
            pytest.param(["manage"], "a\n1\nd\n", id="manage-add"),
        ],
    )
    def test_cli_fresh_add_leaves_auto_continue_absent(
        self, isolated_provider_commands, args, user_input
    ):
        from amplifier_app_cli.commands.provider import provider

        settings = isolated_provider_commands
        with patch(
            "amplifier_app_cli.commands.provider.configure_provider",
            return_value={"default_model": "new-model"},
        ) as configure:
            result = CliRunner().invoke(provider, args, input=user_input)

        assert result.exit_code == 0, result.output
        configure.assert_called_once()
        entries = settings.get_scope_provider_overrides("global")
        assert len(entries) == 1
        assert entries[0]["config"]["default_model"] == "new-model"
        assert "auto_continue" not in entries[0]["config"]
        assert "extra_request_params" not in entries[0]["config"]


class TestAutoContinueWizardBoundary:
    @pytest.mark.parametrize("reconfigure", [False, True], ids=["fresh", "edit"])
    def test_interactive_wizard_does_not_prompt_or_return_auto_continue(
        self, isolated_provider_commands, reconfigure
    ):
        old = (
            {
                "default_model": "old-model",
                "auto_continue": False,
                "extra_request_params": {"service_tier": "fast"},
                "ghost": "obsolete",
            }
            if reconfigure
            else None
        )
        old_snapshot = deepcopy(old)
        key_manager = MagicMock()
        model_configs = []

        def answer_text(prompt, **kwargs):
            return "new-model" if prompt == "Model name" else kwargs.get("default", "")

        def list_models(provider_id, collected_config):
            # Capture before configure_provider adds the selected model to
            # this mutable dict; mock call_args retain the same reference.
            model_configs.append((provider_id, deepcopy(collected_config)))
            return []

        with (
            patch.object(pcu, "get_provider_models", side_effect=list_models) as models,
            patch.object(pcu, "console", MagicMock()),
            patch.object(pcu.Prompt, "ask", side_effect=answer_text) as prompt,
            patch.object(
                pcu.Confirm, "ask", side_effect=lambda *a, **kw: kw.get("default")
            ) as confirm,
        ):
            collected = pcu.configure_provider(
                "provider-test-provider",
                key_manager,
                existing_config=old,
                settings=isolated_provider_commands,
            )

        assert collected == {"default_model": "new-model"}
        assert [call.args[0] for call in prompt.call_args_list] == [
            "API base URL",
            "Model name",
            "Max output tokens",
            "Sampling temperature",
        ]
        assert confirm.call_count == 1
        assert confirm.call_args.args[0].startswith("Stream responses?")
        for call in prompt.call_args_list + confirm.call_args_list:
            text = " ".join(str(arg) for arg in call.args).lower()
            assert "continue truncated" not in text
            assert "auto" not in text
        models.assert_called_once()
        assert model_configs == [("test-provider", {})]
        key_manager.save_key.assert_not_called()

        preserved = pcu._preserve_reserved_keys(old, collected)
        if reconfigure:
            assert preserved["auto_continue"] is False
            assert preserved["extra_request_params"] == {"service_tier": "fast"}
        else:
            assert "auto_continue" not in preserved
            assert "extra_request_params" not in preserved
        assert "ghost" not in preserved
        assert collected == {"default_model": "new-model"}
        assert old == old_snapshot

    def test_non_interactive_wizard_return_preserves_override_at_caller_seam(
        self, isolated_provider_commands
    ):
        old = {
            "base_url": "https://api.example.test",
            "default_model": "old-model",
            "max_tokens": 128,
            "temperature": "0.5",
            "streaming": False,
            "auto_continue": False,
            "extra_request_params": {"service_tier": "fast"},
            "ghost": "obsolete",
        }
        old_snapshot = deepcopy(old)
        with (
            patch.object(pcu, "get_provider_models") as models,
            patch.object(pcu.Prompt, "ask") as prompt,
            patch.object(pcu.Confirm, "ask") as confirm,
        ):
            collected = pcu.configure_provider(
                "provider-test-provider",
                MagicMock(),
                existing_config=old,
                non_interactive=True,
                settings=isolated_provider_commands,
            )

        assert collected == {
            "base_url": "https://api.example.test",
            "default_model": "old-model",
            "max_tokens": 128,
            "temperature": "0.5",
            "streaming": False,
        }
        prompt.assert_not_called()
        confirm.assert_not_called()
        models.assert_not_called()
        collected_snapshot = deepcopy(collected)
        preserved = pcu._preserve_reserved_keys(old, collected)
        assert preserved["auto_continue"] is False
        assert preserved["extra_request_params"] == {"service_tier": "fast"}
        assert "ghost" not in preserved
        assert collected == collected_snapshot
        assert old == old_snapshot
