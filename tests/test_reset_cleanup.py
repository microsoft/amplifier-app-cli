"""Regression coverage for reset's removal-native cleanup plan."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner


reset_module = importlib.import_module("amplifier_app_cli.commands.reset")


def _invoke_dry_run(monkeypatch, args: list[str]) -> tuple[MagicMock, MagicMock]:
    show_plan = MagicMock()
    cleanup = MagicMock()
    monkeypatch.setattr(reset_module, "_show_plan", show_plan)
    monkeypatch.setattr(reset_module, "_clean_uv_cache", MagicMock())
    monkeypatch.setattr(reset_module, "_uninstall_amplifier", MagicMock())
    monkeypatch.setattr(reset_module, "_remove_amplifier_dir", cleanup)

    result = CliRunner().invoke(reset_module.reset, [*args, "--dry-run"])

    assert result.exit_code == 0, result.output
    return show_plan, cleanup


@pytest.mark.parametrize(
    ("args", "expected_remove"),
    [
        (["-y"], {"cache", "registry"}),
        (["--remove", "cache", "-y"], {"cache"}),
        (["--remove", "", "-y"], set()),
        (
            ["--preserve", "projects,settings,keys,other", "-y"],
            {"cache", "registry"},
        ),
        (["--preserve", "", "-y"], set(reset_module.RESET_CATEGORIES)),
        (["--full", "-y"], set(reset_module.RESET_CATEGORIES)),
    ],
)
def test_cli_options_resolve_to_a_removal_plan(monkeypatch, args, expected_remove):
    show_plan, cleanup = _invoke_dry_run(monkeypatch, args)

    assert show_plan.call_args.args[0] == expected_remove
    assert cleanup.call_args.args[0] == expected_remove


def _make_amplifier_dir(tmp_path: Path) -> Path:
    amplifier_dir = tmp_path / ".amplifier"
    (amplifier_dir / "projects").mkdir(parents=True)
    (amplifier_dir / "projects" / "session.json").write_text("session")
    (amplifier_dir / "cache").mkdir()
    (amplifier_dir / "cache" / "bundle").write_text("cache")
    (amplifier_dir / "registry.json").write_text("{}")
    (amplifier_dir / "settings.yaml").write_text("settings")
    (amplifier_dir / "keys.env").write_text("keys")
    (amplifier_dir / "custom.toml").write_text("custom")
    return amplifier_dir


def test_cleanup_removes_only_explicitly_selected_paths(tmp_path, monkeypatch):
    amplifier_dir = _make_amplifier_dir(tmp_path)
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)

    assert reset_module._remove_amplifier_dir({"projects"})

    assert not (amplifier_dir / "projects").exists()
    for name in ("cache", "registry.json", "settings.yaml", "keys.env", "custom.toml"):
        assert (amplifier_dir / name).exists(), name


def test_cleanup_removes_only_dynamic_other_paths(tmp_path, monkeypatch):
    amplifier_dir = _make_amplifier_dir(tmp_path)
    custom_dir = amplifier_dir / "plugin"
    custom_dir.mkdir()
    (custom_dir / "entry.py").write_text("plugin")
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)

    assert reset_module._remove_amplifier_dir({"other"})

    assert not (amplifier_dir / "custom.toml").exists()
    assert not custom_dir.exists()
    for name in ("projects", "cache", "registry.json", "settings.yaml", "keys.env"):
        assert (amplifier_dir / name).exists(), name


def test_full_cleanup_removes_the_root_directory(tmp_path, monkeypatch):
    amplifier_dir = _make_amplifier_dir(tmp_path)
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)
    get_install_state_path = MagicMock()
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path", get_install_state_path
    )

    assert reset_module._remove_amplifier_dir(set(reset_module.RESET_CATEGORIES))

    assert not amplifier_dir.exists()
    get_install_state_path.assert_not_called()


def test_empty_removal_plan_is_a_data_cleanup_no_op(tmp_path, monkeypatch):
    amplifier_dir = _make_amplifier_dir(tmp_path)
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)

    assert reset_module._remove_amplifier_dir(set())

    for name in (
        "projects",
        "cache",
        "registry.json",
        "settings.yaml",
        "keys.env",
        "custom.toml",
    ):
        assert (amplifier_dir / name).exists(), name


def test_install_state_is_cleared_only_when_cache_is_selected(tmp_path, monkeypatch):
    amplifier_dir = _make_amplifier_dir(tmp_path)
    install_state = tmp_path / "install-state.json"
    install_state.write_text("{}")
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path", lambda: install_state
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.clear_download_cache",
        MagicMock(return_value=(1, True)),
    )

    assert reset_module._remove_amplifier_dir({"settings"})
    assert install_state.exists()

    assert reset_module._remove_amplifier_dir({"cache"})
    assert not install_state.exists()


def test_selected_directory_symlink_is_unlinked_without_touching_target(
    tmp_path, monkeypatch
):
    amplifier_dir = tmp_path / ".amplifier"
    amplifier_dir.mkdir()
    target = tmp_path / "projects-target"
    target.mkdir()
    target_file = target / "session.json"
    target_file.write_text("session")
    project_link = amplifier_dir / "projects"
    try:
        project_link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)

    assert reset_module._remove_amplifier_dir({"projects"})

    assert not project_link.is_symlink()
    assert target_file.read_text() == "session"
