"""Preview ranking must not depend on a routing hook import or API order."""

import builtins

import pytest

from amplifier_app_cli.commands.routing import _best_glob_match, _is_glob
from amplifier_foundation.model_patterns import is_glob_pattern, select_model_pattern


@pytest.mark.parametrize("pattern,inventory,expected", [
    ("model-*", ["model-9", "model-10"], "model-10"),
    ("model-*", ["model-10-20260101", "model-10-2026-11-01"], "model-10-2026-11-01"),
    ("model-*", ["Model-10-20261101", "MODEL-10"], "MODEL-10"),
    ("missing-*", ["model-10"], None),
    ("model-*", [], None),
    ("model-[z-a]", ["model-z"], None),
])
def test_preview_uses_foundation_without_routing_hook(monkeypatch, pattern, inventory, expected):
    original = builtins.__import__

    def no_routing_hook(name, *args, **kwargs):
        if name.startswith("amplifier_module_hooks_routing"):
            raise AssertionError("Preview must not import the routing hook")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_routing_hook)
    assert _is_glob is is_glob_pattern
    assert _best_glob_match(pattern, inventory) == expected
    assert _best_glob_match(pattern, list(reversed(inventory))) == expected
    assert select_model_pattern(pattern, inventory) == expected