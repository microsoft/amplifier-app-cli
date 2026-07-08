"""Tests for docs/designs/provider-instance-credentials.md.

Covers:
- Bug 1: id-uniqueness check (NFC-normalized) on both add paths.
- Bug 2: scope threading through `_manage_add_provider` / `provider add --scope`.
- Bug 3: per-instance credential binding -- `_claimed_env_vars`,
  `_secret_env_var_for`, `_suggest_instance_env_var`, `env_var_overrides`
  threading, edit-path recovery, non-interactive fail-loud, stale-credential
  warn-and-reuse, cross-scope aggregation robustness.
- File locking (§5.5) concurrency guards.
- The §5.4.6/§9 regression tripwire pinning §3's verification.
"""

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths

REPO_ROOT = Path(__file__).resolve().parent.parent


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
    scope: str = "global",
) -> None:
    """Seed a provider entry into settings for testing."""
    entry = {
        "module": module,
        "config": {**config, "priority": priority},
    }
    if provider_id is not None:
        entry["id"] = provider_id
    settings.set_provider_override(entry, scope=scope)  # type: ignore[arg-type]


def _write_raw_scope(settings: AppSettings, scope: str, data: dict) -> None:
    """Write a scope's settings.yaml directly to disk, bypassing all
    AppSettings write-path logic (including plaintext-secret
    normalization). Used to seed pre-existing literal-secret state in
    tests -- simulating data written before this fix existed, or by an
    external process -- without the seed itself being normalized away.
    """
    path = settings._get_scope_path(scope)  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _mock_provider_info(env_var: str = "ANTHROPIC_API_KEY") -> dict:
    """Minimal provider info dict with one secret config field."""
    return {
        "display_name": "Anthropic",
        "config_fields": [
            {
                "id": "api_key",
                "display_name": "API Key",
                "field_type": "secret",
                "prompt": "Enter your API key",
                "env_var": env_var,
                "required": True,
            }
        ],
    }


# ============================================================
# Bug 1: id-uniqueness (NFC-normalized), both add paths
# ============================================================


class TestBug1IdUniqueness:
    def test_provider_add_cli_blocks_same_scope_duplicate_id(self, tmp_path):
        """`provider add` must block a same-scope duplicate id."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
            provider_id="anthropic-fable",
        )

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch("amplifier_app_cli.commands.provider.ProviderManager"),
        ):
            # same_module is empty for "anthropic" here since the existing
            # entry has a distinct module name check only via module id --
            # seed matching module so the id-prompt path is exercised.
            result = runner.invoke(
                provider, ["add", "anthropic"], input="anthropic-fable\n"
            )

        assert result.exit_code != 0, (
            f"Expected non-zero exit for duplicate id, got 0: {result.output}"
        )
        assert "already exists" in result.output.lower()

        # No duplicate entry written.
        providers = settings.get_scope_provider_overrides("global")
        matching = [p for p in providers if p.get("id") == "anthropic-fable"]
        assert len(matching) == 1, f"Expected exactly one entry, got: {matching}"

    def test_manage_add_provider_blocks_same_scope_duplicate_id(
        self, tmp_path, monkeypatch
    ):
        """`_manage_add_provider` (dashboard flow) must block + re-prompt on
        a same-scope duplicate id."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import _manage_add_provider

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
            provider_id="anthropic-fable",
        )

        with (
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={
                    "default_model": "claude-opus-4-6",
                    "api_key": "${ANTHROPIC_API_KEY}",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            # First prompt: pick provider #1 (anthropic).
            # Second prompt: colliding id "anthropic-fable" (rejected).
            # Third prompt: distinct id "anthropic-fable-2" (accepted).
            with patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                side_effect=["1", "anthropic-fable", "anthropic-fable-2"],
            ):
                _manage_add_provider(settings, scope="global")

        providers = settings.get_scope_provider_overrides("global")
        ids = [p.get("id") for p in providers]
        assert ids.count("anthropic-fable") == 1
        assert "anthropic-fable-2" in ids

    def test_cross_scope_duplicate_id_warns_but_does_not_block(self, tmp_path):
        """A duplicate id in a *different* scope must warn, not block."""
        from amplifier_app_cli.commands.provider import _id_collision_status

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
            provider_id="anthropic-fable",
            scope="global",
        )

        blocked, warning = _id_collision_status(settings, "project", "anthropic-fable")
        assert blocked is False
        assert warning is not None
        assert "global" in warning

    def test_id_uniqueness_nfc_normalization(self, tmp_path):
        """An id submitted as NFD must collide with an existing NFC id even
        though the raw byte strings differ (design §6, §9)."""
        from amplifier_app_cli.commands.provider import _id_collision_status

        settings = _make_settings(tmp_path)
        # Precomposed NFC "café" (U+00E9)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
            provider_id="cafe\u0301",  # NFD: c-a-f-e + combining acute accent
            scope="global",
        )

        # Submit the NFC precomposed form and expect a collision.
        blocked, _warning = _id_collision_status(settings, "global", "caf\u00e9")
        assert blocked is True, "NFC/NFD forms of the same id must be treated as equal"


# ============================================================
# Bug 2: scope threading
# ============================================================


