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

    def test_routing_use_scope_guard(self, tmp_path):
        """routing use rejects non-global scope when run from home directory."""
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
            patch(
                "amplifier_app_cli.ui.scope.is_running_from_home",
                return_value=True,
            ),
        ):
            result = runner.invoke(
                routing_group, ["use", "balanced", "--scope", "project"]
            )

        # Should fail with a usage error referencing home directory
        assert result.exit_code != 0 or "home" in result.output.lower()
        assert "home" in result.output.lower() or (
            result.exception is not None and "home" in str(result.exception).lower()
        )


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
            tmp_path
            / ".amplifier"
            / "cache"
            / "amplifier-bundle-routing-matrix-abc123"
            / "routing"
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


# ============================================================
# routing manage loop: [c] Create a custom matrix option
# ============================================================


class TestRoutingManageCreateOption:
    """Tests that routing manage loop exposes [c] Create a custom matrix."""

    def test_routing_create_interactive_exists_and_callable(self):
        """_routing_create_interactive is importable and callable."""
        from amplifier_app_cli.commands.routing import _routing_create_interactive

        assert callable(_routing_create_interactive)

    def test_routing_manage_loop_shows_create_option(self, tmp_path):
        """routing_manage_loop prints [c] Create a custom matrix in its menu."""
        from amplifier_app_cli.commands.routing import routing_manage_loop

        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        _seed_providers(settings)

        with (
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
            patch(
                "amplifier_app_cli.commands.routing.Prompt.ask",
                return_value="d",  # immediately quit
            ),
        ):
            from io import StringIO

            from rich.console import Console as RichConsole

            buf = StringIO()
            test_console = RichConsole(file=buf, width=120)
            with patch("amplifier_app_cli.commands.routing.console", test_console):
                routing_manage_loop(settings)

            output = buf.getvalue()

        assert "Create a custom matrix" in output

    def test_routing_manage_loop_c_calls_create_interactive(self, tmp_path):
        """Pressing 'c' in manage loop calls _routing_create_interactive."""
        from amplifier_app_cli.commands.routing import routing_manage_loop

        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        _seed_providers(settings)

        with (
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=list(cache_dir.rglob("*.yaml")),
            ),
            patch(
                "amplifier_app_cli.commands.routing.Prompt.ask",
                side_effect=["c", "d"],  # press c, then d to quit
            ),
            patch(
                "amplifier_app_cli.commands.routing._routing_create_interactive",
            ) as mock_create,
        ):
            routing_manage_loop(settings)

        mock_create.assert_called_once_with(settings)


# ============================================================
# Task 6: _get_provider_names() deduplication
# ============================================================


def _seed_provider(settings: AppSettings, providers: list[dict]) -> None:
    """Seed provider entries with explicit list for fine-grained control."""
    scope_settings = settings._read_scope("global")
    scope_settings["config"] = {"providers": providers}
    settings._write_scope("global", scope_settings)


class TestGetProviderNames:
    """Tests for _get_provider_names() deduplication."""

    def test_get_provider_names_deduplicates_same_module(self, tmp_path):
        """Two providers sharing a module but with different ids yield one type name."""
        from amplifier_app_cli.commands.routing import _get_provider_names

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            [
                {"module": "provider-openai", "id": "openai-1"},
                {"module": "provider-openai", "id": "openai-2"},
            ],
        )

        names = _get_provider_names(settings)

        assert names == ["openai"], f"Expected ['openai'], got {names}"

    def test_get_provider_names_returns_all_unique_types(self, tmp_path):
        """Three providers with distinct modules all appear in the result."""
        from amplifier_app_cli.commands.routing import _get_provider_names

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            [
                {"module": "provider-anthropic"},
                {"module": "provider-openai"},
                {"module": "provider-github-copilot"},
            ],
        )

        names = _get_provider_names(settings)

        assert names == ["anthropic", "openai", "github-copilot"]


# ============================================================
# Task 9: _show_matrix_details()
# ============================================================


def _make_test_console():
    """Create an isolated Rich console writing to a StringIO buffer."""
    from io import StringIO

    from rich.console import Console as RichConsole

    buf = StringIO()
    con = RichConsole(file=buf, force_terminal=False, highlight=False)
    return con, buf


