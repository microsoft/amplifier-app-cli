"""`AMPLIFIER_HOME` is the only home the CLI may compose paths from.

Foundation's ``get_amplifier_home()`` is the single source of truth: it honours
``AMPLIFIER_HOME`` and falls back to ``~/.amplifier``. Code that instead writes
``Path.home() / ".amplifier" / ...`` looks identical in the default layout and
silently splits in two the moment ``AMPLIFIER_HOME`` points somewhere else --
bundle discovery reading the registry from one home while the caches, settings
and install state come from another.

Two tests, deliberately different in kind:

* a *behavioural* test -- discovery reads the registry from ``AMPLIFIER_HOME``
  even when ``HOME`` holds a perfectly good decoy registry; and
* a *structural* guard -- no module in ``amplifier_app_cli`` composes a
  ``.amplifier`` path from ``Path.home()`` again, so the class of bug cannot
  come back through a file this test never heard of.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import amplifier_app_cli


# ---------------------------------------------------------------------------
# Behaviour: AMPLIFIER_HOME wins over HOME
# ---------------------------------------------------------------------------


def _write_registry(amplifier_dir: Path, bundle_name: str) -> None:
    amplifier_dir.mkdir(parents=True, exist_ok=True)
    (amplifier_dir / "registry.json").write_text(
        json.dumps(
            {
                "bundles": {
                    bundle_name: {
                        "uri": f"git+https://example.invalid/{bundle_name}",
                        "is_root": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def split_homes(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """Point ``HOME`` and ``AMPLIFIER_HOME`` at two *different* registries.

    The decoy under ``HOME`` is the whole point: an assertion that only checks
    "the isolated bundle is present" passes even when the code is still reading
    ``~/.amplifier``. Asserting the decoy is *absent* is what actually proves
    which home won.
    """
    decoy_home = tmp_path / "decoy-home"
    _write_registry(decoy_home / ".amplifier", "decoy-bundle-from-home")

    isolated = tmp_path / "isolated" / ".amplifier"
    _write_registry(isolated, "isolated-bundle-from-amplifier-home")

    monkeypatch.setenv("HOME", str(decoy_home))
    monkeypatch.setenv("USERPROFILE", str(decoy_home))  # Path.home() on Windows
    monkeypatch.setenv("AMPLIFIER_HOME", str(isolated))
    return decoy_home, isolated


def test_discovery_reads_registry_from_amplifier_home(split_homes):
    """Discovery enumerates AMPLIFIER_HOME's registry, not HOME's."""
    from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery

    discovery = AppBundleDiscovery()

    all_names = discovery._read_all_from_registry()
    roots, _nested = discovery._get_root_and_nested_bundles()

    assert "isolated-bundle-from-amplifier-home" in all_names
    assert "isolated-bundle-from-amplifier-home" in roots

    assert "decoy-bundle-from-home" not in all_names, (
        "discovery read ~/.amplifier/registry.json instead of AMPLIFIER_HOME's"
    )
    assert "decoy-bundle-from-home" not in roots


def test_cli_paths_follow_amplifier_home(split_homes):
    """The CLI's own path policy resolves under AMPLIFIER_HOME too.

    Install state and global settings living in a different home than the
    registry is exactly the split-brain this fix exists to prevent.
    """
    _decoy_home, isolated = split_homes

    from amplifier_app_cli.lib.settings import SettingsPaths
    from amplifier_app_cli.paths import get_install_state_path

    assert get_install_state_path() == isolated / "cache" / "install-state.json"
    assert SettingsPaths.default().global_settings == isolated / "settings.yaml"


# ---------------------------------------------------------------------------
# Guard: the composition cannot come back
# ---------------------------------------------------------------------------

_PACKAGE_ROOT = Path(amplifier_app_cli.__file__).resolve().parent


def _is_path_home_call(node: ast.AST) -> bool:
    """True for ``Path.home()`` / ``pathlib.Path.home()``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "home"
        and isinstance(node.func.value, ast.Attribute | ast.Name)
        and (
            node.func.value.id == "Path"
            if isinstance(node.func.value, ast.Name)
            else node.func.value.attr == "Path"
        )
    )


def _joins_amplifier(node: ast.AST) -> bool:
    """True for ``<something> / ".amplifier"``."""
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and isinstance(node.right, ast.Constant)
        and node.right.value == ".amplifier"
    )


def _home_aliases(tree: ast.AST) -> set[str]:
    """Names ever bound to ``Path.home()`` anywhere in the module."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_path_home_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
    return aliases


def _violations(path: Path) -> list[str]:
    """Report every ``.amplifier`` path composed from the OS home directory."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _home_aliases(tree)
    found: list[str] = []

    for node in ast.walk(tree):
        # Path.home() / ".amplifier" -- and the one-hop `home = Path.home()`
        # form, which reads innocently and is exactly how routing.py hid one.
        if _joins_amplifier(node):
            left = node.left  # type: ignore[attr-defined]
            if _is_path_home_call(left) or (
                isinstance(left, ast.Name) and left.id in aliases
            ):
                found.append(f"{path.relative_to(_PACKAGE_ROOT)}:{node.lineno}")
        # expanduser("~/.amplifier...")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "expanduser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("~/.amplifier")
        ):
            found.append(f"{path.relative_to(_PACKAGE_ROOT)}:{node.lineno}")

    return found


def test_no_module_composes_amplifier_home_from_path_home():
    """No file in the package may rebuild ~/.amplifier by hand.

    Use ``get_amplifier_home()`` from
    ``amplifier_foundation.paths.resolution`` -- it honours AMPLIFIER_HOME and
    falls back to ``~/.amplifier``, so both layouts keep working.
    """
    sources = sorted(_PACKAGE_ROOT.rglob("*.py"))
    assert sources, "guard found no sources to scan -- it would pass vacuously"

    violations = [v for src in sources for v in _violations(src)]

    assert not violations, (
        "these compose a .amplifier path from the OS home directory, ignoring "
        "AMPLIFIER_HOME -- use get_amplifier_home() instead:\n  "
        + "\n  ".join(violations)
    )