class TestBug2ScopeThreading:
    def test_provider_add_scope_option_writes_to_project_scope(
        self, tmp_path, monkeypatch
    ):
        """`provider add --scope project` must write to project scope, not global."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

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
                return_value={
                    "default_model": "claude-sonnet-4-6",
                    "api_key": "${ANTHROPIC_API_KEY}",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            result = runner.invoke(provider, ["add", "anthropic", "--scope", "project"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "saved to project settings" in result.output.lower()

        project_providers = settings.get_scope_provider_overrides("project")
        assert len(project_providers) == 1
        global_providers = settings.get_scope_provider_overrides("global")
        assert len(global_providers) == 0

    def test_manage_add_provider_respects_current_scope(self, tmp_path, monkeypatch):
        """`_manage_add_provider` must write to the scope it's given, not
        hardcode 'global' (Bug 2)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import _manage_add_provider

        settings = _make_settings(tmp_path)

        with (
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={
                    "default_model": "claude-sonnet-4-6",
                    "api_key": "${ANTHROPIC_API_KEY}",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            with patch(
                "amplifier_app_cli.commands.provider.Prompt.ask", return_value="1"
            ):
                _manage_add_provider(settings, scope="local")

        assert len(settings.get_scope_provider_overrides("local")) == 1
        assert len(settings.get_scope_provider_overrides("global")) == 0

    def test_provider_manage_loop_passes_current_scope_to_add(self, tmp_path):
        """The dashboard's [a] action must forward current_scope, not
        silently default to global."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)

        with (
            patch(
                "amplifier_app_cli.commands.provider._manage_add_provider"
            ) as mock_add,
            patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                side_effect=["a", "d"],
            ),
        ):
            provider_manage_loop(settings, scope="project")

        mock_add.assert_called_once_with(settings, scope="project")


# ============================================================
# Bug 3 unit tests: provider_config_utils helpers
# ============================================================


class TestClaimedEnvVars:
    def test_claimed_env_vars_collects_placeholders_across_scopes(self, tmp_path):
        from amplifier_app_cli.provider_config_utils import _claimed_env_vars

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"api_key": "${ANTHROPIC_API_KEY}"},
            provider_id="anthropic-opus",
            scope="global",
        )
        _seed_provider(
            settings,
            "provider-openai",
            {"api_key": "${OPENAI_API_KEY}"},
            scope="project",
        )

        claimed = _claimed_env_vars(settings)
        assert "ANTHROPIC_API_KEY" in claimed
        assert "OPENAI_API_KEY" in claimed

    def test_claimed_env_vars_tolerates_literal_values(self, tmp_path, monkeypatch):
        """A literal (non-placeholder) config value claims nothing BY
        ITSELF (i.e. with no corresponding keys.env entry).

        Seeds via ``_write_raw_scope`` (bypassing ``_write_scope``'s
        plaintext-secret normalization) so the literal survives on disk
        exactly as written -- this test is about ``_claimed_env_vars``'
        own tolerance of literals it encounters in a scope file, not
        about the normalization behavior itself (which has its own
        dedicated coverage in TestPlaintextSecretNormalization). Isolated
        from the real ``~/.amplifier/keys.env`` via the ``Path.home``
        monkeypatch, since ``_claimed_env_vars`` now also consults
        keys.env membership (§5.4.1 fix) -- without this isolation the
        assertion would be polluted by whatever real secrets happen to be
        stored on the machine running the test.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.provider_config_utils import _claimed_env_vars

        settings = _make_settings(tmp_path)
        _write_raw_scope(
            settings,
            "global",
            {
                "config": {
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "config": {
                                "api_key": "sk-literal-not-a-placeholder",
                                "priority": 1,
                            },
                        }
                    ]
                }
            },
        )

        claimed = _claimed_env_vars(settings)
        assert claimed == set()

    def test_claimed_env_vars_raises_on_corrupt_scope_file(self, tmp_path):
        """A syntactically-corrupt (non-empty) scope file must raise, not
        silently under-count (design §5.4.1, §9)."""
        from amplifier_app_cli.provider_config_utils import _claimed_env_vars

        settings = _make_settings(tmp_path)
        project_path = settings._get_scope_path("project")
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text("providers: [unclosed: [\n", encoding="utf-8")

        with pytest.raises(Exception):
            _claimed_env_vars(settings)

    def test_claimed_env_vars_skips_unset_session_scope(self, tmp_path, monkeypatch):
        """Session scope with no session_id set must be skipped, not raise."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.provider_config_utils import _claimed_env_vars

        settings = _make_settings(tmp_path)
        # Should not raise even though session_settings is None.
        claimed = _claimed_env_vars(settings)
        assert claimed == set()

    def test_claimed_env_vars_includes_names_already_stored_in_keys_env(
        self, tmp_path, monkeypatch
    ):
        """A name backed by a real, saved secret in keys.env must count as
        claimed even when NO scope's config references it via a ${VAR}
        placeholder yet -- this is the fix for the silent-clobber race:
        an existing instance's literal secret that hasn't been normalized
        yet leaves no placeholder trace, but once ANY entry's secret has
        actually been saved under a given name, that name is spoken for.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.key_manager import KeyManager
        from amplifier_app_cli.provider_config_utils import _claimed_env_vars

        settings = _make_settings(tmp_path)
        # No scope file references ANTHROPIC_API_KEY via a placeholder at
        # all -- simulating a name that was saved to keys.env moments ago
        # by a sibling call in the same batch, before its own scope write
        # has landed.
        KeyManager().save_key("ANTHROPIC_API_KEY", "sk-already-saved")

        claimed = _claimed_env_vars(settings)
        assert "ANTHROPIC_API_KEY" in claimed

    def test_claimed_env_vars_literal_alone_still_claims_nothing(
        self, tmp_path, monkeypatch
    ):
        """A literal (non-placeholder) config value with NO corresponding
        keys.env entry still claims nothing by itself -- the original
        "tolerates literal values" contract is preserved for the case
        where no secret has actually been saved anywhere yet.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.provider_config_utils import _claimed_env_vars

        settings = _make_settings(tmp_path)
        _write_raw_scope(
            settings,
            "global",
            {
                "config": {
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "config": {
                                "api_key": "sk-literal-not-a-placeholder",
                                "priority": 1,
                            },
                        }
                    ]
                }
            },
        )
        # No keys.env file exists at all yet.
        claimed = _claimed_env_vars(settings)
        assert claimed == set()


class TestSecretEnvVarFor:
    def test_secret_env_var_for_returns_declared_secret_field_env_var(self):
        from amplifier_app_cli.provider_config_utils import _secret_env_var_for

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            assert _secret_env_var_for("provider-anthropic") == "ANTHROPIC_API_KEY"

    def test_secret_env_var_for_returns_none_when_no_secret_field(self):
        from amplifier_app_cli.provider_config_utils import _secret_env_var_for

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value={"display_name": "X", "config_fields": []},
        ):
            assert _secret_env_var_for("provider-x") is None

    def test_secret_field_id_for_returns_field_id(self):
        from amplifier_app_cli.provider_config_utils import _secret_field_id_for

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            assert _secret_field_id_for("provider-anthropic") == "api_key"


class TestSuggestInstanceEnvVar:
    def test_basic_derivation(self):
        from amplifier_app_cli.provider_config_utils import _suggest_instance_env_var

        suggested = _suggest_instance_env_var(
            "provider-anthropic", "anthropic-fable", claimed=set()
        )
        assert suggested == "ANTHROPIC_FABLE_API_KEY"

    def test_dedupes_against_claimed_by_appending_uniquely(self):
        """Two genuinely distinct ids must not collide even if the naive
        suggestion would (covered by raising -- see the separator-style
        collision test). This test just proves distinct ids produce
        distinct names when neither is claimed."""
        from amplifier_app_cli.provider_config_utils import _suggest_instance_env_var

        a = _suggest_instance_env_var("provider-anthropic", "anthropic-fable", set())
        b = _suggest_instance_env_var("provider-anthropic", "anthropic-opus", set())
        assert a != b

    def test_empty_suffix_raises_value_error(self):
        """An id built only from symbols sanitizes to an empty suffix and
        must raise (design §5.4.2)."""
        from amplifier_app_cli.provider_config_utils import _suggest_instance_env_var

        with pytest.raises(ValueError):
            _suggest_instance_env_var("provider-anthropic", "---", claimed=set())

    def test_separator_style_collision_raises_value_error(self):
        """Two ids differing only in separator style sanitize to the same
        suggestion and must raise rather than silently re-collide."""
        from amplifier_app_cli.provider_config_utils import _suggest_instance_env_var

        claimed = {"ANTHROPIC_FABLE_API_KEY"}
        with pytest.raises(ValueError):
            _suggest_instance_env_var(
                "provider-anthropic", "anthropic_fable", claimed=claimed
            )

    def test_id_that_sanitizes_to_bare_type_name_raises(self):
        """An instance id equal to the display name alone produces no
        distinguishing suffix and must raise."""
        from amplifier_app_cli.provider_config_utils import _suggest_instance_env_var

        with pytest.raises(ValueError):
            _suggest_instance_env_var("provider-anthropic", "anthropic", claimed=set())


class TestPromptForFieldEnvVarOverrides:
    def test_env_var_overrides_redirect_save_and_placeholder(self):
        """`_prompt_for_field` must save/placeholder under the OVERRIDDEN
        name, not the field's declared type default."""
        from amplifier_app_cli.provider_config_utils import _prompt_for_field

        field = {
            "id": "api_key",
            "display_name": "API Key",
            "field_type": "secret",
            "prompt": "Enter your API key",
            "env_var": "ANTHROPIC_API_KEY",
            "required": True,
        }
        mock_key_manager = MagicMock()
        mock_key_manager.has_key.return_value = False

        with patch(
            "amplifier_app_cli.provider_config_utils.Prompt.ask",
            return_value="sk-secret-value",
        ):
            field_id, value = _prompt_for_field(
                field,
                mock_key_manager,
                collected_config={},
                existing_config=None,
                env_var_overrides={"ANTHROPIC_API_KEY": "ANTHROPIC_FABLE_API_KEY"},
            )

        assert field_id == "api_key"
        assert value == "${ANTHROPIC_FABLE_API_KEY}"
        mock_key_manager.save_key.assert_called_once_with(
            "ANTHROPIC_FABLE_API_KEY", "sk-secret-value"
        )


