"""Bundle fallback policy and real command selection without module activation."""

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from amplifier_app_cli.lib.bundle_loader.discovery import DEFAULT_BUNDLE
from amplifier_app_cli.lib.settings import AppSettings

# commands/__init__ re-exports Click groups that shadow these submodules.
tool_cmd = importlib.import_module("amplifier_app_cli.commands.tool")
run_cmd = importlib.import_module("amplifier_app_cli.commands.run")
runtime_config = importlib.import_module("amplifier_app_cli.runtime.config")

SELECTIONS = [
    "anchors",
    "foundation",
    "amplifier-dev",
    "exp-delegation",
    "my-custom-root",
    "git+https://example.test/my-host@main#subdirectory=bundle.md",
]


@pytest.fixture(autouse=True)
def isolated_bundle_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(home / ".amplifier"))
    monkeypatch.chdir(project)
    return AppSettings()


def test_settings_leave_unset_bundle_as_none(isolated_bundle_settings):
    assert DEFAULT_BUNDLE == "anchors-amp-dev"
    assert isolated_bundle_settings.get_active_bundle() is None
    assert not isolated_bundle_settings.paths.global_settings.exists()


def test_should_use_bundle_defaults_to_anchors_amp_dev():
    with patch.object(tool_cmd, "_get_active_bundle_name", return_value=None):
        use_bundle, bundle_name, _ = tool_cmd._should_use_bundle()
    assert use_bundle is True
    assert bundle_name == DEFAULT_BUNDLE


@pytest.mark.parametrize("selection", SELECTIONS)
def test_should_use_bundle_respects_explicit_active(selection):
    with patch.object(tool_cmd, "_get_active_bundle_name", return_value=selection):
        use_bundle, bundle_name, _ = tool_cmd._should_use_bundle()
    assert use_bundle is True
    assert bundle_name == selection


@pytest.fixture
def run_selection_boundary(monkeypatch, isolated_bundle_settings):
    """Register the real run command; stop at configuration activation."""
    cli = click.Group()
    run_cmd.register_run_command(
        cli,
        interactive_chat=MagicMock(),
        execute_single=MagicMock(),
        get_module_search_paths=lambda: [],
        check_first_run=lambda: False,
        prompt_first_run_init=MagicMock(),
    )
    monkeypatch.setattr(
        run_cmd, "create_config_manager", lambda: isolated_bundle_settings
    )
    selected = []

    def activation_boundary(*, bundle_name, app_settings, console):
        selected.append(bundle_name)
        raise click.exceptions.Exit(0)

    monkeypatch.setattr(run_cmd, "resolve_config", activation_boundary)
    return cli, selected


@pytest.mark.parametrize("saved", [None, *SELECTIONS])
def test_run_default_and_saved_selection_at_activation(
    saved, run_selection_boundary, isolated_bundle_settings
):
    if saved:
        isolated_bundle_settings.set_active_bundle(saved)
    before = isolated_bundle_settings.get_merged_settings()
    cli, selected = run_selection_boundary
    result = CliRunner().invoke(cli, ["run", "test prompt"])
    assert result.exit_code == 0, result.output
    assert selected == [saved or DEFAULT_BUNDLE]
    assert isolated_bundle_settings.get_merged_settings() == before


@pytest.mark.parametrize("explicit", SELECTIONS)
def test_run_flag_overrides_saved_selection_at_activation(
    explicit, run_selection_boundary, isolated_bundle_settings
):
    isolated_bundle_settings.set_active_bundle("foundation")
    cli, selected = run_selection_boundary
    result = CliRunner().invoke(cli, ["run", "--bundle", explicit, "test prompt"])
    assert result.exit_code == 0, result.output
    assert selected == [explicit]
    assert isolated_bundle_settings.get_active_bundle() == "foundation"


