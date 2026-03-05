"""Tests for interactive management commands (Tasks 1-4).

Tests provider manage, routing manage, init dashboard, and first-run updates.
"""

from pathlib import Path
from unittest.mock import patch

import yaml
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


def _make_matrix_dir(tmp_path: Path) -> Path:
    """Create a mock routing matrix cache directory with matrix files."""
    cache_dir = (
        tmp_path / "cache" / "amplifier-bundle-routing-matrix-abc123" / "routing"
    )
    cache_dir.mkdir(parents=True)

    balanced = {
        "name": "balanced",
        "description": "Quality/cost balance for mixed workloads.",
        "updated": "2026-02-28",
        "roles": {
            "coding": {
                "description": "Code generation",
                "candidates": [
                    {"provider": "anthropic", "model": "claude-sonnet-*"},
                    {"provider": "openai", "model": "gpt-5.*"},
                ],
            },
            "fast": {
                "description": "Quick tasks",
                "candidates": [
                    {"provider": "anthropic", "model": "claude-haiku-*"},
                    {"provider": "openai", "model": "gpt-5-mini"},
                ],
            },
        },
    }
    (cache_dir / "balanced.yaml").write_text(yaml.dump(balanced))

    economy = {
        "name": "economy",
        "description": "Cost-optimized routing.",
        "updated": "2026-02-28",
        "roles": {
            "coding": {
                "description": "Code generation",
                "candidates": [
                    {"provider": "openai", "model": "gpt-4.*"},
                ],
            },
        },
    }
    (cache_dir / "economy.yaml").write_text(yaml.dump(economy))

    return tmp_path / "cache"


# ============================================================
# Task 1: provider manage
# ============================================================