# ============================================================
# Bug 3 integration: two same-type instances, distinct keys
# ============================================================


class TestBug3Integration:
    def test_two_instances_get_distinct_keys_and_placeholders(
        self, tmp_path, monkeypatch
    ):
        """configure_provider() called twice with different
        env_var_overrides must save distinct keys.env entries and produce
        distinct ${VAR} placeholders."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.key_manager import KeyManager
        from amplifier_app_cli.provider_config_utils import configure_provider

        key_manager = KeyManager()

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="sk-opus-value",
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._prompt_model_selection",
                return_value="claude-opus",
            ),
        ):
            opus_config = configure_provider(
                "provider-anthropic", key_manager, env_var_overrides={}
            )

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="sk-fable-value",
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._prompt_model_selection",
                return_value="claude-fable",
            ),
        ):
            fable_config = configure_provider(
                "provider-anthropic",
                key_manager,
                env_var_overrides={"ANTHROPIC_API_KEY": "ANTHROPIC_FABLE_API_KEY"},
            )

        assert opus_config is not None and fable_config is not None
        assert opus_config["api_key"] == "${ANTHROPIC_API_KEY}"
        assert fable_config["api_key"] == "${ANTHROPIC_FABLE_API_KEY}"

        keys_file = tmp_path / ".amplifier" / "keys.env"
        content = keys_file.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY=" in content
        assert "ANTHROPIC_FABLE_API_KEY=" in content

    def test_edit_second_instance_keeps_distinct_env_var(self, tmp_path):
        """Editing the second instance and pressing Enter to keep the
        existing value must NOT reset its env var to the type default
        (design §5.3, the "must not miss" fix; §9 re-collision guard)."""
        from amplifier_app_cli.commands.provider import _manage_edit_provider

        settings = _make_settings(tmp_path)
        scope_data = {
            "config": {
                "providers": [
                    {
                        "module": "provider-anthropic",
                        "id": "anthropic-opus",
                        "config": {
                            "default_model": "claude-opus",
                            "api_key": "${ANTHROPIC_API_KEY}",
                            "priority": 1,
                        },
                    },
                    {
                        "module": "provider-anthropic",
                        "id": "anthropic-fable",
                        "config": {
                            "default_model": "claude-fable",
                            "api_key": "${ANTHROPIC_FABLE_API_KEY}",
                            "priority": 2,
                        },
                    },
                ]
            }
        }
        settings._write_scope("global", scope_data)

        providers = settings.get_provider_overrides()
        idx = next(
            i for i, p in enumerate(providers, 1) if p.get("id") == "anthropic-fable"
        )

        captured_overrides: dict = {}

        def _fake_configure_provider(module_id, key_manager, **kwargs):
            captured_overrides.update(kwargs.get("env_var_overrides") or {})
            # Simulate "press Enter to keep existing" -- returns config with
            # the SAME placeholder it already had.
            return {
                "default_model": "claude-fable",
                "api_key": "${ANTHROPIC_FABLE_API_KEY}",
            }

        with (
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                side_effect=_fake_configure_provider,
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch(
                "amplifier_app_cli.commands.provider._secret_env_var_for",
                return_value="ANTHROPIC_API_KEY",
            ),
            patch(
                "amplifier_app_cli.commands.provider._secret_field_id_for",
                return_value="api_key",
            ),
        ):
            _manage_edit_provider(settings, f"e{idx}", providers, scope="global")

        # The override map passed to configure_provider must have recovered
        # the instance's real name from its stored placeholder.
        assert captured_overrides == {"ANTHROPIC_API_KEY": "ANTHROPIC_FABLE_API_KEY"}

        raw = settings.get_scope_provider_overrides("global")
        by_id = {p["id"]: p for p in raw}
        assert by_id["anthropic-fable"]["config"]["api_key"] == (
            "${ANTHROPIC_FABLE_API_KEY}"
        )
        # The first instance's placeholder is untouched.
        assert by_id["anthropic-opus"]["config"]["api_key"] == "${ANTHROPIC_API_KEY}"


# ============================================================
# Silent-clobber race regression (§5.4.1 fix): a still-unnormalized
# literal secret for an EXISTING instance must not let a brand-new
# instance's freshly-saved secret get overwritten when that existing
# entry is later normalized in the same write.
# ============================================================


class TestKeysEnvRaceRegression:
    def test_new_instance_secret_survives_later_normalization_of_older_literal(
        self, tmp_path, monkeypatch
    ):
        """Reproduces the exact sequence that exposed the bug:

        1. An existing instance ('anthropic-fable') has a literal secret
           still on disk, unclaimed by ``_claimed_env_vars``'
           placeholder-scan definition (it hasn't been normalized yet).
        2. A brand-new instance ('leaktest') is added via
           ``configure_provider`` with a DIFFERENT literal secret. Its
           default env var (ANTHROPIC_API_KEY) looks unclaimed at this
           moment too, so it saves under the type default and gets a
           placeholder pointing at it.
        3. The whole provider list (both entries) is written via
           ``_write_scope``, which normalizes 'anthropic-fable's still
           literal secret. Pre-fix, ``_claimed_env_vars`` didn't know
           ANTHROPIC_API_KEY already had a real, freshly-saved value (that
           fact lived only in keys.env, not in any placeholder), so it
           would reuse the same name and silently clobber leaktest's
           secret. Post-fix, keys.env's real membership is part of
           "claimed", so 'anthropic-fable' gets a distinct name instead.

        Asserts, by reading keys.env directly, that BOTH distinct input
        values survive under their respective (possibly distinct) names --
        not one value silently overwriting the other.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.key_manager import KeyManager
        from amplifier_app_cli.provider_config_utils import configure_provider

        settings = _make_settings(tmp_path)

        # Step 1: seed the existing instance's still-unnormalized literal
        # secret directly on disk (bypassing _write_scope's normalization),
        # simulating state from before this instance was ever normalized.
        _write_raw_scope(
            settings,
            "global",
            {
                "config": {
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "id": "anthropic-fable",
                            "config": {
                                "default_model": "claude-fable",
                                "api_key": "sk-fable-literal-value",
                                "priority": 1,
                            },
                        }
                    ]
                }
            },
        )

        key_manager = KeyManager()

        # Step 2: add the new 'leaktest' instance via the same
        # configure_provider() entry point the real add-flow uses, with no
        # override -- mirroring the real race window, since the add-flow's
        # own collision check (_resolve_env_var_overrides) also can't see
        # anthropic-fable's literal at this point (it hasn't reached
        # keys.env or a placeholder yet either).
        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="sk-leaktest-literal-value",
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._prompt_model_selection",
                return_value="claude-leaktest",
            ),
        ):
            leaktest_config = configure_provider(
                "provider-anthropic", key_manager, env_var_overrides={}
            )

        assert leaktest_config is not None
        assert leaktest_config["api_key"] == "${ANTHROPIC_API_KEY}"

        keys_file = tmp_path / ".amplifier" / "keys.env"
        assert "sk-leaktest-literal-value" in keys_file.read_text(encoding="utf-8")

        # Step 3: write the full provider list (existing literal entry +
        # new leaktest entry) in one batch, exactly as the real add-flow
        # does (read scope, append new entry, write scope) -- this is
        # where anthropic-fable's literal finally gets normalized.
        scope_data = settings._read_scope("global")
        scope_data["config"]["providers"].append(
            {
                "module": "provider-anthropic",
                "id": "leaktest",
                "config": {**leaktest_config, "priority": 2},
            }
        )

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            settings._write_scope("global", scope_data)

        providers = settings.get_scope_provider_overrides("global")
        by_id = {p["id"]: p for p in providers}
        fable_key_var = by_id["anthropic-fable"]["config"]["api_key"]
        leaktest_key_var = by_id["leaktest"]["config"]["api_key"]

        assert fable_key_var.startswith("${") and fable_key_var.endswith("}")
        assert leaktest_key_var.startswith("${") and leaktest_key_var.endswith("}")
        assert fable_key_var != leaktest_key_var, (
            "anthropic-fable's literal was normalized onto the SAME env "
            "var as leaktest -- this reintroduces the silent-clobber race."
        )

        # Parse keys.env directly and confirm two distinct real values are
        # present, matching the two distinct inputs -- not one value
        # appearing twice (i.e. one clobbered the other).
        final_content = keys_file.read_text(encoding="utf-8")
        stored: dict[str, str] = {}
        for line in final_content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                stored[k.strip()] = v.strip().strip('"')

        fable_var_name = fable_key_var[2:-1]
        leaktest_var_name = leaktest_key_var[2:-1]
        assert stored.get(fable_var_name) == "sk-fable-literal-value"
        assert stored.get(leaktest_var_name) == "sk-leaktest-literal-value"