def _seed_providers_for_details(settings: AppSettings, modules: list[str]) -> None:
    """Seed provider entries by module name list."""
    providers = [{"module": m} for m in modules]
    scope_settings = settings._read_scope("global")
    scope_settings["config"] = {"providers": providers}
    settings._write_scope("global", scope_settings)


class TestShowMatrixDetails:
    """Tests for _show_matrix_details() candidate waterfall view."""

    # Common matrix fixture used in most tests
    _THREE_CANDIDATE_MATRIX = {
        "name": "test-matrix",
        "description": "Test description",
        "updated": "2026-03-01",
        "roles": {
            "general": {
                "description": "Versatile catch-all",
                "candidates": [
                    {"provider": "anthropic", "model": "claude-sonnet-4-6"},
                    {"provider": "openai", "model": "gpt-5.2"},
                    {"provider": "google", "model": "gemini-pro"},
                ],
            }
        },
    }

    def test_show_matrix_details_shows_all_candidates(self, tmp_path):
        """All candidates appear in output, not just the winner."""
        from amplifier_app_cli.commands.routing import _show_matrix_details

        settings = _make_settings(tmp_path)
        _seed_providers_for_details(settings, ["provider-anthropic", "provider-openai"])

        con, buf = _make_test_console()
        with patch("amplifier_app_cli.commands.routing.console", con):
            _show_matrix_details(self._THREE_CANDIDATE_MATRIX, settings)

        rendered = buf.getvalue()
        assert "claude-sonnet-4-6" in rendered
        assert "gpt-5.2" in rendered
        assert "gemini-pro" in rendered

    def test_show_matrix_details_marks_winner_with_star(self, tmp_path):
        """First configured candidate is marked with ★."""
        from amplifier_app_cli.commands.routing import _show_matrix_details

        settings = _make_settings(tmp_path)
        _seed_providers_for_details(settings, ["provider-anthropic", "provider-openai"])

        con, buf = _make_test_console()
        with patch("amplifier_app_cli.commands.routing.console", con):
            _show_matrix_details(self._THREE_CANDIDATE_MATRIX, settings)

        rendered = buf.getvalue()
        assert "★" in rendered

    def test_show_matrix_details_marks_unconfigured(self, tmp_path):
        """Unconfigured candidates show 'not configured'."""
        from amplifier_app_cli.commands.routing import _show_matrix_details

        settings = _make_settings(tmp_path)
        _seed_providers_for_details(settings, ["provider-anthropic", "provider-openai"])

        con, buf = _make_test_console()
        with patch("amplifier_app_cli.commands.routing.console", con):
            _show_matrix_details(self._THREE_CANDIDATE_MATRIX, settings)

        rendered = buf.getvalue()
        assert "not configured" in rendered

    def test_show_matrix_details_shows_role_description(self, tmp_path):
        """Role description appears in the output."""
        from amplifier_app_cli.commands.routing import _show_matrix_details

        settings = _make_settings(tmp_path)
        _seed_providers_for_details(settings, ["provider-anthropic"])

        con, buf = _make_test_console()
        with patch("amplifier_app_cli.commands.routing.console", con):
            _show_matrix_details(self._THREE_CANDIDATE_MATRIX, settings)

        rendered = buf.getvalue()
        assert "Versatile catch-all" in rendered

    def test_show_matrix_details_shows_config_block(self, tmp_path):
        """Candidate config dict is rendered inline (e.g. [reasoning_effort: high])."""
        from amplifier_app_cli.commands.routing import _show_matrix_details

        matrix = {
            "name": "config-test",
            "description": "Config block test",
            "updated": "2026-03-01",
            "roles": {
                "security-audit": {
                    "description": "Vulnerability assessment",
                    "candidates": [
                        {
                            "provider": "openai",
                            "model": "gpt-5.3-codex",
                            "config": {"reasoning_effort": "high"},
                        }
                    ],
                }
            },
        }

        settings = _make_settings(tmp_path)
        _seed_providers_for_details(settings, ["provider-openai"])

        con, buf = _make_test_console()
        with patch("amplifier_app_cli.commands.routing.console", con):
            _show_matrix_details(matrix, settings)

        rendered = buf.getvalue()
        assert "reasoning_effort" in rendered
        assert "high" in rendered

    def test_show_matrix_details_shows_no_coverage_warning(self, tmp_path):
        """⚠ warning shown when no candidate for a role is configured."""
        from amplifier_app_cli.commands.routing import _show_matrix_details

        matrix = {
            "name": "no-coverage",
            "description": "Coverage test",
            "updated": "2026-03-01",
            "roles": {
                "image-gen": {
                    "description": "Image generation",
                    "candidates": [
                        {"provider": "google", "model": "gemini-3-pro-image-preview"},
                    ],
                }
            },
        }

        settings = _make_settings(tmp_path)
        # Only configure anthropic — which is NOT in the role's candidates
        _seed_providers_for_details(settings, ["provider-anthropic"])

        con, buf = _make_test_console()
        with patch("amplifier_app_cli.commands.routing.console", con):
            _show_matrix_details(matrix, settings)

        rendered = buf.getvalue()
        assert "⚠" in rendered

    def test_show_matrix_details_shows_matrix_header(self, tmp_path):
        """Matrix name, description, and updated date appear in output."""
        from amplifier_app_cli.commands.routing import _show_matrix_details

        settings = _make_settings(tmp_path)
        _seed_providers_for_details(settings, ["provider-anthropic"])

        con, buf = _make_test_console()
        with patch("amplifier_app_cli.commands.routing.console", con):
            _show_matrix_details(self._THREE_CANDIDATE_MATRIX, settings)

        rendered = buf.getvalue()
        assert "test-matrix" in rendered
        assert "Test description" in rendered
        assert "2026-03-01" in rendered

    def test_routing_show_detailed_flag(self, tmp_path):
        """routing show --detailed calls _show_matrix_details instead of _show_matrix_resolution."""
        from amplifier_app_cli.commands.routing import routing_group

        cache_dir = _make_matrix_dir(tmp_path)
        settings = _make_settings(tmp_path)
        _seed_providers(settings)

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
            patch(
                "amplifier_app_cli.commands.routing._show_matrix_details",
            ) as mock_details,
            patch(
                "amplifier_app_cli.commands.routing._show_matrix_resolution",
            ) as mock_resolution,
        ):
            result = runner.invoke(routing_group, ["show", "--detailed"])

        assert result.exit_code == 0, f"Output: {result.output}"
        mock_details.assert_called_once()
        mock_resolution.assert_not_called()


