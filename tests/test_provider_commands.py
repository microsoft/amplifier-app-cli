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
        """Default merged view should include the scope (source) for each provider.

        Since the bespoke Rich.Table was replaced with ItemRenderer, the scope
        now appears as the attribution on each item line rather than a column header.
        """
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
        # Scope appears as attribution in ItemRenderer output, not as a column header
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
        """Default merged view must show providers with scope attribution.

        Since the bespoke Rich.Table with CWD-based title was replaced with
        ItemRenderer, the title no longer includes CWD.  Instead the output
        shows providers with their scope as attribution.
        """
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
        # ItemRenderer output: section header with count + per-item lines
        assert "providers" in result.output.lower()
        assert "anthropic" in result.output.lower()

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

    def test_provider_remove_preserves_other_instance(self, tmp_path):
        """Removing unnamed provider must not remove named instances of the same module."""
        settings = _make_settings(tmp_path)
        # Write both providers directly — set_provider_override dedupes by module,
        # so we bypass it to create a realistic multi-instance scenario.
        scope_data = {
            "config": {
                "providers": [
                    {
                        "module": "provider-openai",
                        "config": {"default_model": "gpt-4o", "priority": 1},
                    },
                    {
                        "id": "openai-2",
                        "module": "provider-openai",
                        "config": {"default_model": "gpt-4-turbo", "priority": 2},
                    },
                ]
            }
        }
        settings._write_scope("global", scope_data)

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["remove", "openai"], input="y\n")

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_provider_overrides()

        # openai-2 must survive
        surviving_ids = [p.get("id") for p in providers]
        assert "openai-2" in surviving_ids, (
            f"openai-2 was wrongly removed; providers={providers}"
        )

        # unnamed openai must be gone
        unnamed = [
            p
            for p in providers
            if p.get("module") == "provider-openai" and not p.get("id")
        ]
        assert len(unnamed) == 0, (
            f"Unnamed openai was not removed; providers={providers}"
        )

    def test_provider_remove_by_id(self, tmp_path):
        """Removing a named provider instance must not remove the unnamed instance."""
        settings = _make_settings(tmp_path)
        # Write both providers directly — set_provider_override dedupes by module,
        # so we bypass it to create a realistic multi-instance scenario.
        scope_data = {
            "config": {
                "providers": [
                    {
                        "module": "provider-openai",
                        "config": {"default_model": "gpt-4o", "priority": 1},
                    },
                    {
                        "id": "openai-2",
                        "module": "provider-openai",
                        "config": {"default_model": "gpt-4-turbo", "priority": 2},
                    },
                ]
            }
        }
        settings._write_scope("global", scope_data)

        from amplifier_app_cli.commands.provider import provider

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["remove", "openai-2"], input="y\n")

        assert result.exit_code == 0, f"Output: {result.output}"
        providers = settings.get_provider_overrides()

        # openai-2 must be gone
        surviving_ids = [p.get("id") for p in providers]
        assert "openai-2" not in surviving_ids, (
            f"openai-2 was not removed; providers={providers}"
        )

        # unnamed openai must survive
        unnamed = [
            p
            for p in providers
            if p.get("module") == "provider-openai" and not p.get("id")
        ]
        assert len(unnamed) == 1, (
            f"Unnamed openai was wrongly removed; providers={providers}"
        )


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

    def test_provider_edit_accepts_scope(self, tmp_path):
        """provider edit --scope project should write the updated entry to project scope."""
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
                return_value={"default_model": "claude-opus-4-6"},
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            result = runner.invoke(
                provider, ["edit", "anthropic", "--scope", "project"]
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        # The updated entry should appear in project scope
        project_providers = settings.get_scope_provider_overrides("project")
        assert len(project_providers) == 1
        assert project_providers[0]["module"] == "provider-anthropic"
        assert project_providers[0]["config"]["default_model"] == "claude-opus-4-6"

    def test_provider_edit_scope_guard(self, tmp_path):
        """provider edit --scope project from home directory should show an error."""
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
                "amplifier_app_cli.ui.scope.is_running_from_home",
                return_value=True,
            ),
        ):
            result = runner.invoke(
                provider, ["edit", "anthropic", "--scope", "project"]
            )

        # Should fail with a usage error referencing home directory
        assert result.exit_code != 0 or "home" in result.output.lower()
        assert "home" in result.output.lower() or (
            result.exception is not None and "home" in str(result.exception).lower()
        )