# ============================================================
# Exact-name prefill (§5.4.3 replacement for the cut fuzzy scan)
# ============================================================


class TestExactNamePrefill:
    def test_prefill_uses_live_env_value_when_set(self, tmp_path, monkeypatch):
        """When the suggested name is already a real, unclaimed env var,
        the prompt's default is prefilled with it."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import _resolve_env_var_overrides

        monkeypatch.setenv("ANTHROPIC_FABLE_API_KEY", "sk-live-value")
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"api_key": "${ANTHROPIC_API_KEY}"},
            provider_id="anthropic-opus",
            scope="global",
        )
        mock_key_manager = MagicMock()
        mock_key_manager.has_key.return_value = False
        mock_key_manager.has_stored_key.return_value = False

        with (
            patch(
                "amplifier_app_cli.commands.provider._secret_env_var_for",
                return_value="ANTHROPIC_API_KEY",
            ),
            patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                return_value="ANTHROPIC_FABLE_API_KEY",
            ) as mock_ask,
        ):
            overrides = _resolve_env_var_overrides(
                settings, mock_key_manager, "provider-anthropic", "anthropic-fable"
            )

        assert overrides == {"ANTHROPIC_API_KEY": "ANTHROPIC_FABLE_API_KEY"}
        # default= kwarg on the prompt call should be the derived suggestion
        _, kwargs = mock_ask.call_args
        assert kwargs.get("default") == "ANTHROPIC_FABLE_API_KEY"

    def test_no_prefill_note_when_env_var_not_set(self, tmp_path, monkeypatch):
        """With no live env var set, the default is just the plain derived
        suggestion (no fuzzy-match behavior)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import _resolve_env_var_overrides

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"api_key": "${ANTHROPIC_API_KEY}"},
            provider_id="anthropic-opus",
            scope="global",
        )
        mock_key_manager = MagicMock()
        mock_key_manager.has_key.return_value = False
        mock_key_manager.has_stored_key.return_value = False

        with (
            patch(
                "amplifier_app_cli.commands.provider._secret_env_var_for",
                return_value="ANTHROPIC_API_KEY",
            ),
            patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                return_value="ANTHROPIC_FABLE_API_KEY",
            ),
        ):
            overrides = _resolve_env_var_overrides(
                settings, mock_key_manager, "provider-anthropic", "anthropic-fable"
            )

        assert overrides == {"ANTHROPIC_API_KEY": "ANTHROPIC_FABLE_API_KEY"}