# ============================================================
# _list_models_for_provider: pass collected_config from settings
# ============================================================


class TestListModelsForProvider:
    """Tests that _list_models_for_provider threads provider config to get_provider_models."""

    def test_list_models_passes_config_to_get_provider_models(self, tmp_path):
        """When settings has a provider config, collected_config is passed to get_provider_models."""
        from amplifier_app_cli.commands.routing import _list_models_for_provider

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            [
                {
                    "module": "provider-anthropic",
                    "config": {"api_key": "test-key", "default_model": "claude-sonnet"},
                }
            ],
        )

        with patch(
            "amplifier_app_cli.provider_loader.get_provider_models", return_value=[]
        ) as mock_gpm:
            _list_models_for_provider("anthropic", settings=settings)

        mock_gpm.assert_called_once()
        _, call_kwargs = mock_gpm.call_args
        assert call_kwargs.get("collected_config") is not None, (
            "Expected collected_config to be passed to get_provider_models"
        )
        assert call_kwargs["collected_config"].get("api_key") == "test-key"

    def test_list_models_works_without_settings(self, tmp_path):
        """Calling without settings still calls get_provider_models (backward compat)."""
        from amplifier_app_cli.commands.routing import _list_models_for_provider

        with patch(
            "amplifier_app_cli.provider_loader.get_provider_models", return_value=[]
        ) as mock_gpm:
            result = _list_models_for_provider("anthropic")

        mock_gpm.assert_called_once()
        assert result == []