@pytest.mark.parametrize("resumed", SELECTIONS)
@pytest.mark.parametrize("explicit", [None, "anchors"])
def test_run_resume_precedence_at_activation(
    resumed, explicit, run_selection_boundary, isolated_bundle_settings, monkeypatch
):
    isolated_bundle_settings.set_active_bundle("anchors-amp-dev")
    monkeypatch.setattr("amplifier_app_cli.session_store.SessionStore", MagicMock())
    monkeypatch.setattr(
        "amplifier_app_cli.shared_root_state.resolve_root_session_id",
        lambda store, session_id: session_id,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.shared_root_state.load_root_resume",
        lambda store, session_id: ([], {"bundle": f"bundle:{resumed}"}),
    )
    cli, selected = run_selection_boundary
    args = ["run", "--resume", "saved-session", "test prompt"]
    if explicit:
        args.extend(["--bundle", explicit])
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert selected == [explicit or resumed]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [None, *SELECTIONS])
async def test_runtime_default_and_explicit_selection_at_activation(
    explicit, isolated_bundle_settings, monkeypatch
):
    prepared = object()
    activation = AsyncMock(return_value=({}, prepared))
    monkeypatch.setattr(runtime_config, "resolve_bundle_config", activation)
    result = await runtime_config.resolve_config_async(
        bundle_name=explicit,
        app_settings=isolated_bundle_settings,
        session_id="session",
        project_slug="project",
    )
    assert result == ({}, prepared)
    activation.assert_awaited_once_with(
        bundle_name=explicit or DEFAULT_BUNDLE,
        app_settings=isolated_bundle_settings,
        console=None,
        session_id="session",
        project_slug="project",
    )


@pytest.mark.parametrize("command", ["list", "info", "invoke"])
@pytest.mark.parametrize(
    "saved, explicit",
    [
        (None, None),
        *[(name, None) for name in SELECTIONS],
        *[("foundation", name) for name in SELECTIONS],
    ],
)
def test_tool_command_selection_at_mount_or_invoke_boundary(
    command, saved, explicit, isolated_bundle_settings, monkeypatch
):
    if saved:
        isolated_bundle_settings.set_active_bundle(saved)
    monkeypatch.setattr(
        tool_cmd, "create_config_manager", lambda: isolated_bundle_settings
    )
    monkeypatch.setattr(tool_cmd, "_ensure_provider_configured", lambda: None)
    mounted = AsyncMock(
        return_value=[{"name": "test_tool", "description": "Test", "has_execute": True}]
    )
    invoked = AsyncMock(return_value={})
    monkeypatch.setattr(tool_cmd, "_get_mounted_tools_from_bundle_async", mounted)
    monkeypatch.setattr(tool_cmd, "_invoke_tool_from_bundle_async", invoked)
    args = [command]
    if command != "list":
        args.append("test_tool")
    if explicit:
        args.extend(["--bundle", explicit])
    result = CliRunner().invoke(tool_cmd.tool, args)
    assert result.exit_code == 0, result.output
    boundary = invoked if command == "invoke" else mounted
    assert boundary.await_count == 1
    assert boundary.await_args.args[0] == (explicit or saved or DEFAULT_BUNDLE)
    assert isolated_bundle_settings.get_active_bundle() == saved


@pytest.mark.parametrize("command", ["list", "info"])
def test_tool_defensive_fallback_uses_shared_default(command, monkeypatch):
    monkeypatch.setattr(tool_cmd, "_ensure_provider_configured", lambda: None)
    monkeypatch.setattr(tool_cmd, "_should_use_bundle", lambda: (True, None, None))
    mounted = AsyncMock(
        return_value=[{"name": "test_tool", "description": "Test", "has_execute": True}]
    )
    monkeypatch.setattr(tool_cmd, "_get_mounted_tools_from_bundle_async", mounted)
    args = [command] if command == "list" else [command, "test_tool"]
    result = CliRunner().invoke(tool_cmd.tool, args)
    assert result.exit_code == 0, result.output
    mounted.assert_awaited_once_with(DEFAULT_BUNDLE)