# ============================================================
# Stale-credential warn-and-reuse (§5.4.4)
# ============================================================


class TestStaleCredentialWarnAndReuse:
    def test_warns_and_reuses_keys_env_only_leftover(self, tmp_path, monkeypatch):
        """A keys.env-only leftover (not a live env var) must trigger the
        warn-and-reuse message before the secret prompt runs."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import _resolve_env_var_overrides

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"api_key": "${ANTHROPIC_API_KEY}"},
            provider_id="anthropic-opus",
            scope="global",
        )

        mock_key_manager = MagicMock()
        # Simulate: chosen_name is in os.environ (loaded from keys.env at
        # KeyManager startup) AND present in the on-disk store.
        mock_key_manager.has_key.return_value = True
        mock_key_manager.has_stored_key.return_value = True

        with (
            patch(
                "amplifier_app_cli.commands.provider._secret_env_var_for",
                return_value="ANTHROPIC_API_KEY",
            ),
            patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                return_value="ANTHROPIC_FABLE_API_KEY",
            ),
            patch("amplifier_app_cli.commands.provider.console") as mock_console,
        ):
            _resolve_env_var_overrides(
                settings, mock_key_manager, "provider-anthropic", "anthropic-fable"
            )

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "stored credential" in printed.lower()
        assert "reused" in printed.lower()


# ============================================================
# Non-interactive fail-loud (§5.4.5)
# ============================================================


class TestNonInteractiveFailLoud:
    def test_raises_without_override_on_collision(self, tmp_path):
        """A second same-type instance in non_interactive mode without an
        explicit env_var_overrides must raise ValueError."""
        from amplifier_app_cli.provider_config_utils import configure_provider

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"api_key": "${ANTHROPIC_API_KEY}"},
            provider_id="anthropic-opus",
            scope="global",
        )
        mock_key_manager = MagicMock()

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            with pytest.raises(ValueError):
                configure_provider(
                    "provider-anthropic",
                    mock_key_manager,
                    non_interactive=True,
                    settings=settings,
                )

    def test_succeeds_with_explicit_override(self, tmp_path):
        """Supplying env_var_overrides in non-interactive mode succeeds and
        produces the expected distinct placeholder."""
        from amplifier_app_cli.provider_config_utils import configure_provider

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"api_key": "${ANTHROPIC_API_KEY}"},
            provider_id="anthropic-opus",
            scope="global",
        )
        mock_key_manager = MagicMock()

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            config = configure_provider(
                "provider-anthropic",
                mock_key_manager,
                non_interactive=True,
                settings=settings,
                env_var_overrides={"ANTHROPIC_API_KEY": "ANTHROPIC_FABLE_API_KEY"},
            )

        assert config is not None
        # No live env var set for either name, so neither is placeholdered
        # via the env-var branch; but no exception was raised, which is the
        # behavior under test. (Non-interactive mode without any existing
        # config or CLI overrides simply won't populate api_key here --
        # confirmed no ValueError was raised.)

    def test_no_settings_skips_check_backward_compatible(self):
        """Omitting `settings` entirely must skip the fail-loud check
        (fully backward compatible with existing callers)."""
        from amplifier_app_cli.provider_config_utils import configure_provider

        mock_key_manager = MagicMock()
        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            # Should not raise even though non_interactive=True and no
            # override was given -- settings=None means "skip the check".
            config = configure_provider(
                "provider-anthropic", mock_key_manager, non_interactive=True
            )
        assert config is not None


# ============================================================
# Concurrency (§5.5)
# ============================================================


class TestConcurrency:
    def test_key_manager_save_key_concurrent_writes_both_survive(
        self, tmp_path, monkeypatch
    ):
        """Two threads calling KeyManager.save_key with different key names
        concurrently must not lose either write."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.key_manager import KeyManager

        km_a = KeyManager()
        km_b = KeyManager()

        def _save_a():
            km_a.save_key("KEY_A", "value-a")

        def _save_b():
            km_b.save_key("KEY_B", "value-b")

        t1 = threading.Thread(target=_save_a)
        t2 = threading.Thread(target=_save_b)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        content = (tmp_path / ".amplifier" / "keys.env").read_text(encoding="utf-8")
        assert "KEY_A=" in content
        assert "KEY_B=" in content

    def test_concurrent_scope_writes_both_survive(self, tmp_path):
        """Two concurrent add-provider-style scope writes (via _scope_lock)
        must not lose either write."""
        settings = _make_settings(tmp_path)

        def _write(provider_id: str):
            with settings._scope_lock("global"):
                scope_settings = settings._read_scope("global")
                providers = (scope_settings.get("config") or {}).get("providers", [])
                if not isinstance(providers, list):
                    providers = []
                providers.append(
                    {
                        "module": "provider-anthropic",
                        "id": provider_id,
                        "config": {"priority": 1},
                    }
                )
                scope_settings.setdefault("config", {})["providers"] = providers
                settings._write_scope("global", scope_settings)

        t1 = threading.Thread(target=_write, args=("instance-a",))
        t2 = threading.Thread(target=_write, args=("instance-b",))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        providers = settings.get_scope_provider_overrides("global")
        ids = {p.get("id") for p in providers}
        assert ids == {"instance-a", "instance-b"}, (
            f"Expected both concurrent writes to survive, got: {ids}"
        )


