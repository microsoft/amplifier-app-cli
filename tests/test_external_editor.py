"""External editor round-trip: draft -> .md tempfile -> $VISUAL/$EDITOR -> draft."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from prompt_toolkit.output import DummyOutput

from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.keyboard_protocol import KEYBOARD_ENHANCEMENT_DISABLE
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl_input import editor_command


def _make_app(tmp_path) -> LayeredReplApp:
    registry = CommandRegistry.from_legacy({"/help": {"description": "Show help"}})
    return LayeredReplApp(
        config=LayeredReplConfig(
            history_path=tmp_path / "history",
            completion=LayeredReplCompletion(registry),
            output=DummyOutput(),
        ),
        bindings=LayeredReplBindings(on_submit=lambda submission: None),
    )


def _write_editor_script(path: Path, body: str) -> str:
    """A fake $EDITOR: a shell script that receives the tempfile as $1."""
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture(autouse=True)
def _clear_editor_env(monkeypatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)


@pytest.mark.asyncio
async def test_editor_rewrites_draft_through_md_tempfile(tmp_path, monkeypatch) -> None:
    app = _make_app(tmp_path)
    app.input_buffer.text = "original draft\nsecond line"
    capture = tmp_path / "capture"
    monkeypatch.setenv(
        "EDITOR",
        _write_editor_script(
            tmp_path / "editor.sh",
            f'echo "$1" > "{capture}.path"\n'
            f'cat "$1" > "{capture}.seed"\n'
            'printf "rewritten by editor\\n" > "$1"',
        ),
    )

    task = app.open_external_editor()
    assert task is not None
    await task

    # Draft replaced on clean exit; the editor's trailing newline is dropped.
    assert app.input_buffer.text == "rewritten by editor"
    notice = app._notices.current()
    assert notice is not None
    assert notice.text == "draft updated from editor"
    # The tempfile carried the .md suffix and the full draft, and is gone now.
    tempfile_path = (capture.with_suffix(".path")).read_text().strip()
    assert tempfile_path.endswith(".md")
    assert not Path(tempfile_path).exists()
    assert (capture.with_suffix(".seed")).read_text() == ("original draft\nsecond line")


@pytest.mark.asyncio
async def test_visual_wins_over_editor(tmp_path, monkeypatch) -> None:
    app = _make_app(tmp_path)
    monkeypatch.setenv(
        "VISUAL",
        _write_editor_script(tmp_path / "visual.sh", 'printf "visual won" > "$1"'),
    )
    monkeypatch.setenv(
        "EDITOR",
        _write_editor_script(tmp_path / "editor.sh", 'printf "editor won" > "$1"'),
    )

    task = app.open_external_editor()
    assert task is not None
    await task

    assert app.input_buffer.text == "visual won"


@pytest.mark.asyncio
async def test_editor_nonzero_exit_leaves_draft_unchanged(
    tmp_path, monkeypatch
) -> None:
    app = _make_app(tmp_path)
    app.input_buffer.text = "original draft"
    monkeypatch.setenv(
        "EDITOR",
        _write_editor_script(
            tmp_path / "editor.sh",
            'printf "must not land" > "$1"\nexit 1',
        ),
    )

    task = app.open_external_editor()
    assert task is not None
    await task

    # Codex parity: the draft is only replaced on a clean editor exit.
    assert app.input_buffer.text == "original draft"


@pytest.mark.asyncio
async def test_missing_editor_shows_error_notice(tmp_path) -> None:
    app = _make_app(tmp_path)
    app.input_buffer.text = "original draft"

    assert app.open_external_editor() is None

    assert app.input_buffer.text == "original draft"
    notice = app._notices.current()
    assert notice is not None
    assert notice.text == "set $VISUAL or $EDITOR to edit the draft"


@pytest.mark.asyncio
async def test_unlaunchable_editor_leaves_draft_unchanged(
    tmp_path, monkeypatch
) -> None:
    app = _make_app(tmp_path)
    app.input_buffer.text = "original draft"
    monkeypatch.setenv("EDITOR", str(tmp_path / "does-not-exist"))

    task = app.open_external_editor()
    assert task is not None
    await task

    assert app.input_buffer.text == "original draft"
    notice = app._notices.current()
    assert notice is not None
    assert notice.text.startswith("could not launch editor")


@pytest.mark.asyncio
async def test_editor_pops_keyboard_enhancements_for_the_subprocess(
    tmp_path, monkeypatch
) -> None:
    app = _make_app(tmp_path)
    app.input_buffer.text = "draft"
    terminal = io.StringIO()
    app._terminal_file = terminal
    app._keyboard_enhancements_active = True
    monkeypatch.setenv(
        "EDITOR",
        _write_editor_script(tmp_path / "editor.sh", 'printf "edited" > "$1"'),
    )

    task = app.open_external_editor()
    assert task is not None
    await task

    # The editor received a legacy keyboard; the next render re-pushes the
    # enhancements (layered_repl_terminal._sync_keyboard_enhancements).
    assert KEYBOARD_ENHANCEMENT_DISABLE in terminal.getvalue()
    assert app._keyboard_enhancements_active is False
    assert app.input_buffer.text == "edited"


@pytest.mark.asyncio
async def test_second_open_while_editor_running_returns_same_task(
    tmp_path, monkeypatch
) -> None:
    app = _make_app(tmp_path)
    monkeypatch.setenv(
        "EDITOR",
        _write_editor_script(tmp_path / "editor.sh", 'printf "edited" > "$1"'),
    )
    pending: asyncio.Task[None] = asyncio.get_running_loop().create_task(
        asyncio.sleep(30)
    )
    app._external_editor_task = pending
    try:
        assert app.open_external_editor() is pending
        notice = app._notices.current()
        assert notice is not None
        assert notice.text == "editor already open"
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending


@pytest.mark.asyncio
async def test_collapsed_paste_expands_before_editing(tmp_path, monkeypatch) -> None:
    app = _make_app(tmp_path)
    pasted = "\n".join(f"pasted line {index}" for index in range(60))
    app._insert_text_paste(pasted, pasted)
    assert pasted not in app.input_buffer.text  # collapsed to a stub token
    monkeypatch.setenv(
        "EDITOR",
        _write_editor_script(tmp_path / "editor.sh", "exit 0"),
    )

    task = app.open_external_editor()
    assert task is not None
    await task

    # The editor saw (and kept) the real pasted content, not the stub.
    assert app.input_buffer.text == pasted


def test_editor_command_prefers_visual_and_shell_splits(monkeypatch) -> None:
    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "vim")
    assert editor_command() == ["code", "--wait"]

    monkeypatch.delenv("VISUAL")
    assert editor_command() == ["vim"]

    monkeypatch.setenv("EDITOR", "")
    assert editor_command() is None

    monkeypatch.setenv("EDITOR", "'unterminated")
    assert editor_command() is None
