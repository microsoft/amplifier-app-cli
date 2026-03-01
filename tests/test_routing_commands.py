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


# ============================================================
# routing create + custom matrix discovery
# ============================================================


def _make_custom_routing_dir(tmp_path: Path) -> Path:
    """Create a custom routing directory with a user matrix."""
    custom_dir = tmp_path / "custom_routing"
    custom_dir.mkdir(parents=True)

    custom_matrix = {
        "name": "my-custom",
        "description": "My custom matrix.",
        "updated": "2026-02-28",
        "roles": {
            "coding": {
                "description": "Code generation",
                "candidates": [
                    {"provider": "anthropic", "model": "claude-sonnet-4-6"},
                ],
            },
            "fast": {
                "description": "Quick tasks",
                "candidates": [
                    {"provider": "anthropic", "model": "claude-haiku-4-5"},
                ],
            },
            "general": {
                "description": "Catch-all",
                "candidates": [
                    {"provider": "anthropic", "model": "claude-sonnet-4-6"},
                ],
            },
        },
    }
    (custom_dir / "my-custom.yaml").write_text(yaml.dump(custom_matrix))
    return custom_dir


class TestRoutingCreateCommandExists:
    """Tests that routing create is registered on the routing group."""

    def test_routing_create_command_exists(self):
        """create is registered as a subcommand on routing_group."""
        from amplifier_app_cli.commands.routing import routing_group

        command_names = [cmd for cmd in routing_group.commands]
        assert "create" in command_names


class TestDiscoverRolesFromMatrices:
    """Tests for role discovery across matrix files."""

    def test_discover_roles_from_single_matrix(self, tmp_path):
        """Discovers roles and descriptions from matrix YAML files."""
        from amplifier_app_cli.commands.routing import discover_roles_from_matrices

        cache_dir = _make_matrix_dir(tmp_path)
        matrix_files = sorted(cache_dir.rglob("*.yaml"))

        roles = discover_roles_from_matrices(matrix_files)

        # balanced has coding + fast, economy has coding + fast
        # deduplication means we get 2 unique roles
        assert "coding" in roles
        assert "fast" in roles
        assert isinstance(roles["coding"], str)
        assert len(roles["coding"]) > 0  # has a description

    def test_discover_roles_deduplicates(self, tmp_path):
        """First description wins when roles appear in multiple matrices."""
        from amplifier_app_cli.commands.routing import discover_roles_from_matrices

        cache_dir = _make_matrix_dir(tmp_path)
        matrix_files = sorted(cache_dir.rglob("*.yaml"))

        roles = discover_roles_from_matrices(matrix_files)

        # "coding" appears in both balanced and economy
        # balanced is first alphabetically, so its description wins
        assert roles["coding"] == "Code generation"

    def test_discover_roles_empty_dir(self, tmp_path):
        """Returns empty dict when no matrix files exist."""
        from amplifier_app_cli.commands.routing import discover_roles_from_matrices

        roles = discover_roles_from_matrices([])
        assert roles == {}


class TestSaveCustomMatrix:
    """Tests for saving a custom matrix to YAML."""

    def test_save_custom_matrix_writes_yaml(self, tmp_path):
        """Custom matrix is saved as valid YAML with correct schema."""
        from amplifier_app_cli.commands.routing import save_custom_matrix

        output_dir = tmp_path / "routing"
        assignments = {
            "coding": {
                "description": "Code generation",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
            "fast": {
                "description": "Quick tasks",
                "provider": "openai",
                "model": "gpt-5-mini",
            },
            "general": {
                "description": "Catch-all",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        }

        save_custom_matrix("test-matrix", assignments, output_dir)

        saved_path = output_dir / "test-matrix.yaml"
        assert saved_path.exists()

        data = yaml.safe_load(saved_path.read_text())
        assert data["name"] == "test-matrix"
        assert "roles" in data
        assert "coding" in data["roles"]
        assert data["roles"]["coding"]["candidates"][0]["provider"] == "anthropic"
        assert data["roles"]["coding"]["candidates"][0]["model"] == "claude-sonnet-4-6"
        assert data["roles"]["fast"]["candidates"][0]["provider"] == "openai"
        assert data["roles"]["fast"]["candidates"][0]["model"] == "gpt-5-mini"

    def test_save_custom_matrix_creates_directory(self, tmp_path):
        """save_custom_matrix creates the output directory if it doesn't exist."""
        from amplifier_app_cli.commands.routing import save_custom_matrix

        output_dir = tmp_path / "nonexistent" / "routing"
        assignments = {
            "general": {
                "description": "Catch-all",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
            "fast": {
                "description": "Quick",
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
            },
        }

        save_custom_matrix("minimal", assignments, output_dir)
        assert (output_dir / "minimal.yaml").exists()


class TestCustomMatrixDiscovery:
    """Tests that matrix discovery includes custom routing directory."""

    def test_discover_includes_custom_matrices(self, tmp_path):
        """_discover_matrix_files finds both bundle and custom matrices."""
        from amplifier_app_cli.commands.routing import _discover_matrix_files

        # Set up bundle cache at tmp_path/.amplifier/cache/...
        cache_dir = (
            tmp_path / ".amplifier" / "cache"
            / "amplifier-bundle-routing-matrix-abc123" / "routing"
        )
        cache_dir.mkdir(parents=True)
        balanced = {
            "name": "balanced",
            "description": "Balanced.",
            "roles": {"general": {"description": "G", "candidates": []}},
        }
        (cache_dir / "balanced.yaml").write_text(yaml.dump(balanced))

        # Set up custom routing dir at tmp_path/.amplifier/routing/
        custom_dir = tmp_path / ".amplifier" / "routing"
        custom_dir.mkdir(parents=True)
        custom = {
            "name": "my-custom",
            "description": "Custom.",
            "roles": {"general": {"description": "G", "candidates": []}},
        }
        (custom_dir / "my-custom.yaml").write_text(yaml.dump(custom))

        # Patch Path.home so ~/.amplifier resolves to tmp_path/.amplifier
        with patch(
            "amplifier_app_cli.commands.routing.Path.home",
            return_value=tmp_path,
        ):
            files = _discover_matrix_files()

        filenames = [f.name for f in files]
        assert "balanced.yaml" in filenames
        assert "my-custom.yaml" in filenames