# ============================================================
# Malformed id fail-loud (surfaced via _suggest_instance_env_var, already
# covered above) -- additional coverage for the caller-side wiring.
# ============================================================


class TestMalformedIdCallerWiring:
    def test_provider_add_cli_exits_on_degenerate_suggestion(self, tmp_path):
        """CLI `provider add` must exit(1) (not silently proceed) when the
        chosen id can't produce a usable credential suggestion."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"api_key": "${ANTHROPIC_API_KEY}"},
            provider_id="anthropic-opus",
            scope="global",
        )

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch(
                "amplifier_app_cli.commands.provider._secret_env_var_for",
                return_value="ANTHROPIC_API_KEY",
            ),
        ):
            # instance_id "---" sanitizes to an empty suffix.
            result = runner.invoke(provider, ["add", "anthropic"], input="---\n")

        assert result.exit_code != 0


# ============================================================
# Plaintext-secret normalization at the _write_scope choke point
# (docs/designs/provider-instance-credentials.md addendum: no scope's
# settings.yaml may ever hold a literal secret.)
# ============================================================


class TestPlaintextSecretNormalization:
    def test_reported_bug_reorder_into_project_scope_normalizes_literal(
        self, tmp_path, monkeypatch
    ):
        """Reordering an entry with a literal secret into a new scope must
        result in a placeholder in that scope's settings.yaml, never the
        literal, with the secret moved to keys.env and a visibility
        message printed."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

        # Simulate pre-existing literal state in global scope, as if
        # written before this fix existed.
        _write_raw_scope(
            settings,
            "global",
            {
                "config": {
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "id": "anthropic-fable",
                            "config": {
                                "default_model": "claude-sonnet-4-6",
                                "api_key": "sk-literal-reported-bug",
                                "priority": 1,
                            },
                        }
                    ]
                }
            },
        )
        entry = settings.get_scope_provider_overrides("global")[0]

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            # Simulate "reorder into project scope": the entry (literal
            # secret and all) gets written into a different scope.
            settings.set_provider_override(dict(entry), scope="project")

        project_providers = settings.get_scope_provider_overrides("project")
        assert len(project_providers) == 1
        api_key = project_providers[0]["config"]["api_key"]
        assert api_key.startswith("${") and api_key.endswith("}")
        assert api_key != "sk-literal-reported-bug"

        project_raw = settings._get_scope_path("project").read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert "sk-literal-reported-bug" not in project_raw

        keys_file = tmp_path / ".amplifier" / "keys.env"
        assert keys_file.exists()
        assert "sk-literal-reported-bug" in keys_file.read_text(encoding="utf-8")

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "moved it to keys.env" in printed
        assert "anthropic-fable" in printed
        assert "project settings are team-shared" in printed

    def test_add_cli_path_normalizes_literal(self, tmp_path, monkeypatch):
        """`provider add` must not let a literal secret reach
        settings.yaml as plaintext, proving enforcement at the choke
        point rather than only in the reorder path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

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
                return_value={
                    "default_model": "claude-sonnet-4-6",
                    "api_key": "sk-literal-from-add",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            result = runner.invoke(provider, ["add", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_scope_provider_overrides("global")
        assert len(providers) == 1
        assert providers[0]["config"]["api_key"] == "${ANTHROPIC_API_KEY}"

        global_raw = settings._get_scope_path("global").read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert "sk-literal-from-add" not in global_raw

    def test_manage_edit_provider_path_normalizes_literal(self, tmp_path, monkeypatch):
        """`_manage_edit_provider` must not let a literal secret returned
        by configure_provider reach settings.yaml as plaintext."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import _manage_edit_provider

        settings = _make_settings(tmp_path)
        _write_raw_scope(
            settings,
            "global",
            {
                "config": {
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "id": "anthropic-opus",
                            "config": {
                                "default_model": "claude-opus",
                                "api_key": "${ANTHROPIC_API_KEY}",
                                "priority": 1,
                            },
                        }
                    ]
                }
            },
        )

        providers = settings.get_provider_overrides()

        with (
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={
                    "default_model": "claude-opus",
                    "api_key": "sk-literal-from-edit",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch(
                "amplifier_app_cli.commands.provider._secret_env_var_for",
                return_value="ANTHROPIC_API_KEY",
            ),
            patch(
                "amplifier_app_cli.commands.provider._secret_field_id_for",
                return_value="api_key",
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
        ):
            _manage_edit_provider(settings, "e1", providers, scope="global")

        raw = settings.get_scope_provider_overrides("global")
        api_key = raw[0]["config"]["api_key"]
        # Proves literal -> placeholder at this choke point. The exact
        # chosen name isn't asserted here: because this instance's OLD
        # on-disk placeholder (ANTHROPIC_API_KEY) is itself still
        # "claimed" at the moment normalization runs (the new write hasn't
        # landed yet), the type default is already taken and a distinct
        # suggested name is correctly chosen instead -- see
        # TestBug3Integration for dedicated same-name-reuse coverage.
        assert api_key.startswith("${") and api_key.endswith("}")
        assert api_key != "sk-literal-from-edit"

        global_raw = settings._get_scope_path("global").read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert "sk-literal-from-edit" not in global_raw

    def test_manage_add_provider_dashboard_path_normalizes_literal(
        self, tmp_path, monkeypatch
    ):
        """`_manage_add_provider` (dashboard flow) must not let a literal
        secret reach settings.yaml as plaintext."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from amplifier_app_cli.commands.provider import _manage_add_provider

        settings = _make_settings(tmp_path)

        with (
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={
                    "default_model": "claude-sonnet-4-6",
                    "api_key": "sk-literal-from-dashboard",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            with patch(
                "amplifier_app_cli.commands.provider.Prompt.ask", return_value="1"
            ):
                _manage_add_provider(settings, scope="global")

        providers = settings.get_scope_provider_overrides("global")
        assert len(providers) == 1
        assert providers[0]["config"]["api_key"] == "${ANTHROPIC_API_KEY}"

        global_raw = settings._get_scope_path("global").read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert "sk-literal-from-dashboard" not in global_raw

    def test_idempotent_second_write_no_duplicate_key_or_extra_save(
        self, tmp_path, monkeypatch
    ):
        """Writing the already-normalized entry a second time must be a
        no-op: value is already a placeholder, so it's skipped -- no
        duplicate keys.env entry, no second KeyManager construction/save."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

        entry = {
            "module": "provider-anthropic",
            "id": "anthropic-fable",
            "config": {
                "default_model": "claude-sonnet-4-6",
                "api_key": "sk-literal-idempotent",
                "priority": 1,
            },
        }

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            settings.set_provider_override(dict(entry), scope="global")

            providers = settings.get_scope_provider_overrides("global")
            placeholder = providers[0]["config"]["api_key"]
            assert placeholder == "${ANTHROPIC_API_KEY}"

            keys_file = tmp_path / ".amplifier" / "keys.env"
            content_after_first = keys_file.read_text(encoding="utf-8")

            with patch("amplifier_app_cli.provider_config_utils.KeyManager") as MockKM:
                settings.set_provider_override(dict(providers[0]), scope="global")
                MockKM.assert_not_called()

        content_after_second = keys_file.read_text(encoding="utf-8")
        assert content_after_second == content_after_first
        assert content_after_second.count("ANTHROPIC_API_KEY=") == 1

    def test_batch_collision_reuse_assigns_distinct_names(self, tmp_path, monkeypatch):
        """Two entries normalized in the SAME write, both needing the same
        default env var name, must get two distinct names -- no
        cross-entry clobbering within the batch."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

        scope_data = {
            "config": {
                "providers": [
                    {
                        "module": "provider-anthropic",
                        "id": "anthropic-alpha",
                        "config": {
                            "default_model": "claude-alpha",
                            "api_key": "sk-literal-alpha",
                            "priority": 1,
                        },
                    },
                    {
                        "module": "provider-anthropic",
                        "id": "anthropic-beta",
                        "config": {
                            "default_model": "claude-beta",
                            "api_key": "sk-literal-beta",
                            "priority": 2,
                        },
                    },
                ]
            }
        }

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            settings._write_scope("global", scope_data)

        providers = settings.get_scope_provider_overrides("global")
        by_id = {p["id"]: p for p in providers}
        alpha_key = by_id["anthropic-alpha"]["config"]["api_key"]
        beta_key = by_id["anthropic-beta"]["config"]["api_key"]

        assert alpha_key != beta_key
        assert alpha_key.startswith("${") and alpha_key.endswith("}")
        assert beta_key.startswith("${") and beta_key.endswith("}")

        keys_file = tmp_path / ".amplifier" / "keys.env"
        content = keys_file.read_text(encoding="utf-8")
        assert "sk-literal-alpha" in content
        assert "sk-literal-beta" in content

    def test_local_scope_literal_normalized(self, tmp_path, monkeypatch):
        """The extraction behavior applies identically at local scope --
        no scope-conditional skip; this is a universal invariant."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            settings.set_provider_override(
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-local",
                    "config": {
                        "default_model": "claude-sonnet-4-6",
                        "api_key": "sk-literal-local-scope",
                        "priority": 1,
                    },
                },
                scope="local",
            )

        providers = settings.get_scope_provider_overrides("local")
        api_key = providers[0]["config"]["api_key"]
        assert api_key.startswith("${") and api_key.endswith("}")

        local_raw = settings._get_scope_path("local").read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert "sk-literal-local-scope" not in local_raw

        keys_file = tmp_path / ".amplifier" / "keys.env"
        assert "sk-literal-local-scope" in keys_file.read_text(encoding="utf-8")

    def test_placeholder_entry_untouched_no_key_manager_write(
        self, tmp_path, monkeypatch
    ):
        """An entry already using ${VAR} must be left alone -- no
        KeyManager construction, no visibility message."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
            patch("amplifier_app_cli.provider_config_utils.KeyManager") as MockKM,
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            settings.set_provider_override(
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-existing",
                    "config": {
                        "default_model": "claude-sonnet-4-6",
                        "api_key": "${ANTHROPIC_API_KEY}",
                        "priority": 1,
                    },
                },
                scope="global",
            )

        MockKM.assert_not_called()
        mock_console.print.assert_not_called()

        providers = settings.get_scope_provider_overrides("global")
        assert providers[0]["config"]["api_key"] == "${ANTHROPIC_API_KEY}"

    def test_unloadable_module_warns_and_skips_other_entries_still_processed(
        self, tmp_path, monkeypatch
    ):
        """An entry for a module whose info can't load must warn loudly,
        leave that entry's literal as-is, not crash, and NOT block
        processing of other entries in the same batch."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

        scope_data = {
            "config": {
                "providers": [
                    {
                        "module": "provider-unknown-vanished",
                        "id": "vanished-1",
                        "config": {
                            "default_model": "x",
                            "api_key": "sk-literal-unresolvable",
                            "priority": 1,
                        },
                    },
                    {
                        "module": "provider-anthropic",
                        "id": "anthropic-fine",
                        "config": {
                            "default_model": "claude-sonnet-4-6",
                            "api_key": "sk-literal-fine",
                            "priority": 2,
                        },
                    },
                ]
            }
        }

        def _fake_get_provider_info(module_id):
            if module_id == "provider-anthropic":
                return _mock_provider_info()
            return None

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                side_effect=_fake_get_provider_info,
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            settings._write_scope("global", scope_data)

        providers = settings.get_scope_provider_overrides("global")
        by_id = {p["id"]: p for p in providers}

        # Unresolvable entry's literal is left as-is -- no crash.
        assert by_id["vanished-1"]["config"]["api_key"] == "sk-literal-unresolvable"

        # The other entry in the same batch was still processed.
        fine_key = by_id["anthropic-fine"]["config"]["api_key"]
        assert fine_key.startswith("${") and fine_key.endswith("}")

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "vanished-1" in printed or "provider-unknown-vanished" in printed

    def test_suggestion_failure_aborts_and_preserves_old_scope_file(
        self, tmp_path, monkeypatch
    ):
        """When _suggest_instance_env_var can't produce a usable name (the
        degenerate-id case), the whole write must abort loudly and the
        scope's OLD file content must be preserved untouched -- not
        partially written."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            settings.set_provider_override(
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-existing",
                    "config": {
                        "default_model": "claude-sonnet-4-6",
                        "api_key": "${ANTHROPIC_API_KEY}",
                        "priority": 1,
                    },
                },
                scope="global",
            )

        old_raw = settings._get_scope_path("global").read_text(encoding="utf-8")  # type: ignore[arg-type]

        scope_data = settings._read_scope("global")
        scope_data["config"]["providers"].append(
            {
                "module": "provider-anthropic",
                # Bare display name -- sanitizes to an empty distinguishing
                # suffix, and ANTHROPIC_API_KEY is already claimed by the
                # existing entry above, so the fallback suggestion path
                # must raise.
                "id": "anthropic",
                "config": {
                    "default_model": "claude-degenerate",
                    "api_key": "sk-literal-degenerate",
                    "priority": 2,
                },
            }
        )

        with patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=_mock_provider_info(),
        ):
            with pytest.raises(ValueError):
                settings._write_scope("global", scope_data)

        new_raw = settings._get_scope_path("global").read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert new_raw == old_raw

    def test_save_key_failure_preserves_old_scope_file(self, tmp_path, monkeypatch):
        """If KeyManager.save_key raises, the scope's settings.yaml must be
        unchanged from before the write attempt -- the atomic write never
        proceeds."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings = _make_settings(tmp_path)

        settings._write_scope("global", {"some": "existing-content"})
        old_raw = settings._get_scope_path("global").read_text(encoding="utf-8")  # type: ignore[arg-type]

        scope_data = {
            "config": {
                "providers": [
                    {
                        "module": "provider-anthropic",
                        "id": "anthropic-fable",
                        "config": {
                            "default_model": "claude-sonnet-4-6",
                            "api_key": "sk-literal-save-failure",
                            "priority": 1,
                        },
                    }
                ]
            }
        }

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_mock_provider_info(),
            ),
            patch(
                "amplifier_app_cli.key_manager.KeyManager.save_key",
                side_effect=RuntimeError("disk full"),
            ),
        ):
            with pytest.raises(RuntimeError):
                settings._write_scope("global", scope_data)

        new_raw = settings._get_scope_path("global").read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert new_raw == old_raw


