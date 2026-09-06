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
            ["--preserve", "projects,settings,keys", "-y"],
            {"cache", "registry"},
        ),
        (["--full", "-y"], set(reset_module.RESET_CATEGORIES)),
    ],
)
def test_cli_options_resolve_to_a_removal_plan(monkeypatch, args, expected_remove):
    show_plan, cleanup = _invoke_dry_run(monkeypatch, args)

    assert show_plan.call_args.args[0] == expected_remove
    assert cleanup.call_args.args[0] == expected_remove


def test_empty_preserve_is_refused_rather_than_removing_everything(monkeypatch):
    """`--preserve "$VAR"` with VAR unset must not become a silent --full.

    An empty --remove is a no-op, so the mirrored empty --preserve reaching
    "remove every category, projects included" - with -y suppressing the
    confirm - is a blast radius an unset shell variable should not be able to
    select. --full is how that is asked for.
    """
    cleanup = MagicMock()
    monkeypatch.setattr(reset_module, "_remove_amplifier_dir", cleanup)

    result = CliRunner().invoke(reset_module.reset, ["--preserve", "", "-y"])

    assert result.exit_code != 0
    assert "--full" in result.output
    cleanup.assert_not_called()


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


def test_cleanup_never_touches_unmanaged_state(tmp_path, monkeypatch):
    """Entries no category names belong to other components, and are kept.

    ~/.amplifier accumulates state from components reset does not model -
    device memory, user skills, credentials modules store outside keys.env.
    Selecting every category reset *does* know about must not reach any of it.
    """
    amplifier_dir = _make_amplifier_dir(tmp_path)
    unmanaged_dir = amplifier_dir / "engram"
    unmanaged_dir.mkdir()
    (unmanaged_dir / "memory.json").write_text("irreplaceable")
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path",
        lambda: tmp_path / "install-state.json",
    )

    assert reset_module._remove_amplifier_dir(set(reset_module.RESET_CATEGORIES))

    assert (unmanaged_dir / "memory.json").read_text() == "irreplaceable"
    assert (amplifier_dir / "custom.toml").exists()
    assert amplifier_dir.exists()
    for name in ("projects", "settings.yaml", "keys.env", "cache", "registry.json"):
        assert not (amplifier_dir / name).exists(), name


def test_retired_other_category_is_refused_with_an_explanation(monkeypatch):
    cleanup = MagicMock()
    monkeypatch.setattr(reset_module, "_remove_amplifier_dir", cleanup)

    result = CliRunner().invoke(reset_module.reset, ["--remove", "other", "-y"])

    assert result.exit_code != 0
    assert "--full" in result.output
    cleanup.assert_not_called()


def test_full_cleanup_removes_the_root_directory(tmp_path, monkeypatch):
    amplifier_dir = _make_amplifier_dir(tmp_path)
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)
    get_install_state_path = MagicMock()
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path", get_install_state_path
    )

    assert reset_module._remove_amplifier_dir(
        set(reset_module.RESET_CATEGORIES), full=True
    )

    assert not amplifier_dir.exists()
    get_install_state_path.assert_not_called()


def test_whole_directory_removal_requires_full_not_a_complete_category_set(
    tmp_path, monkeypatch
):
    """Naming every category is not a request to delete a sixth thing.

    Inferring "remove the root" from set equality is the same overloading that
    made an empty preserve set mean "nuke everything"; ``full`` is explicit.
    """
    amplifier_dir = _make_amplifier_dir(tmp_path)
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path",
        lambda: tmp_path / "install-state.json",
    )

    assert reset_module._remove_amplifier_dir(set(reset_module.RESET_CATEGORIES))

    assert amplifier_dir.exists()
    assert (amplifier_dir / "custom.toml").exists()


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


def test_cleanup_never_clears_the_real_cache_for_another_directory(
    tmp_path, monkeypatch
):
    """Selecting cache/registry must act on the directory being cleaned.

    clear_download_cache/clear_registry resolve ~/.amplifier themselves, via a
    second copy of the path logic in cache_management. Without a guard, a
    _remove_amplifier_dir call pointed anywhere else still wiped the *real*
    cache and registry -- a cross-directory delete, and a live footgun for any
    test that selects those categories.
    """
    amplifier_dir = _make_amplifier_dir(tmp_path)
    monkeypatch.setattr(reset_module, "_get_amplifier_dir", lambda: amplifier_dir)

    real_cache = MagicMock(return_value=(0, True))
    real_registry = MagicMock(return_value=True)
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.clear_download_cache", real_cache
    )
    monkeypatch.setattr(
        "amplifier_app_cli.utils.cache_management.clear_registry", real_registry
    )
    monkeypatch.setattr(
        "amplifier_app_cli.paths.get_install_state_path",
        lambda: tmp_path / "install-state.json",
    )

    assert reset_module._remove_amplifier_dir({"cache", "registry"})

    real_cache.assert_not_called()
    real_registry.assert_not_called()
    assert not (amplifier_dir / "cache").exists()
    assert not (amplifier_dir / "registry.json").exists()