# ============================================================
# Task 4: Upfront model cache in _routing_create_interactive()
# ============================================================


class TestRoutingCreateModelCache:
    """Tests that _routing_create_interactive() fetches models upfront once per provider."""

    def _make_prompts_for_full_flow(self):
        """Return prompt side_effect list for routing.Prompt in a 2-provider, 2-role flow.

        Flow (routing.Prompt only — model selection now uses provider_config_utils.Prompt):
          - role walk: skip general, skip fast (2 skips)
          - required role retry: pick provider 1 (×2 roles)
          - post-summary menu: quit
        """
        return [
            "s",  # skip "general" in role walk
            "s",  # skip "fast" in role walk
            "1",  # pick provider 1 for required "general" retry
            "1",  # pick provider 1 for required "fast" retry
            "q",  # quit without saving
        ]

    def test_routing_create_fetches_models_upfront(self, tmp_path):
        """get_provider_models is called once per provider (not once per role)."""
        from io import StringIO

        from rich.console import Console as RichConsole
        from unittest.mock import MagicMock

        from amplifier_app_cli.commands.routing import _routing_create_interactive

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            [
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-sonnet"},
                },
                {"module": "provider-openai", "config": {"default_model": "gpt-5.2"}},
            ],
        )

        mock_model = MagicMock()
        mock_model.id = "test-model"
        mock_model.display_name = "Test Model"
        mock_model.capabilities = []

        buf = StringIO()
        test_console = RichConsole(file=buf, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.routing.console", test_console),
            patch(
                "amplifier_app_cli.commands.routing.get_provider_models",
                return_value=[mock_model],
            ) as mock_gpm,
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=[],
            ),
            patch("amplifier_app_cli.commands.routing.Prompt") as MockPrompt,
            patch("amplifier_app_cli.provider_config_utils.Prompt") as MockPromptPCU,
        ):
            MockPrompt.ask.side_effect = self._make_prompts_for_full_flow()
            # Model selection (via _prompt_model_selection) picks model #1 for each role
            MockPromptPCU.ask.side_effect = ["1", "1"]
            _routing_create_interactive(settings)

        # get_provider_models should be called once per provider (2), not once per role
        assert mock_gpm.call_count == 2, (
            f"Expected 2 calls (one per provider), got {mock_gpm.call_count}"
        )

    def test_routing_create_shows_fetch_summary(self, tmp_path):
        """Output contains per-provider model count summary after upfront fetch."""
        from io import StringIO

        from rich.console import Console as RichConsole
        from unittest.mock import MagicMock

        from amplifier_app_cli.commands.routing import _routing_create_interactive

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            [
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-sonnet"},
                },
                {"module": "provider-openai", "config": {"default_model": "gpt-5.2"}},
            ],
        )

        mock_model = MagicMock()
        mock_model.id = "test-model"
        mock_model.display_name = "Test Model"
        mock_model.capabilities = []

        buf = StringIO()
        test_console = RichConsole(file=buf, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.routing.console", test_console),
            patch(
                "amplifier_app_cli.commands.routing.get_provider_models",
                return_value=[mock_model],
            ),
            patch(
                "amplifier_app_cli.commands.routing._discover_matrix_files",
                return_value=[],
            ),
            patch("amplifier_app_cli.commands.routing.Prompt") as MockPrompt,
            patch("amplifier_app_cli.provider_config_utils.Prompt") as MockPromptPCU,
        ):
            MockPrompt.ask.side_effect = self._make_prompts_for_full_flow()
            MockPromptPCU.ask.side_effect = ["1", "1"]
            _routing_create_interactive(settings)

        rendered = buf.getvalue()
        assert "model(s)" in rendered, (
            f"Expected fetch summary with 'model(s)' in output, got:\n{rendered}"
        )


# ============================================================
# Task 5: DRY _prompt_provider_and_model() — calls _prompt_model_selection()
# ============================================================