class TestProviderEditMultiInstance:
    """Regression tests for the write-back-targets-wrong-entry bug.

    provider_edit() resolves the entry to edit via resolve_provider_entry()
    (priority-based, unambiguous) for the READ side, but the WRITE-back loop
    previously re-matched each scope-provider candidate independently via
    ``_find_provider_entry([p], name)`` on a single-element list -- discarding
    the priority resolution and updating whichever id-less/module-matching
    entry happened to be first in list order instead of the entry that was
    actually read and shown in the wizard.

    Confirmed via live DTU repro: 2 same-module instances, low-priority
    entry ("wrong-instance") listed first, high-priority entry
    ("correct-instance") listed second. `provider edit anthropic` correctly
    READ "correct-instance" into the wizard, but the write-back loop matched
    and overwrote "wrong-instance" instead -- leaving a duplicate
    `id: correct-instance` and corrupting "wrong-instance"'s config in place.
    """

    def _seed_two_instance_scope(self, settings: AppSettings) -> None:
        """Seed 2 same-module instances directly (bypasses set_provider_override,
        which dedupes by module and would drop one of them).

        Order matters for reproducing the bug: the low-priority ("less
        preferred") entry is listed FIRST, the high-priority ("preferred",
        lower config.priority number wins) entry is listed SECOND -- matching
        the exact DTU repro list order.
        """
        scope_data = {
            "config": {
                "providers": [
                    {
                        "id": "wrong-instance",
                        "module": "provider-anthropic",
                        "config": {
                            "default_model": "claude-old-wrong",
                            "api_key": "wrong-key",
                            "priority": 10,
                        },
                    },
                    {
                        "id": "correct-instance",
                        "module": "provider-anthropic",
                        "config": {
                            "default_model": "claude-old-correct",
                            "api_key": "correct-key",
                            "priority": 1,
                        },
                    },
                ]
            }
        }
        settings._write_scope("global", scope_data)

    def test_provider_edit_ambiguous_multi_instance_updates_resolved_entry(
        self, tmp_path
    ):
        """`provider edit anthropic` (bare type name) must update the entry
        that was actually resolved for reading (highest priority /
        lowest config.priority number = "correct-instance"), leaving the
        other same-module instance ("wrong-instance") completely untouched
        and producing no duplicate/orphaned entry.
        """
        settings = _make_settings(tmp_path)
        self._seed_two_instance_scope(settings)

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
                    "default_model": "claude-new",
                    "api_key": "new-key",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            result = runner.invoke(provider, ["edit", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"

        providers = settings.get_scope_provider_overrides("global")

        # No duplicate/orphaned entry: exactly 2 entries, unique ids.
        ids = [p.get("id") for p in providers]
        assert len(providers) == 2, f"Expected 2 entries, got: {providers}"
        assert len(set(ids)) == 2, f"Duplicate id created: {providers}"
        assert set(ids) == {"wrong-instance", "correct-instance"}

        by_id = {p["id"]: p for p in providers}

        # correct-instance (the one actually resolved+shown by the wizard)
        # must have received the edit.
        correct = by_id["correct-instance"]
        assert correct["config"]["default_model"] == "claude-new"
        assert correct["config"]["api_key"] == "new-key"
        assert correct["config"]["priority"] == 1  # priority preserved

        # wrong-instance must be completely unchanged.
        wrong = by_id["wrong-instance"]
        assert wrong["config"]["default_model"] == "claude-old-wrong"
        assert wrong["config"]["api_key"] == "wrong-key"
        assert wrong["config"]["priority"] == 10

    def test_provider_edit_by_distinct_id_unaffected(self, tmp_path):
        """Regression: editing by an explicit, distinct instance id must
        continue to update only that instance (unambiguous by definition),
        leaving the sibling instance untouched.
        """
        settings = _make_settings(tmp_path)
        self._seed_two_instance_scope(settings)

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
                    "default_model": "claude-new",
                    "api_key": "new-key",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            result = runner.invoke(provider, ["edit", "wrong-instance"])

        assert result.exit_code == 0, f"Output: {result.output}"

        providers = settings.get_scope_provider_overrides("global")
        by_id = {p["id"]: p for p in providers}
        assert len(providers) == 2

        assert by_id["wrong-instance"]["config"]["default_model"] == "claude-new"
        assert by_id["wrong-instance"]["config"]["api_key"] == "new-key"

        # correct-instance untouched
        assert (
            by_id["correct-instance"]["config"]["default_model"] == "claude-old-correct"
        )
        assert by_id["correct-instance"]["config"]["api_key"] == "correct-key"

    def test_provider_edit_single_instance_unaffected(self, tmp_path):
        """Regression: the normal single-instance case (no ambiguity at all)
        must still update the sole entry in place.
        """
        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6", "api_key": "old-key"},
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
                    "api_key": "new-key",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            result = runner.invoke(provider, ["edit", "anthropic"])

        assert result.exit_code == 0, f"Output: {result.output}"

        providers = settings.get_scope_provider_overrides("global")
        assert len(providers) == 1
        assert providers[0]["config"]["default_model"] == "claude-opus-4-6"
        assert providers[0]["config"]["api_key"] == "new-key"
        assert providers[0]["config"]["priority"] == 1


class TestManageEditProviderMultiInstance:
    """Regression tests for the identical write-back-targets-wrong-entry bug
    in `_manage_edit_provider()` (the `provider manage` interactive
    dashboard's edit path).

    Unlike `provider_edit()`, `_manage_edit_provider()` selects its entry to
    read via LIST INDEX into the merged, cross-scope `providers` list
    (`settings.get_provider_overrides()`) -- not by name-based resolution.
    Two id-less same-module instances get *collapsed into one row* by that
    merge (AppSettings._merge_provider_lists() treats the later-listed
    instance as an "overlay" that deep-merges on top of the earlier one),
    so the merged/displayed entry mostly reflects the second (higher-
    priority, "correct") instance's values -- confirmed empirically:
    seeding a low-priority instance first and high-priority instance second
    in raw settings.yaml, `get_provider_overrides()` returns exactly ONE
    entry whose fields come from the second ("correct") instance.

    The bug: after editing that single displayed/merged row, the write-back
    loop derives `name = entry.get("id") or _display_name(module_id)` (here,
    the bare id-less display name, e.g. "anthropic") and then re-matches
    `_find_provider_entry([p], name)` independently per candidate in the
    RAW per-scope list (`settings.get_scope_provider_overrides(scope)`,
    which still has BOTH instances, unmerged). With no priority tie-break
    in that per-item re-match, it hits whichever raw entry is first in list
    order -- silently overwriting the low-priority ("wrong") instance even
    though the high-priority ("correct") instance's values were what the
    user actually saw and edited.
    """

    def _seed_two_id_less_instance_scope(self, settings: AppSettings) -> None:
        """Seed 2 id-less same-module instances directly into global scope
        (bypasses set_provider_override, which dedupes by module and would
        drop one of them). Low-priority ("wrong") listed FIRST, high-
        priority ("correct") listed SECOND -- the exact list order that
        makes get_provider_overrides()'s merge favor "correct"'s values
        for the single collapsed/displayed row.
        """
        scope_data = {
            "config": {
                "providers": [
                    {
                        "module": "provider-anthropic",
                        "config": {
                            "default_model": "claude-old-wrong",
                            "api_key": "wrong-key",
                            "priority": 10,
                        },
                    },
                    {
                        "module": "provider-anthropic",
                        "config": {
                            "default_model": "claude-old-correct",
                            "api_key": "correct-key",
                            "priority": 1,
                        },
                    },
                ]
            }
        }
        settings._write_scope("global", scope_data)

    def test_merged_view_collapses_to_single_correct_leaning_row(self, tmp_path):
        """Sanity check on the premise: get_provider_overrides() must
        collapse the 2 id-less same-module instances into exactly one row,
        and that row's values must come from the higher-priority
        ("correct") instance (confirms the read-side behavior this test
        suite's write-back assertions depend on).
        """
        settings = _make_settings(tmp_path)
        self._seed_two_id_less_instance_scope(settings)

        merged = settings.get_provider_overrides()
        assert len(merged) == 1, f"Expected merge to collapse to 1 row: {merged}"
        assert merged[0]["config"]["default_model"] == "claude-old-correct"
        assert merged[0]["config"]["api_key"] == "correct-key"

    def test_manage_edit_provider_updates_correct_instance_not_first_in_scope(
        self, tmp_path
    ):
        """`_manage_edit_provider()` must update the same instance whose
        values were actually shown to the user (the higher-priority
        "correct" instance, per the merge premise above) -- not whichever
        raw per-scope entry happens to be first in list order. The other
        instance ("wrong") must be completely unchanged, and no
        duplicate/orphaned entry may be created.
        """
        from amplifier_app_cli.commands.provider import _manage_edit_provider

        settings = _make_settings(tmp_path)
        self._seed_two_id_less_instance_scope(settings)

        # Exactly as provider_manage_loop does: fetch the merged view for
        # display/selection, then edit index 0 (the only displayed row).
        providers = settings.get_provider_overrides()
        assert len(providers) == 1

        with (
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={
                    "default_model": "claude-new",
                    "api_key": "new-key",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            _manage_edit_provider(settings, "e1", providers, scope="global")

        raw = settings.get_scope_provider_overrides("global")
        assert len(raw) == 2, f"Expected 2 entries, got: {raw}"

        # No entry may have been dropped or duplicated: exactly one entry
        # keeps "wrong-key"'s pre-edit values, and exactly one entry
        # reflects the edit.
        wrong_matches = [p for p in raw if p["config"].get("api_key") == "wrong-key"]
        edited_matches = [p for p in raw if p["config"].get("api_key") == "new-key"]

        assert len(wrong_matches) == 1, (
            f"'wrong' instance was not preserved untouched: {raw}"
        )
        assert wrong_matches[0]["config"]["default_model"] == "claude-old-wrong"
        assert wrong_matches[0]["config"]["priority"] == 10

        assert len(edited_matches) == 1, (
            f"Expected exactly one entry to receive the edit: {raw}"
        )
        assert edited_matches[0]["config"]["default_model"] == "claude-new"
        # Priority preserved from the entry that was actually resolved/read
        # (the "correct" instance's priority=1), not the "wrong" instance's.
        assert edited_matches[0]["config"]["priority"] == 1

    def test_manage_edit_provider_single_instance_unaffected(self, tmp_path):
        """Regression: the normal single-instance case (no ambiguity at
        all) must still update the sole entry in place.
        """
        from amplifier_app_cli.commands.provider import _manage_edit_provider

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6", "api_key": "old-key"},
            priority=1,
        )

        providers = settings.get_provider_overrides()
        assert len(providers) == 1

        with (
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={
                    "default_model": "claude-opus-4-6",
                    "api_key": "new-key",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            _manage_edit_provider(settings, "e1", providers, scope="global")

        raw = settings.get_scope_provider_overrides("global")
        assert len(raw) == 1
        assert raw[0]["config"]["default_model"] == "claude-opus-4-6"
        assert raw[0]["config"]["api_key"] == "new-key"
        assert raw[0]["config"]["priority"] == 1

    def test_manage_edit_provider_by_distinct_id_unaffected(self, tmp_path):
        """Regression: editing an entry that carries a distinct 'id' remains
        unambiguous (id match short-circuits in _find_provider_entry) and
        must continue to update only that instance.
        """
        from amplifier_app_cli.commands.provider import _manage_edit_provider

        settings = _make_settings(tmp_path)
        scope_data = {
            "config": {
                "providers": [
                    {
                        "id": "wrong-instance",
                        "module": "provider-anthropic",
                        "config": {
                            "default_model": "claude-old-wrong",
                            "api_key": "wrong-key",
                            "priority": 10,
                        },
                    },
                    {
                        "id": "correct-instance",
                        "module": "provider-anthropic",
                        "config": {
                            "default_model": "claude-old-correct",
                            "api_key": "correct-key",
                            "priority": 1,
                        },
                    },
                ]
            }
        }
        settings._write_scope("global", scope_data)

        providers = settings.get_provider_overrides()
        assert len(providers) == 2  # distinct ids -> not collapsed by merge

        # Pick whichever index corresponds to "correct-instance" to edit it.
        idx = next(
            i for i, p in enumerate(providers, 1) if p.get("id") == "correct-instance"
        )

        with (
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={
                    "default_model": "claude-new",
                    "api_key": "new-key",
                },
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
        ):
            _manage_edit_provider(settings, f"e{idx}", providers, scope="global")

        raw = settings.get_scope_provider_overrides("global")
        by_id = {p["id"]: p for p in raw}
        assert len(raw) == 2

        assert by_id["correct-instance"]["config"]["default_model"] == "claude-new"
        assert by_id["correct-instance"]["config"]["api_key"] == "new-key"

        # wrong-instance untouched
        assert by_id["wrong-instance"]["config"]["default_model"] == "claude-old-wrong"
        assert by_id["wrong-instance"]["config"]["api_key"] == "wrong-key"


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


# ============================================================
# Fix 4: -p flag matches provider instance id/mount name
# ============================================================


def _make_run_cli_p_flag(captured: list) -> "click.Group":
    """Create a minimal Click CLI with the run command registered.

    ``captured`` is cleared and replaced with the providers list that
    ``execute_single`` receives, i.e. after the provider selection logic has
    run and the selected provider has been promoted to priority 0.
    """
    import click
    from amplifier_app_cli.commands.run import register_run_command
    from unittest.mock import AsyncMock

    cli = click.Group("test")

    async def _execute_single(prompt, config_data, *args, **kwargs):
        captured[:] = list(config_data.get("providers", []))

    register_run_command(
        cli,
        interactive_chat=AsyncMock(),
        execute_single=_execute_single,
        get_module_search_paths=lambda: [],
        check_first_run=lambda: False,
        prompt_first_run_init=lambda c: False,
    )
    return cli


def _invoke_run_p_flag(providers_list: list, p_flag: str):
    """Run ``amplifier run -p <p_flag> --output-format json hello`` via CliRunner.

    Mocks ``resolve_config`` to inject *providers_list* and suppresses the
    update-check coroutine.  Returns ``(CliResult, captured_providers)`` where
    *captured_providers* contains the providers as passed to ``execute_single``
    — the selected provider will have ``config["priority"] == 0``.
    """
    from click.testing import CliRunner
    from unittest.mock import AsyncMock, MagicMock, patch

    captured: list = []
    cli = _make_run_cli_p_flag(captured)

    fake_config: dict = {"providers": list(providers_list)}
    fake_bundle = MagicMock()
    fake_bundle.mount_plan = {"providers": list(providers_list)}

    with (
        patch(
            "amplifier_app_cli.commands.run.create_config_manager",
            return_value=MagicMock(get_merged_settings=lambda: {}),
        ),
        patch(
            "amplifier_app_cli.commands.run.resolve_config",
            return_value=(fake_config, fake_bundle),
        ),
        patch(
            "amplifier_app_cli.utils.startup_checker.check_and_notify",
            new_callable=AsyncMock,
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["run", "-p", p_flag, "--output-format", "json", "hello"]
        )

    return result, captured


class TestRunPFlag:
    """Tests for -p/--provider flag matching provider instance id/mount name.

    Validates Fix 4 from UPSTREAM-FIXES.md: ``-p`` now does a two-pass search —
    Pass 1 matches on ``id`` or ``instance_id``; Pass 2 falls back to module type
    (``provider-{name}``) for backward compatibility.
    """

    def test_run_p_flag_matches_instance_id(self):
        """Pass 1: -p <id> selects the first of two same-module providers by instance id."""
        providers = [
            {
                "module": "provider-anthropic",
                "id": "spark2-gemma",
                "config": {"priority": 2},
            },
            {
                "module": "provider-anthropic",
                "id": "r11-gemma",
                "config": {"priority": 3},
            },
        ]
        result, captured = _invoke_run_p_flag(providers, "spark2-gemma")

        assert result.exit_code == 0, (
            f"Expected success, got exit {result.exit_code}: {result.output}"
        )
        selected = next(p for p in captured if p.get("id") == "spark2-gemma")
        other = next(p for p in captured if p.get("id") == "r11-gemma")
        assert selected["config"]["priority"] == 0, (
            f"spark2-gemma should be promoted to priority 0, "
            f"got {selected['config']['priority']}"
        )
        assert other["config"]["priority"] == 3, (
            f"r11-gemma should keep original priority 3, "
            f"got {other['config']['priority']}"
        )

    def test_run_p_flag_matches_instance_id_non_first_position(self):
        """Pass 1: -p <id> selects the second of two same-module providers by instance id."""
        providers = [
            {
                "module": "provider-anthropic",
                "id": "spark2-gemma",
                "config": {"priority": 2},
            },
            {
                "module": "provider-anthropic",
                "id": "r11-gemma",
                "config": {"priority": 3},
            },
        ]
        result, captured = _invoke_run_p_flag(providers, "r11-gemma")

        assert result.exit_code == 0, (
            f"Expected success, got exit {result.exit_code}: {result.output}"
        )
        selected = next(p for p in captured if p.get("id") == "r11-gemma")
        other = next(p for p in captured if p.get("id") == "spark2-gemma")
        assert selected["config"]["priority"] == 0, (
            f"r11-gemma should be promoted to priority 0, "
            f"got {selected['config']['priority']}"
        )
        assert other["config"]["priority"] == 2, (
            f"spark2-gemma should keep original priority 2, "
            f"got {other['config']['priority']}"
        )

    def test_run_p_flag_falls_back_to_module_type(self):
        """Pass 2 (fallback): -p anthropic still works for a single provider with no id."""
        providers = [
            {"module": "provider-anthropic", "config": {"priority": 1}},
        ]
        result, captured = _invoke_run_p_flag(providers, "anthropic")

        assert result.exit_code == 0, (
            f"Regression: -p anthropic no longer works via module-type fallback: "
            f"{result.output}"
        )
        assert captured[0]["config"]["priority"] == 0, (
            f"Provider should be promoted to priority 0, "
            f"got {captured[0]['config']['priority']}"
        )

    def test_run_p_flag_end_to_end_via_resolve_bundle_config(self):
        """Pass 1 works on providers processed by _map_id_to_instance_id (real data flow).

        ``_map_id_to_instance_id`` copies ``id`` → ``instance_id`` without stripping
        ``id``, so both fields co-exist on resolved entries.  This test uses the
        actual mapping function to produce realistic provider dicts and verifies that
        Pass 1 matches correctly on the resolved data, not just on synthetic dicts.
        """
        from amplifier_app_cli.runtime.config import _map_id_to_instance_id

        raw_providers = [
            {
                "module": "provider-vllm",
                "id": "r11-gemma",
                "config": {"base_url": "http://r11:8000/v1", "priority": 1},
            },
            {
                "module": "provider-vllm",
                "id": "spark2-gemma",
                "config": {"base_url": "http://spark2:8000/v1", "priority": 2},
            },
        ]
        resolved = _map_id_to_instance_id(raw_providers)

        # Confirm the mapping preserves id AND adds instance_id
        assert resolved[0].get("id") == "r11-gemma"
        assert resolved[0].get("instance_id") == "r11-gemma"

        result, captured = _invoke_run_p_flag(resolved, "r11-gemma")

        assert result.exit_code == 0, (
            f"Expected success, got exit {result.exit_code}: {result.output}"
        )
        r11 = next(p for p in captured if p.get("instance_id") == "r11-gemma")
        spark2 = next(p for p in captured if p.get("instance_id") == "spark2-gemma")
        assert r11["config"]["priority"] == 0, (
            f"r11-gemma should be promoted to priority 0, "
            f"got {r11['config']['priority']}"
        )
        assert spark2["config"]["priority"] == 2, (
            f"spark2-gemma should keep original priority 2, "
            f"got {spark2['config']['priority']}"
        )


class TestFindProviderEntryPrioritySelection:
    """BUG 3 regression: ``_find_provider_entry()`` is the third function with
    the same first-match-wins anti-pattern already fixed twice in this repo
    (PR #214, for ``ProviderManager.get_provider_config()`` and
    ``commands/routing.py::_get_provider_config()``).

    It matches on 'id' first (unambiguous, ids are unique), then falls back to
    matching on the bare/full 'module' name. Nothing enforces that only one
    id-less (or display-name-colliding) instance of a module exists -- if a
    user hand-edits settings.yaml to create 2+ such instances, the function
    silently returns whichever is first in list order instead of being
    priority-aware, exactly like the two sibling bugs fixed in PR #214.

    Called from provider_remove(), provider_edit(), and provider_test() --
    all 3 CLI-facing sites where a user types a bare provider type name.
    """

    def test_selects_highest_priority_instance_not_first_in_list(self):
        """Two id-less instances of the same module ('anthropic'), with the
        LOWER priority instance (priority=5) listed BEFORE the HIGHER
        priority instance (priority=1, lower number = higher precedence).
        Must resolve to the priority=1 instance, not whichever is first in
        list order.
        """
        from amplifier_app_cli.commands.provider import _find_provider_entry

        providers = [
            {
                "module": "provider-anthropic",
                "config": {"priority": 5, "default_model": "wrong-instance"},
            },
            {
                "module": "provider-anthropic",
                "config": {"priority": 1, "default_model": "correct-instance"},
            },
        ]

        entry = _find_provider_entry(providers, "anthropic")

        assert entry is not None, "Expected a matching entry, got None"
        assert entry["config"].get("default_model") == "correct-instance", (
            "_find_provider_entry() must resolve to the highest-priority "
            f"(lowest priority number) instance, got: {entry}"
        )

    def test_selects_highest_priority_instance_when_matched_by_full_module_name(
        self,
    ):
        """Same ambiguity, but matching via the full module id
        ('provider-openai') rather than the bare display name."""
        from amplifier_app_cli.commands.provider import _find_provider_entry

        providers = [
            {
                "module": "provider-openai",
                "config": {"priority": 3, "default_model": "wrong-instance"},
            },
            {
                "module": "provider-openai",
                "config": {"priority": 1, "default_model": "correct-instance"},
            },
        ]

        entry = _find_provider_entry(providers, "provider-openai")

        assert entry is not None
        assert entry["config"].get("default_model") == "correct-instance", (
            f"Expected the priority=1 instance, got: {entry}"
        )

    def test_id_match_is_unambiguous_and_unaffected_by_priority(self):
        """Regression guard: matching by an explicit 'id' is unambiguous by
        definition (ids are unique) and must return that exact entry
        regardless of priority values on other entries of the same module.
        """
        from amplifier_app_cli.commands.provider import _find_provider_entry

        providers = [
            {
                "module": "provider-openai",
                "config": {"priority": 1, "default_model": "unnamed-instance"},
            },
            {
                "id": "openai-2",
                "module": "provider-openai",
                "config": {"priority": 99, "default_model": "named-instance"},
            },
        ]

        entry = _find_provider_entry(providers, "openai-2")

        assert entry is not None
        assert entry.get("id") == "openai-2"
        assert entry["config"].get("default_model") == "named-instance", (
            f"Expected the id-matched entry, got: {entry}"
        )

    def test_returns_none_when_no_match_found(self):
        """Regression guard: the 'genuinely not found' contract must be
        preserved as None -- all 3 CLI call sites branch on this explicitly
        for their error messages.
        """
        from amplifier_app_cli.commands.provider import _find_provider_entry

        providers = [
            {
                "module": "provider-openai",
                "config": {"priority": 1, "default_model": "gpt-5"},
            },
        ]

        entry = _find_provider_entry(providers, "anthropic")

        assert entry is None, f"Expected None for unconfigured module, got: {entry}"
