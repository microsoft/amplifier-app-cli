"""Tests for redesigned provider commands (Tasks 7-11).

Tests provider add, list, remove, edit, test commands and first-run detection.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

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
    """Seed a provider entry into global settings for testing."""
    entry = {
        "module": module,
        "config": {**config, "priority": priority},
    }
    if provider_id is not None:
        entry["id"] = provider_id
    settings.set_provider_override(entry, scope="global")


# ============================================================
# Task 7: provider add
# ============================================================


class TestProviderAdd:
    """Tests for `amplifier provider add` command."""

    def test_provider_add_command_exists(self):
        """provider_add should be registered on the provider group."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "add" in command_names

    def test_provider_add_saves_entry_to_settings(self, tmp_path):
        """provider add should write a provider entry to config.providers in settings."""
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)

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

            result = runner.invoke(provider, ["add", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show confirmation
        assert "Provider added" in result.output or "anthropic" in result.output

        # Verify entry written to settings
        providers = settings.get_provider_overrides()
        assert len(providers) >= 1
        added = providers[0]
        assert added["module"] == "provider-anthropic"
        assert added["config"]["default_model"] == "claude-sonnet-4-6"

    def test_provider_add_assigns_priority(self, tmp_path):
        """First provider gets priority 1, subsequent get max+1."""
        settings = _make_settings(tmp_path)

        # Seed an existing provider with priority 1
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
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
                return_value={
                    "default_model": "gpt-4o",
                    "api_key": "${OPENAI_API_KEY}",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-openai", "OpenAI", "OpenAI provider"),
            ]
            MockPM.return_value = mock_pm

            result = runner.invoke(provider, ["add", "openai"])

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_provider_overrides()
        # The new provider should have priority = max_existing + 1 = 2
        # Find openai entry
        openai_entry = next(
            (p for p in providers if p["module"] == "provider-openai"), None
        )
        assert openai_entry is not None
        assert openai_entry["config"]["priority"] >= 2

    def test_provider_add_multi_instance_prompts_for_id(self, tmp_path):
        """Adding a second provider of same type should include an id field."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
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
                return_value={
                    "default_model": "claude-opus-4-6",
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

            # Provide "anthropic-2" as the id when prompted
            result = runner.invoke(
                provider, ["add", "anthropic"], input="anthropic-2\n"
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_provider_overrides()
        # Find the entry with the id
        id_entries = [p for p in providers if p.get("id") == "anthropic-2"]
        assert len(id_entries) == 1


# ============================================================
# Task 8: provider list (redesigned)
# ============================================================


class TestProviderList:
    """Tests for redesigned `amplifier provider list`."""

    def test_provider_list_shows_configured_providers(self, tmp_path):
        """provider list should show configured providers in a table."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )
        _seed_provider(
            settings, "provider-openai", {"default_model": "gpt-4o"}, priority=2
        )

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "anthropic" in result.output.lower()
        assert "openai" in result.output.lower()

    def test_provider_list_shows_star_for_primary(self, tmp_path):
        """Primary provider (lowest priority) should be marked with star."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )
        _seed_provider(
            settings, "provider-openai", {"default_model": "gpt-4o"}, priority=2
        )

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should have a star marker
        assert "★" in result.output or "primary" in result.output.lower()

    def test_provider_list_empty_shows_help(self, tmp_path):
        """When no providers configured, show helpful message."""
        settings = _make_settings(tmp_path)

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "No providers configured" in result.output
        assert "provider add" in result.output

    # ---- Task 5: --scope flag tests ----

    def test_provider_list_shows_source_column(self, tmp_path):
        """Default merged view should include a 'Source' column showing which scope
        contributed each provider."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
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
        ):
            result = runner.invoke(provider, ["list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Source" in result.output
        assert "global" in result.output.lower()

    def test_provider_list_scope_filter(self, tmp_path):
        """provider list --scope project should show only providers from project scope."""
        settings = _make_settings(tmp_path)
        # Seed a provider in global scope
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )
        # Seed a different provider in project scope
        project_settings = settings._read_scope("project")
        if "config" not in project_settings:
            project_settings["config"] = {}
        project_settings["config"]["providers"] = [
            {
                "module": "provider-openai",
                "config": {"default_model": "gpt-4o", "priority": 1},
            }
        ]
        settings._write_scope("project", project_settings)

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["list", "--scope", "project"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Only the project provider should appear
        assert "openai" in result.output.lower()
        # The global provider should NOT appear (it's not in project scope)
        assert "anthropic" not in result.output.lower()

    # ---- Task 5 spec-compliance tests ----

    def test_provider_list_default_title_includes_cwd(self, tmp_path):
        """Default merged view title must include the current working directory."""
        import os

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
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
        ):
            result = runner.invoke(provider, ["list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        cwd = os.getcwd()
        # Title must contain "effective from <cwd>"
        assert "effective from" in result.output
        assert cwd in result.output

    def test_provider_list_merged_view_no_status_column(self, tmp_path):
        """Default merged view must NOT include a 'Status' column."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
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
        ):
            result = runner.invoke(provider, ["list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # "Status" column header must not appear in merged view
        assert "Status" not in result.output

    def test_provider_list_single_scope_empty_includes_path(self, tmp_path):
        """Single-scope empty state must include the scope path."""
        settings = _make_settings(tmp_path)

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["list", "--scope", "global"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Rich may wrap long paths across lines — join to check as one string
        output_joined = result.output.replace("\n", "")
        scope_path = settings._get_scope_path("global")
        assert str(scope_path) in output_joined
        assert "No providers in global scope" in result.output

    def test_provider_list_scope_guard(self, tmp_path):
        """provider list --scope project from home directory should show an error."""
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
                "amplifier_app_cli.ui.scope.is_running_from_home",
                return_value=True,
            ),
        ):
            result = runner.invoke(provider, ["list", "--scope", "project"])

        # Should fail with a usage error referencing home directory
        assert result.exit_code != 0 or "home" in result.output.lower()
        assert "home" in result.output.lower() or (
            result.exception is not None and "home" in str(result.exception).lower()
        )


# ============================================================
# Task 9: provider remove and provider edit
# ============================================================


class TestProviderRemove:
    """Tests for `amplifier provider remove`."""

    def test_provider_remove_command_exists(self):
        """provider_remove should be registered on the provider group."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "remove" in command_names

    def test_provider_remove_deletes_entry(self, tmp_path):
        """provider remove should delete the entry from settings."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
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
        ):
            # Confirm removal with 'y'
            result = runner.invoke(provider, ["remove", "anthropic"], input="y\n")

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_provider_overrides()
        anthropic_entries = [
            p for p in providers if p["module"] == "provider-anthropic"
        ]
        assert len(anthropic_entries) == 0

    def test_provider_remove_not_found(self, tmp_path):
        """provider remove should show error for unknown provider."""
        settings = _make_settings(tmp_path)

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["remove", "nonexistent"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "not found" in result.output.lower()


class TestProviderEdit:
    """Tests for `amplifier provider edit`."""

    def test_provider_edit_command_exists(self):
        """provider_edit should be registered on the provider group."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "edit" in command_names

    def test_provider_edit_calls_configure_with_existing(self, tmp_path):
        """provider edit should call configure_provider with existing config as defaults."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
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
                return_value={
                    "default_model": "claude-opus-4-6",
                },
            ) as mock_configure,
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            result = runner.invoke(provider, ["edit", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # configure_provider should have been called with existing_config
        mock_configure.assert_called_once()
        call_kwargs = mock_configure.call_args
        assert call_kwargs[1].get("existing_config") is not None or (
            len(call_kwargs[0]) > 0  # positional args
        )


# ============================================================
# Task 10: provider test
# ============================================================


class TestProviderTest:
    """Tests for `amplifier provider test`."""

    def test_provider_test_command_exists(self):
        """provider_test should be registered on the provider group."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "test" in command_names

    def test_provider_test_shows_success(self, tmp_path):
        """provider test should show success for working provider."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )

        from amplifier_app_cli.commands.provider import provider

        mock_model = MagicMock()
        mock_model.id = "claude-sonnet-4-6"

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_models",
                return_value=[mock_model],
            ),
        ):
            result = runner.invoke(provider, ["test", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert (
            "✓" in result.output
            or "pass" in result.output.lower()
            or "ok" in result.output.lower()
        )

    def test_provider_test_shows_failure(self, tmp_path):
        """provider test should show failure gracefully."""
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
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
                "amplifier_app_cli.commands.provider.get_provider_models",
                side_effect=Exception("Connection refused"),
            ),
        ):
            result = runner.invoke(provider, ["test", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert (
            "✗" in result.output
            or "fail" in result.output.lower()
            or "error" in result.output.lower()
        )


# ============================================================
# Task 11: Remove old commands, first-run detection
# ============================================================


class TestOldCommandsRemoved:
    """Tests that old commands are removed."""

    def test_init_command_exists(self):
        """amplifier init should be registered as combined dashboard."""

        # init is a top-level command, not on provider group
        # Check that init_cmd IS in commands/__init__.py exports
        from amplifier_app_cli.commands import __all__ as cmd_exports

        assert "init_cmd" in cmd_exports

    def test_provider_use_removed(self):
        """provider use command should no longer exist."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "use" not in command_names

    def test_provider_current_removed(self):
        """provider current command should no longer exist."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "current" not in command_names

    def test_provider_reset_removed(self):
        """provider reset command should no longer exist."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "reset" not in command_names

    def test_provider_install_still_exists(self):
        """provider install should still exist."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "install" in command_names

    def test_provider_models_still_exists(self):
        """provider models should still exist."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "models" in command_names


class TestFirstRunDetection:
    """Tests for first-run detection triggering provider add."""

    def test_check_first_run_still_exists(self):
        """check_first_run function should still exist."""
        from amplifier_app_cli.commands.init import check_first_run

        assert callable(check_first_run)

    def test_first_run_references_provider_add(self):
        """When no providers configured, first-run should reference provider add flow."""
        import inspect
        from amplifier_app_cli.commands.init import prompt_first_run_init

        source = inspect.getsource(prompt_first_run_init)
        # Should reference provider add, not old init command
        assert "provider" in source.lower() and "add" in source.lower()