class TestPromptProviderAndModelDRY:
    """Tests that _prompt_provider_and_model() delegates model selection to _prompt_model_selection()."""

    def test_prompt_provider_and_model_calls_prompt_model_selection(self, tmp_path):
        """_prompt_provider_and_model() calls _prompt_model_selection with cached models."""
        from io import StringIO
        from unittest.mock import MagicMock

        from rich.console import Console as RichConsole

        from amplifier_app_cli.commands.routing import _prompt_provider_and_model

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            [
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-sonnet"},
                }
            ],
        )

        mock_model = MagicMock()
        mock_model.id = "claude-sonnet-4-6"
        mock_model.display_name = "Claude Sonnet 4.6"
        mock_model.capabilities = ["vision", "thinking"]

        model_cache = {"anthropic": [mock_model]}

        output = StringIO()
        test_console = RichConsole(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.routing.console", test_console),
            patch("amplifier_app_cli.commands.routing.Prompt") as MockPrompt,
            patch(
                "amplifier_app_cli.provider_config_utils._prompt_model_selection",
                return_value="claude-sonnet-4-6",
            ) as mock_pms,
        ):
            MockPrompt.ask.return_value = "1"  # Pick provider #1 (anthropic)
            result = _prompt_provider_and_model(
                "general",
                "Versatile catch-all",
                ["anthropic"],
                settings=settings,
                model_cache=model_cache,
            )

        assert result == ("anthropic", "claude-sonnet-4-6")
        mock_pms.assert_called_once()
        # Verify cached models were passed via keyword arg
        call_kwargs = mock_pms.call_args
        passed_models = call_kwargs.kwargs.get("models")
        assert passed_models == [mock_model], (
            f"Expected cached models to be passed, got: {passed_models}"
        )

    def test_prompt_provider_and_model_returns_none_on_model_cancel(self, tmp_path):
        """When _prompt_model_selection returns None (Ctrl-C), the function returns None."""
        from io import StringIO
        from unittest.mock import MagicMock

        from rich.console import Console as RichConsole

        from amplifier_app_cli.commands.routing import _prompt_provider_and_model

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            [{"module": "provider-openai", "config": {"default_model": "gpt-4"}}],
        )

        mock_model = MagicMock()
        mock_model.id = "gpt-4o"
        mock_model.display_name = "GPT-4o"
        mock_model.capabilities = []

        model_cache = {"openai": [mock_model]}

        output = StringIO()
        test_console = RichConsole(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.routing.console", test_console),
            patch("amplifier_app_cli.commands.routing.Prompt") as MockPrompt,
            patch(
                "amplifier_app_cli.provider_config_utils._prompt_model_selection",
                return_value=None,  # Ctrl-C / cancel
            ),
        ):
            MockPrompt.ask.return_value = "1"  # Pick provider #1 (openai)
            result = _prompt_provider_and_model(
                "fast",
                "Quick utility tasks",
                ["openai"],
                settings=settings,
                model_cache=model_cache,
            )

        assert result is None, (
            f"Expected None when model selection cancelled, got: {result}"
        )

    def test_prompt_provider_and_model_displays_formatted_models(self, tmp_path):
        """Without mocking _prompt_model_selection, output shows display_name not raw repr."""
        from io import StringIO
        from unittest.mock import MagicMock

        from rich.console import Console as RichConsole

        from amplifier_app_cli.commands.routing import _prompt_provider_and_model

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings,
            [
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-sonnet"},
                }
            ],
        )

        mock_model = MagicMock()
        mock_model.id = "claude-sonnet-4-6"
        mock_model.display_name = "Claude Sonnet 4.6"
        mock_model.capabilities = ["vision", "thinking"]

        model_cache = {"anthropic": [mock_model]}

        output = StringIO()
        test_console = RichConsole(file=output, force_terminal=False)

        with (
            patch("amplifier_app_cli.commands.routing.console", test_console),
            patch("amplifier_app_cli.commands.routing.Prompt") as MockPrompt,
            patch("amplifier_app_cli.provider_config_utils.console", test_console),
            patch("amplifier_app_cli.provider_config_utils.Prompt") as MockPromptPCU,
        ):
            MockPrompt.ask.return_value = "1"  # Pick provider #1 (anthropic)
            MockPromptPCU.ask.return_value = "1"  # Pick model #1

            result = _prompt_provider_and_model(
                "general",
                "Versatile catch-all",
                ["anthropic"],
                settings=settings,
                model_cache=model_cache,
            )

        rendered = output.getvalue()
        assert "Claude Sonnet 4.6" in rendered, (
            f"Expected display_name 'Claude Sonnet 4.6' in output, got:\n{rendered}"
        )
        assert result is not None, "Expected a result tuple, not None"


