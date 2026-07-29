"""Smoke tests for the checked-in TUI reality-check harness."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

# Like tests/test_tui_pty.py, this drives a child process on a fresh openpty
# pair, so it runs in the default suite guarded only by PTY availability.
from reality_check import tui_harness


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="PTY support required")
def test_reality_harness_generates_artifacts_and_reports_optional_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tui = tmp_path / "fake_tui.py"
    # Raw string: the escape sequences (and "\n") must land textually in the
    # generated script so the child process interprets them, and so dedent
    # sees uniform indentation.
    fake_tui.write_text(
        textwrap.dedent(
            r"""
            import sys

            sys.stdout.write("\x1b[?1049h\x1b[2J\x1b[32mAmplifier fake TUI\x1b[0m\n")
            sys.stdout.write("footer: shift-tab mode · enter send\n")
            sys.stdout.write("amplifier > ")
            sys.stdout.flush()
            line = sys.stdin.readline()
            sys.stdout.write(f"\nuser said: {line}")
            sys.stdout.write("tool output: done\n")
            sys.stdout.write("\x1b[?1049l")
            sys.stdout.flush()
            """
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "artifacts"
    monkeypatch.setattr(tui_harness.shutil, "which", lambda _name: None)
    monkeypatch.delenv("AMPLIFIER_TUI_GUI_CLIPBOARD_BRIDGE", raising=False)

    summary = tui_harness.run_scripted_check(
        tui_harness.HarnessConfig(
            command=[sys.executable, str(fake_tui)],
            cwd=tmp_path,
            output_dir=output_dir,
            timeout_seconds=5,
        ),
        [
            {"type": "wait", "text": "shift-tab mode", "name": "initial_footer"},
            {"type": "send", "text": "hello harness\n", "name": "submit_message"},
            {"type": "wait", "text": "tool output: done", "name": "tool_output"},
            {"type": "wait_exit", "name": "process_exit"},
        ],
    )

    assert summary["overall_status"] == "PASS"
    assert summary["checks"]["initial_footer"]["status"] == "PASS"
    assert summary["checks"]["tool_output"]["status"] == "PASS"
    assert summary["checks"]["artifacts"]["status"] == "PASS"
    assert summary["checks"]["png_render"]["status"] == "SKIP"
    assert "png_unavailable" in summary["checks"]["png_render"]["reason"]
    assert summary["checks"]["clipboard_image_paste"]["status"] == "SKIP"
    assert summary["checks"]["clipboard_image_paste"]["capability"] == "UNSUPPORTED"

    raw = (output_dir / "raw.ansi").read_text(encoding="utf-8")
    clean = (output_dir / "clean.txt").read_text(encoding="utf-8")
    transcript = (output_dir / "transcript.html").read_text(encoding="utf-8")
    action_log = json.loads(
        (output_dir / "action_log.json").read_text(encoding="utf-8")
    )
    persisted_summary = json.loads(
        (output_dir / "summary.json").read_text(encoding="utf-8")
    )

    assert "\x1b[32m" in raw
    assert "\x1b[" not in clean
    assert "hello harness" in clean
    assert "<!doctype html>" in transcript.lower()
    assert "tool output: done" in transcript
    assert any(entry["name"] == "submit_message" for entry in action_log)
    assert persisted_summary["overall_status"] == summary["overall_status"]
