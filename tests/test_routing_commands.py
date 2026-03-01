"""Tests for routing commands (Tasks 12-15).

Tests routing settings, list, use, and show commands.
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
                    {"provider": "github-copilot", "model": "claude-sonnet-*"},
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
                    {"provider": "github-copilot", "model": "gpt-4.*"},
                    {"provider": "ollama", "model": "*"},
                ],
            },
            "fast": {
                "description": "Quick tasks",
                "candidates": [
                    {"provider": "github-copilot", "model": "gpt-4.*"},
                ],
            },
        },
    }
    (cache_dir / "economy.yaml").write_text(yaml.dump(economy))

    return tmp_path / "cache"


def _seed_providers(settings: AppSettings) -> None:
    """Seed provider entries for routing resolution tests."""
    scope_settings = settings._read_scope("global")
    scope_settings["config"] = {
        "providers": [
            {"module": "provider-anthropic", "config": {"priority": 1}},
            {"module": "provider-openai", "config": {"priority": 2}},
        ]
    }
    settings._write_scope("global", scope_settings)


# ============================================================
# Task 12: AppSettings routing methods
# ============================================================


class TestRoutingSettings:
    """Tests for AppSettings routing config methods."""

    def test_get_routing_config_empty(self, tmp_path):
        """Returns {} when no routing section exists."""
        settings = _make_settings(tmp_path)
        result = settings.get_routing_config()
        assert result == {}

    def test_get_routing_config_reads_matrix(self, tmp_path):
        """Returns routing config when set."""
        settings = _make_settings(tmp_path)
        # Write routing config directly
        scope_settings = {"routing": {"matrix": "economy"}}
        settings._write_scope("global", scope_settings)

        result = settings.get_routing_config()
        assert result == {"matrix": "economy"}

    def test_set_routing_matrix_writes(self, tmp_path):
        """set_routing_matrix writes routing.matrix to correct scope."""
        settings = _make_settings(tmp_path)
        settings.set_routing_matrix("economy", scope="global")

        result = settings.get_routing_config()
        assert result["matrix"] == "economy"

    def test_set_routing_matrix_project_scope(self, tmp_path):
        """set_routing_matrix respects scope parameter."""
        settings = _make_settings(tmp_path)
        settings.set_routing_matrix("quality", scope="project")

        # Read project scope directly
        project_settings = settings._read_scope("project")
        assert project_settings["routing"]["matrix"] == "quality"

    def test_set_routing_matrix_preserves_other_settings(self, tmp_path):
        """set_routing_matrix doesn't clobber other routing settings."""
        settings = _make_settings(tmp_path)
        # Write some existing settings
        settings._write_scope(
            "global",
            {
                "routing": {"overrides": {"coding": "special"}},
                "bundle": {"active": "foundation"},
            },
        )

        settings.set_routing_matrix("economy", scope="global")

        scope_settings = settings._read_scope("global")
        assert scope_settings["routing"]["matrix"] == "economy"
        assert scope_settings["routing"]["overrides"] == {"coding": "special"}
        assert scope_settings["bundle"]["active"] == "foundation"


# ============================================================
# Task 13: routing list
# ============================================================


class TestRoutingList:
    """Tests for `amplifier routing list` command."""

    def test_routing_list_shows_matrices(self, tmp_path):
        """routing list shows available matrices in a table."""
        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        _seed_providers(settings)

        from amplifier_app_cli.commands.routing import routing_group

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.routing._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            result = runner.invoke(routing_group, ["list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "balanced" in result.output
        assert "economy" in result.output

    def test_routing_list_marks_active(self, tmp_path):
        """Active matrix gets arrow indicator."""
        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        _seed_providers(settings)
        settings.set_routing_matrix("economy", scope="global")

        from amplifier_app_cli.commands.routing import routing_group

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.routing._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            result = runner.invoke(routing_group, ["list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # The active matrix should have an arrow indicator
        assert "\u2192" in result.output or "→" in result.output


# ============================================================
# Task 14: routing use
# ============================================================


class TestRoutingUse:
    """Tests for `amplifier routing use` command."""

    def test_routing_use_writes_settings(self, tmp_path):
        """routing use writes the correct matrix name to settings."""
        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        _seed_providers(settings)

        from amplifier_app_cli.commands.routing import routing_group

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.routing._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            result = runner.invoke(routing_group, ["use", "economy"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Verify settings were written
        routing_config = settings.get_routing_config()
        assert routing_config["matrix"] == "economy"

    def test_routing_use_invalid_name(self, tmp_path):
        """routing use shows error for unknown matrix."""
        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)

        from amplifier_app_cli.commands.routing import routing_group

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.routing._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            result = runner.invoke(routing_group, ["use", "nonexistent"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "not found" in result.output.lower()


# ============================================================
# Task 15: routing show
# ============================================================


class TestRoutingShow:
    """Tests for `amplifier routing show` command."""

    def test_routing_show_displays_table(self, tmp_path):
        """routing show displays role resolution table."""
        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        _seed_providers(settings)

        from amplifier_app_cli.commands.routing import routing_group

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.routing._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            result = runner.invoke(routing_group, ["show"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show roles from the default matrix (balanced)
        assert "coding" in result.output.lower()
        assert "fast" in result.output.lower()

    def test_routing_show_unresolvable_role(self, tmp_path):
        """Role with no matching provider shows warning."""
        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        # Seed providers that DON'T match economy matrix candidates
        scope_settings = settings._read_scope("global")
        scope_settings["config"] = {
            "providers": [
                # google provider - not in economy matrix for coding
                {"module": "provider-google", "config": {"priority": 1}},
            ]
        }
        settings._write_scope("global", scope_settings)
        settings.set_routing_matrix("economy", scope="global")

        from amplifier_app_cli.commands.routing import routing_group

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.routing._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            result = runner.invoke(routing_group, ["show"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show warning for unresolvable roles
        assert "⚠" in result.output or "no provider" in result.output.lower()

    def test_routing_show_specific_matrix(self, tmp_path):
        """Can show a specific matrix by name."""
        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        _seed_providers(settings)

        from amplifier_app_cli.commands.routing import routing_group

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.routing._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
        ):
            result = runner.invoke(routing_group, ["show", "economy"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "economy" in result.output.lower()
