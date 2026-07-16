"""Shared test helper factories for amplifier-app-cli tests."""

from __future__ import annotations

import difflib
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from amplifier_app_cli.main import CommandProcessor

GOLDENS_DIR = Path(__file__).resolve().parent / "goldens"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_OSC8_HYPERLINK = re.compile(r"\x1b\]8;[^\x1b\x07]*(?:\x07|\x1b\\)")
_REGEN_HINT = "uv run python tests/regen_goldens.py --write"


def normalize_for_golden(text: str) -> str:
    """Canonicalize rendered TUI text for byte-stable golden comparison.

    Golden *inputs* must already be deterministic (fixed ``Telemetry`` values,
    fixed session ids); this only scrubs environment-dependent artifacts:

    - strips OSC 8 hyperlink escapes (the visible link text stays)
    - replaces the project root and the temp dir with stable placeholders
    - trims trailing whitespace per line (invisible in a terminal; keeps the
      checked-in goldens safe from editor/git whitespace munging)
    - guarantees exactly one trailing newline

    Every golden write and read goes through this function.
    """
    text = _OSC8_HYPERLINK.sub("", text)
    text = text.replace(str(_PROJECT_ROOT), "<project>")
    tmp = tempfile.gettempdir().rstrip("/")
    text = text.replace(tmp, "<tmp>")
    if tmp != "/tmp":
        text = text.replace("/tmp", "<tmp>")
    normalized = "\n".join(line.rstrip() for line in text.split("\n"))
    return normalized.rstrip("\n") + "\n"


def write_golden(rendered: str, golden_path: Path) -> None:
    """Write a golden snapshot file (normalized) creating parents as needed."""
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    golden_path.write_text(normalize_for_golden(rendered), encoding="utf-8")


def assert_matches_golden(rendered: str, golden_path: Path) -> None:
    """Assert rendered text equals the checked-in golden.

    On mismatch, fail with a unified diff of the rendered screen — read it as
    a UI diff (before/after screens), not as an equality dump.
    """
    actual = normalize_for_golden(rendered)
    if not golden_path.exists():
        raise AssertionError(f"missing golden {golden_path}\nregenerate: {_REGEN_HINT}")
    expected = golden_path.read_text(encoding="utf-8")
    if actual == expected:
        return
    diff = "\n".join(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=f"goldens/{golden_path.parent.name}/{golden_path.name}",
            tofile="rendered",
            lineterm="",
        )
    )
    raise AssertionError(
        f"rendered output diverges from {golden_path.name} — UI diff:\n{diff}\n"
        f"if this change is intentional: {_REGEN_HINT} "
        "(and update docs/designs/tui-v3-cohesive.md in the same commit)"
    )


def _make_command_processor(
    skills_discovery=None, mode_shortcuts=None, configurator=None
):
    """Create a CommandProcessor with mocked session for unit testing."""
    mock_session = MagicMock()
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.session_state = {
        "active_mode": None,
    }
    mock_session.coordinator.get_capability.return_value = None

    if mode_shortcuts is not None:
        mock_mode_discovery = MagicMock()
        mock_mode_discovery.get_shortcuts.return_value = mode_shortcuts
        mock_session.coordinator.session_state["mode_discovery"] = mock_mode_discovery

    if skills_discovery is not None:
        original_get_capability = mock_session.coordinator.get_capability

        def _get_capability(key):
            if key == "skills_discovery":
                return skills_discovery
            return original_get_capability(key)

        mock_session.coordinator.get_capability = _get_capability

    cp = CommandProcessor(mock_session, "test-bundle")
    if configurator is not None:
        cp.configurator = configurator
    return cp
