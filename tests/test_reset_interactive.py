"""Focused behavior tests for the interactive reset checklist."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

from click.testing import CliRunner


reset_module = importlib.import_module("amplifier_app_cli.commands.reset")
interactive_module = importlib.import_module(
    "amplifier_app_cli.commands.reset_interactive"
)


def _items_with_default_removals():
    return [
        interactive_module.ChecklistItem(
            category,
            reset_module.CATEGORY_DESCRIPTIONS[category],
            category in reset_module.DEFAULT_REMOVE,
        )
        for category in reset_module.CATEGORY_ORDER
    ]


def _interactive_dry_run(
    monkeypatch, remove_cats: set[str]
) -> tuple[MagicMock, MagicMock]:
    show_plan = MagicMock()
    cleanup = MagicMock()
    monkeypatch.setattr(reset_module, "_run_interactive", lambda: remove_cats)
    monkeypatch.setattr(reset_module, "_show_plan", show_plan)
    monkeypatch.setattr(reset_module, "_clean_uv_cache", MagicMock())
    monkeypatch.setattr(reset_module, "_uninstall_amplifier", MagicMock())
    monkeypatch.setattr(reset_module, "_remove_amplifier_dir", cleanup)

    result = CliRunner().invoke(reset_module.reset, ["--dry-run"])

    assert result.exit_code == 0, result.output
    return show_plan, cleanup


def test_interactive_checklist_selects_current_default_removals(monkeypatch):
    captured = {}

    def capture_items(items, title):
        captured["items"] = items
        captured["title"] = title
        return set()

    monkeypatch.setattr(reset_module, "run_checklist", capture_items)

    reset_module._run_interactive()

    selected = {item.key for item in captured["items"] if item.selected}
    assert captured["title"] == "Amplifier Reset"
    assert selected == {"cache", "registry"}
    assert all(
        not item.selected
        for item in captured["items"]
        if item.key in {"projects", "settings", "keys", "other"}
    )


def test_enter_keeps_protected_categories_and_shows_removal_wording(
    monkeypatch, capsys
):
    items = _items_with_default_removals()
    monkeypatch.setattr(interactive_module, "_get_key", lambda: "ENTER")

    remove_cats = interactive_module.run_checklist(items)
    output = capsys.readouterr().out

    assert remove_cats == {"cache", "registry"}
    assert "Checked items will be removed/reset." in output
    assert "Will REMOVE: cache, registry" in output
    assert "[a] Remove all  [n] Remove none" in output

    show_plan, cleanup = _interactive_dry_run(monkeypatch, remove_cats)
    assert show_plan.call_args.args[0] == reset_module.DEFAULT_REMOVE
    assert cleanup.call_args.args[0] == reset_module.DEFAULT_REMOVE


def test_toggling_protected_category_selects_it_for_removal(monkeypatch):
    items = _items_with_default_removals()
    keys = iter([" ", "ENTER"])
    monkeypatch.setattr(interactive_module, "_get_key", lambda: next(keys))

    remove_cats = interactive_module.run_checklist(items)

    assert remove_cats == {"projects", "cache", "registry"}

    show_plan, cleanup = _interactive_dry_run(monkeypatch, remove_cats)
    assert "projects" in show_plan.call_args.args[0]
    assert "projects" in cleanup.call_args.args[0]


def test_remove_all_and_remove_none_shortcuts(monkeypatch):
    all_items = _items_with_default_removals()
    all_keys = iter(["a", "ENTER"])
    monkeypatch.setattr(interactive_module, "_get_key", lambda: next(all_keys))
    assert interactive_module.run_checklist(all_items) == set(
        reset_module.RESET_CATEGORIES
    )

    no_items = _items_with_default_removals()
    no_keys = iter(["n", "ENTER"])
    monkeypatch.setattr(interactive_module, "_get_key", lambda: next(no_keys))
    assert interactive_module.run_checklist(no_items) == set()