class TestProviderManage:
    """Tests for `amplifier provider manage` command."""

    def test_provider_manage_command_exists(self):
        """manage should be registered on the provider group."""
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "manage" in command_names

    def test_provider_manage_loop_displays_no_providers(self, tmp_path):
        """With no providers, manage loop should show 'No providers configured'."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)

        # Capture output by using Rich Console with string IO
        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with patch("amplifier_app_cli.commands.provider.console", test_console):
            with patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt:
                # Simulate user pressing 'd' for done immediately
                MockPrompt.ask.return_value = "d"
                provider_manage_loop(settings)

        rendered = output.getvalue()
        assert "No providers configured" in rendered

    def test_provider_manage_loop_displays_providers(self, tmp_path):
        """With providers configured, manage loop should show them in a table."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )
        _seed_provider(
            settings,
            "provider-openai",
            {"default_model": "gpt-4o"},
            priority=2,
        )

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with patch("amplifier_app_cli.commands.provider.console", test_console):
            with patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt:
                MockPrompt.ask.return_value = "d"
                provider_manage_loop(settings)

        rendered = output.getvalue()
        assert "anthropic" in rendered.lower()
        assert "openai" in rendered.lower()

    def test_provider_manage_loop_shows_star_for_primary(self, tmp_path):
        """Primary provider (lowest priority) should have star marker."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )
        _seed_provider(
            settings,
            "provider-openai",
            {"default_model": "gpt-4o"},
            priority=2,
        )

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with patch("amplifier_app_cli.commands.provider.console", test_console):
            with patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt:
                MockPrompt.ask.return_value = "d"
                provider_manage_loop(settings)

        rendered = output.getvalue()
        assert "★" in rendered

    def test_provider_manage_cli_invocation(self, tmp_path):
        """CLI command `provider manage` should invoke the manage loop."""
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        ):
            result = runner.invoke(provider, ["manage"], input="d\n")

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "No providers configured" in result.output

    # --------------------------------------------------------
    # Scope integration tests (task-2-provider-manage-scope)
    # --------------------------------------------------------

    def test_scope_indicator_displayed(self, tmp_path):
        """Scope indicator should appear after the provider table."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.provider.console", test_console),
            patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt,
            patch(
                "amplifier_app_cli.commands.provider.print_scope_indicator"
            ) as mock_indicator,
        ):
            MockPrompt.ask.return_value = "d"
            provider_manage_loop(settings)

        # print_scope_indicator should have been called
        mock_indicator.assert_called()

    def test_scope_param_accepted(self, tmp_path):
        """provider_manage_loop should accept a scope parameter."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.provider.console", test_console),
            patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt,
        ):
            MockPrompt.ask.return_value = "d"
            # Should not raise - scope param should be accepted
            provider_manage_loop(settings, scope="project")

    def test_scope_return_value(self, tmp_path):
        """provider_manage_loop should return the current scope."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.provider.console", test_console),
            patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt,
        ):
            MockPrompt.ask.return_value = "d"
            result = provider_manage_loop(settings, scope="project")

        assert result == "project"

    def test_w_action_visible_outside_home(self, tmp_path):
        """[w] action should appear when not running from home directory."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.provider.console", test_console),
            patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt,
            patch(
                "amplifier_app_cli.commands.provider.is_scope_change_available",
                return_value=True,
            ),
        ):
            MockPrompt.ask.return_value = "d"
            provider_manage_loop(settings)

        rendered = output.getvalue()
        assert "Change write scope" in rendered

    def test_w_action_hidden_at_home(self, tmp_path):
        """[w] action should be hidden when running from home directory."""
        from amplifier_app_cli.commands.provider import provider_manage_loop

        settings = _make_settings(tmp_path)

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.provider.console", test_console),
            patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt,
            patch(
                "amplifier_app_cli.commands.provider.is_scope_change_available",
                return_value=False,
            ),
        ):
            MockPrompt.ask.return_value = "d"
            provider_manage_loop(settings)

        rendered = output.getvalue()
        assert "Change write scope" not in rendered

    def test_reorder_writes_to_scope(self, tmp_path):
        """Reorder should write to the current scope, not hardcoded global."""
        from amplifier_app_cli.commands.provider import _manage_reorder_providers

        settings = _make_settings(tmp_path)
        # Seed providers into project scope
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )
        _seed_provider(
            settings,
            "provider-openai",
            {"default_model": "gpt-4o"},
            priority=2,
        )

        providers = settings.get_provider_overrides()

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.provider.console", test_console),
            patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt,
        ):
            MockPrompt.ask.return_value = "2 1"
            _manage_reorder_providers(settings, providers, scope="project")

        # Verify written to project scope
        project_settings = settings._read_scope("project")
        project_providers = project_settings.get("config", {}).get("providers", [])
        assert len(project_providers) == 2

    def test_add_provider_shows_global_info(self, tmp_path):
        """_manage_add_provider should show info that credentials are saved to global."""
        from amplifier_app_cli.commands.provider import _manage_add_provider

        settings = _make_settings(tmp_path)

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.provider.console", test_console),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
            patch("amplifier_app_cli.commands.provider.Prompt") as MockPrompt,
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                return_value={"default_model": "test-model"},
            ),
        ):
            MockPM.return_value.list_providers.return_value = [
                ("provider-test", "Test Provider", "A test provider")
            ]
            MockPrompt.ask.return_value = "1"
            _manage_add_provider(settings)

        rendered = output.getvalue()
        assert "global" in rendered.lower()

    def test_cli_scope_option_accepted(self, tmp_path):
        """CLI command `provider manage --scope=project` should be accepted."""
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
                "amplifier_app_cli.commands.provider.validate_scope_cli",
            ),
        ):
            result = runner.invoke(provider, ["manage", "--scope=project"], input="d\n")

        assert result.exit_code == 0, f"Output: {result.output}"


# ============================================================
# Task 2: routing manage
# ============================================================


class TestRoutingManage:
    """Tests for `amplifier routing manage` command."""

    def test_routing_manage_command_exists(self):
        """manage should be registered on the routing group."""
        from amplifier_app_cli.commands.routing import routing_group

        command_names = [c.name for c in routing_group.commands.values()]
        assert "manage" in command_names

    def test_routing_manage_loop_displays_active_matrix(self, tmp_path):
        """Routing manage loop should show the active routing matrix name."""
        from amplifier_app_cli.commands.routing import routing_manage_loop

        settings = _make_settings(tmp_path)
        cache_dir = _make_matrix_dir(tmp_path)

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.routing.console", test_console),
            patch("amplifier_app_cli.commands.routing.Prompt") as MockPrompt,
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            MockPrompt.ask.return_value = "d"
            routing_manage_loop(settings)

        rendered = output.getvalue()
        assert "balanced" in rendered.lower()


# ============================================================
# Task 3: init dashboard
# ============================================================


class TestInitDashboard:
    """Tests for `amplifier init` combined dashboard."""

    def test_init_command_exists(self):
        """init_cmd should be importable and be a Click command."""
        from amplifier_app_cli.commands.init import init_cmd

        assert init_cmd is not None
        import click

        assert isinstance(init_cmd, click.Command)

    def test_init_cmd_exported(self):
        """init_cmd should be in commands/__init__.py exports."""
        from amplifier_app_cli.commands import __all__ as cmd_exports

        assert "init_cmd" in cmd_exports

    def test_init_dashboard_shows_combined_view(self, tmp_path):
        """Dashboard should show both provider summary and routing info."""
        from amplifier_app_cli.commands.init import init_dashboard_loop

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            "provider-anthropic",
            {"default_model": "claude-sonnet-4-6"},
            priority=1,
        )
        cache_dir = _make_matrix_dir(tmp_path)

        from io import StringIO

        from rich.console import Console

        output = StringIO()
        test_console = Console(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.init.console", test_console),
            patch("amplifier_app_cli.commands.init.Prompt") as MockPrompt,
            patch(
                "amplifier_app_cli.commands.init._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            MockPrompt.ask.return_value = "d"
            init_dashboard_loop(settings)

        rendered = output.getvalue()
        # Should show header
        assert "Amplifier Setup" in rendered
        # Should show provider info
        assert "anthropic" in rendered.lower()
        # Should show routing info
        assert "balanced" in rendered.lower()


# ============================================================
# Task 4: First-run updates
# ============================================================


class TestFirstRunUpdates:
    """Tests for updated first-run detection."""

    def test_prompt_first_run_references_init(self):
        """First-run prompt should reference `amplifier init`."""
        import inspect

        from amplifier_app_cli.commands.init import prompt_first_run_init

        source = inspect.getsource(prompt_first_run_init)
        assert "amplifier init" in source

    def test_init_command_removed_test_is_gone(self):
        """The old test_init_command_removed assertion should no longer hold.

        init_cmd IS now exported, so the old assertion would fail.
        This test validates the new state.
        """
        from amplifier_app_cli.commands import __all__ as cmd_exports

        # init_cmd should NOW be in exports (opposite of old test)
        assert "init_cmd" in cmd_exports