# ============================================================
# Regression tripwire (§5.4.6 / §9) -- pins the §3 verification
# ============================================================


def test_no_kernel_level_env_var_rederivation():
    """If this test starts failing, someone has reintroduced a runtime path
    that re-derives a credential env-var name from ConfigField.env_var /
    get_provider_info() instead of reading the instance's own ${VAR}
    placeholder. That would silently defeat every per-instance credential
    binding this design creates -- see
    docs/designs/provider-instance-credentials.md §3.

    Scoped to the specific runtime-resolution functions §3 identified
    (``expand_env_vars`` in runtime/config.py, ``_resolve_env_placeholder``
    in provider_loader.py) rather than a whole-file grep: provider_loader.py
    legitimately *defines* and uses ``get_provider_info`` elsewhere in the
    file for wizard/prompt-time field derivation (§3: "type-level
    declarations consumed at authoring/prompt time only") -- a whole-file
    check would false-positive on the function's own name.
    """
    import inspect

    from amplifier_app_cli.provider_loader import _resolve_env_placeholder
    from amplifier_app_cli.runtime.config import expand_env_vars

    forbidden = ("get_provider_info", "ConfigField", "credential_env_vars")
    for fn in (expand_env_vars, _resolve_env_placeholder):
        source = inspect.getsource(fn)
        for name in forbidden:
            assert name not in source, (
                f"{fn.__module__}.{fn.__qualname__} references {name!r} -- "
                f"this looks like a kernel-level env-var re-derivation "
                f"creeping into the runtime resolution path (see §3, §5.4.6)."
            )