# ============================================================
# Task 6: _pick_base_matrix() helper
# ============================================================


class TestPickBaseMatrix:
    """Tests for _pick_base_matrix() matrix picker helper."""

    # Minimal matrices fixture: two matrices for testing
    _MATRICES = {
        "alpha": {"name": "alpha", "description": "First matrix", "roles": {}},
        "beta": {"name": "beta", "description": "Second matrix", "roles": {}},
    }

    def _set_active_matrix(self, settings: AppSettings, matrix_name: str) -> None:
        """Write routing config so settings reports a specific active matrix."""
        settings._write_scope("global", {"routing": {"matrix": matrix_name}})

    def test_pick_base_matrix_returns_deep_copy(self, tmp_path):
        """Returns a deep copy of the selected matrix, not the original object."""
        from amplifier_app_cli.commands.routing import _pick_base_matrix

        settings = _make_settings(tmp_path)
        self._set_active_matrix(settings, "alpha")

        matrices = {
            "alpha": {"name": "alpha", "description": "First", "roles": {"r": {}}},
            "beta": {"name": "beta", "description": "Second", "roles": {}},
        }

        con, buf = _make_test_console()
        with (
            patch("amplifier_app_cli.commands.routing.console", con),
            patch(
                "amplifier_app_cli.commands.routing.Prompt.ask",
                return_value="1",  # pick #1 (alpha — sorted first)
            ),
        ):
            result = _pick_base_matrix(settings, matrices)

        # Sorted: alpha=1, beta=2; "1" → alpha
        assert result is not None, "Expected a dict, got None"
        assert result == matrices["alpha"], "Returned data should equal selected matrix"
        assert result is not matrices["alpha"], (
            "Should be a deep copy, not the same object"
        )
        # Verify it's truly deep: inner dict must also differ
        assert result["roles"] is not matrices["alpha"]["roles"], (
            "Deep copy should produce independent nested dicts"
        )

    def test_pick_base_matrix_marks_active(self, tmp_path):
        """Active matrix is marked with → arrow and (active) suffix in display."""
        from amplifier_app_cli.commands.routing import _pick_base_matrix

        settings = _make_settings(tmp_path)
        self._set_active_matrix(settings, "beta")

        matrices = {
            "alpha": {"name": "alpha", "roles": {}},
            "beta": {"name": "beta", "roles": {}},
        }

        con, buf = _make_test_console()
        with (
            patch("amplifier_app_cli.commands.routing.console", con),
            patch(
                "amplifier_app_cli.commands.routing.Prompt.ask",
                return_value="1",  # pick any; we only care about printed output
            ),
        ):
            _pick_base_matrix(settings, matrices)

        rendered = buf.getvalue()
        assert "→" in rendered, (
            f"Expected '→' arrow for active matrix, got:\n{rendered}"
        )
        assert "(active)" in rendered, f"Expected '(active)' label, got:\n{rendered}"

    def test_pick_base_matrix_returns_none_on_cancel(self, tmp_path):
        """Returns None when user presses Ctrl-C."""
        from amplifier_app_cli.commands.routing import _pick_base_matrix

        settings = _make_settings(tmp_path)
        self._set_active_matrix(settings, "alpha")

        matrices = {
            "alpha": {"name": "alpha", "roles": {}},
        }

        con, buf = _make_test_console()
        with (
            patch("amplifier_app_cli.commands.routing.console", con),
            patch(
                "amplifier_app_cli.commands.routing.Prompt.ask",
                side_effect=KeyboardInterrupt,
            ),
        ):
            result = _pick_base_matrix(settings, matrices)

        assert result is None, f"Expected None on Ctrl-C, got: {result}"
