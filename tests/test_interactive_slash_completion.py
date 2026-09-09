"""End-to-end and pure-engine coverage for interactive slash completion."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
from collections import namedtuple
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_app_cli.main import CommandProcessor, _create_prompt_session
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.ui.completion import (
    CompletionSnapshot,
    SlashCompleter,
    SlashCompletionEngine,
    build_completion_snapshot,
)
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


def _engine() -> SlashCompletionEngine:
    return SlashCompletionEngine(
        CompletionSnapshot(
            commands={
                "/provider": "Provider controls\nunsafe",
                "/config": "Live configuration",
                "/clear": "Clear context",
                "/goal": "Set a goal",
                "/status": "Show session status",
                "/exit": "Exit this session",
                "/quit": "Alias for /exit",
            },
            mode_shortcuts={"p": "plan"},
            modes={"plan": "Plan work"},
            skill_shortcuts={
                "review": {"name": "review", "description": "Review a result"},
                "rvw": {"name": "review", "description": "Review a result"},
                "memory": {"name": "memory", "description": "Persistent preferences"},
                "p": {"name": "review", "description": "Colliding review skill"},
                "status": {"name": "review", "description": "Colliding status skill"},
            },
            skills=[
                {
                    "name": "review",
                    "aliases": ["rvw"],
                    "description": "Review a result",
                    "argument_hint": "[target]",
                    "arguments": [
                        {"after": [], "values": ["Fast", "thorough"]},
                        {"after": ["thorough"], "values": ["security"]},
                    ],
                },
                {
                    "name": "memory",
                    "aliases": [],
                    "description": "Persistent preferences",
                    "argument_hint": None,
                    "arguments": [
                        {
                            "after": [],
                            "values": [
                                "list",
                                "review",
                                "forget",
                                "edit",
                                "remember",
                                "help",
                            ],
                        },
                        {"after": ["review"], "values": ["accept", "decline", "skip"]},
                    ],
                },
            ],
            providers=("alpha", "beta"),
            config_items={
                "tools": (("bash", True), ("browser", False)),
                "providers": (("alpha", True),),
            },
        )
    )


def _values(engine: SlashCompletionEngine, text: str, cursor: int | None = None) -> list[str]:
    return [candidate.value for candidate in engine.complete(text, cursor)]


def test_engine_completes_commands_provider_modes_and_goal() -> None:
    engine = _engine()

    assert _values(engine, "/pro") == ["/provider"]
    assert _values(engine, "/exit ") == []
    assert _values(engine, "/quit ") == []
    assert {"/clear", "/config"}.issubset(_values(engine, "/c"))
    assert _values(engine, "/provider ") == ["auto", "use", "test", "models"]
    assert _values(engine, "/provider use ") == ["alpha", "beta"]
    assert _values(engine, "/mode ") == ["off", "info", "plan"]
    assert _values(engine, "/mode plan ") == ["on", "off"]
    assert _values(engine, "/mode info ") == ["plan"]
    assert _values(engine, "/p ") == ["on", "off"]
    assert "--max-turns" in _values(engine, "/goal ")
    assert _values(engine, "/goal --max-turns ") == []
    assert _values(engine, "/goal finish the task ") == []
    assert _values(engine, "/allowed-dirs ") == ["list", "add", "remove"]
    assert _values(engine, "/denied-dirs add ") == []
    assert _values(engine, "/status ") == []


def test_engine_completes_config_only_in_parser_valid_positions() -> None:
    engine = _engine()

    assert {"show", "diff", "save", "tools", "--format"}.issubset(
        _values(engine, "/config ")
    )
    assert _values(engine, "/config --format ") == ["text", "json"]
    assert _values(engine, "/config save --sc") == ["--scope"]
    assert _values(engine, "/config save --scope ") == ["project", "global"]
    assert _values(engine, "/config --scope ") == []
    assert "--compact" in _values(engine, "/config show tools ")
    assert "--trees" not in _values(engine, "/config --detailed ")
    assert _values(engine, "/config tools disable ") == ["bash"]
    assert _values(engine, "/config tools enable ") == ["browser"]
    assert _values(engine, "/config set path ") == []
    assert "enable" not in _values(engine, "/config hooks ")
    assert "disable" not in _values(engine, "/config hooks ")


def test_engine_skill_catalog_aliases_hints_and_memory_sidecar() -> None:
    engine = _engine()

    names = engine.complete("/skill ")
    assert {candidate.value for candidate in names} == {"review", "rvw", "memory"}
    assert next(candidate for candidate in names if candidate.value == "review").description.startswith(
        "[target]"
    )
    assert _values(engine, "/skill rvw ") == ["Fast", "thorough"]
    assert _values(engine, "/skill rvw f") == []
    assert _values(engine, "/skill rvw F") == ["Fast"]
    assert _values(engine, "/rvw thorough ") == ["security"]
    direct = next(candidate for candidate in engine.complete("/rvw") if candidate.value == "/rvw")
    assert direct.description.endswith("Review a result")
    assert direct.description.startswith("[target]")
    assert _values(engine, "/skill memory ") == [
        "list",
        "review",
        "forget",
        "edit",
        "remember",
        "help",
    ]
    assert _values(engine, "/memory review ") == ["accept", "decline", "skip"]


def test_engine_never_completes_prose_or_unsafe_cursor_suffix() -> None:
    engine = _engine()

    assert _values(engine, "write /pro") == []
    assert _values(engine, "/proXvider", 4) == []
    assert _values(engine, "/provider", 4) == []


def test_completer_reads_only_the_existing_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    completer = SlashCompleter(engine)
    monkeypatch.setattr(
        engine,
        "refresh",
        lambda snapshot: (_ for _ in ()).throw(AssertionError("keypress refresh")),
    )

    completions = list(completer.get_completions(__import__("prompt_toolkit").document.Document("/pro"), None))
    assert [completion.text for completion in completions] == ["/provider "]


class _Discovery:
    def __init__(self) -> None:
        self.shortcuts = {"rvw": {"name": "review", "description": "Review"}}

    def get_shortcuts(self):
        return self.shortcuts

    def list_skills(self):
        return [("review", "Review")]


def test_snapshot_refreshes_live_cached_values_and_old_skill_fallback() -> None:
    discovery = _Discovery()
    coordinator = MagicMock()
    coordinator.session_state = {"active_mode": None}
    coordinator.get_capability.side_effect = lambda name: discovery if name == "skills_discovery" else None
    providers = {"one": object()}
    coordinator.get.side_effect = lambda name: providers if name == "providers" else None
    session = SimpleNamespace(coordinator=coordinator)
    processor = CommandProcessor(session)

    first = build_completion_snapshot(processor)
    assert first.providers == ("one",)
    assert first.skills[0]["name"] == "review"
    assert first.skills[0]["aliases"] == ["rvw"]
    assert first.skills[0]["arguments"] == []

    providers["two"] = object()
    discovery.shortcuts["audit"] = {"name": "audit", "description": "Audit"}
    second = build_completion_snapshot(processor)
    assert second.providers == ("one", "two")
    assert "audit" in second.skill_shortcuts


def test_snapshot_reads_actual_modelisting_shape() -> None:
    ModeListing = namedtuple("ModeListing", ["name", "description", "source", "advertised"])
    discovery = _Discovery()
    modes = MagicMock()
    modes.get_shortcuts.return_value = {"p": "plan"}
    modes.list_modes.return_value = [ModeListing("plan", "Think first", "modes", True)]
    coordinator = MagicMock()
    coordinator.session_state = {"active_mode": None, "mode_discovery": modes}
    coordinator.get_capability.side_effect = lambda name: discovery if name == "skills_discovery" else None
    coordinator.get.return_value = {}
    snapshot = build_completion_snapshot(CommandProcessor(SimpleNamespace(coordinator=coordinator)))

    assert snapshot.modes == {"plan": "Think first"}
    assert snapshot.mode_shortcuts == {"p": "plan", "plan": "plan"}


@pytest.mark.asyncio
async def test_config_save_completion_round_trips_handler_syntax() -> None:
    coordinator = MagicMock()
    coordinator.session_state = {"active_mode": None}
    coordinator.get_capability.return_value = None
    processor = CommandProcessor(SimpleNamespace(coordinator=coordinator))
    processor.configurator = MagicMock()
    processor._handle_config_save = AsyncMock(return_value="saved")

    assert await processor._get_config_display("save --scope project") == "saved"
    processor._handle_config_save.assert_awaited_once_with("project")


@pytest.fixture
def prompt_factory(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Create real PromptSessions with a pipe input and DummyOutput."""
    main_module = importlib.import_module("amplifier_app_cli.main")

    original_init = PromptSession.__init__

    def dummy_output_init(self, *args, **kwargs):
        kwargs["output"] = DummyOutput()
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(PromptSession, "__init__", dummy_output_init)
    monkeypatch.setattr(main_module, "get_amplifier_home", lambda: tmp_path / ".amplifier")

    def build(snapshot: CompletionSnapshot, *, auto_popup_enabled: bool = True):
        pipe_context = create_pipe_input()
        pipe = pipe_context.__enter__()
        monkeypatch.setattr(main_module, "get_dedicated_tty_input", lambda: pipe)
        completer = SlashCompleter()
        completer.refresh(snapshot)
        return (
            _create_prompt_session(
                completer=completer, auto_popup_enabled=auto_popup_enabled
            ),
            pipe,
            pipe_context,
        )

    return build


async def _start(session):
    task = asyncio.create_task(session.prompt_async())
    await asyncio.sleep(0)
    return task


async def _wait_until_settled_complete_state(
    session, expected: list[str] | None = None
):
    """Wait for the public completion state to settle without fixed test delays."""
    for _ in range(100):
        state = session.default_buffer.complete_state
        values = (
            [completion.display_text for completion in state.completions]
            if state
            else []
        )
        if state is not None and values and (expected is None or values == expected):
            await asyncio.sleep(0)
            settled = session.default_buffer.complete_state
            settled_values = (
                [completion.display_text for completion in settled.completions]
                if settled
                else []
            )
            if settled is not None and settled_values == values:
                return settled
        await asyncio.sleep(0.01)
    raise AssertionError("completion state did not settle")


async def _wait_until_completion_closed(session, expected_text: str) -> None:
    """Wait until the menu is closed after the buffer has received an edit."""
    stable_checks = 0
    for _ in range(100):
        buffer = session.default_buffer
        if buffer.text == expected_text and buffer.complete_state is None:
            stable_checks += 1
            if stable_checks == 3:
                return
        else:
            stable_checks = 0
        await asyncio.sleep(0.01)
    raise AssertionError("completion state did not close")


async def _wait_until_selected_completion(session, expected_text: str) -> None:
    for _ in range(100):
        state = session.default_buffer.complete_state
        completion = state.current_completion if state else None
        if completion is not None and completion.text == f"{expected_text} ":
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{expected_text!r} was not selected")


async def _cancel_pending_prompt(task) -> None:
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_pipe_unique_tab_and_ctrl_j(prompt_factory) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(
            commands={"/provider": "Provider", "/quit": "Alias for /exit"}
        )
    )
    try:
        task = await _start(session)
        pipe.send_text("/pro\t\r")
        assert await asyncio.wait_for(task, 1) == "/provider "

        task = await _start(session)
        pipe.send_text("/qui\t\r")
        assert await asyncio.wait_for(task, 1) == "/quit "

        task = await _start(session)
        pipe.send_text("first\x0asecond\r")
        assert await asyncio.wait_for(task, 1) == "first\nsecond"

        session.history.append_string("previous prompt")
        task = await _start(session)
        pipe.send_text("\x1b[A\r")
        assert await asyncio.wait_for(task, 1) == "previous prompt"
    finally:
        await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_pipe_menu_navigation_escape_and_enter_does_not_submit(
    prompt_factory,
) -> None:
    snapshot = CompletionSnapshot(
        commands={"/clear": "Clear", "/config": "Config", "/context": "Context"}
    )
    session, pipe, context = prompt_factory(snapshot)
    try:
        task = await _start(session)
        pipe.send_text("/c\t")
        await asyncio.sleep(0.08)
        pipe.send_text("\x1b[B\r")  # Down chooses the first candidate.
        await asyncio.sleep(0.03)
        assert not task.done(), "first Enter must only accept the menu selection"
        pipe.send_text("\r")
        assert await asyncio.wait_for(task, 1) == "/clear "

        task = await _start(session)
        pipe.send_text("/c\t")
        await asyncio.sleep(0.08)
        pipe.send_text("\x1b[Z\r")  # Shift-Tab chooses the last candidate.
        await asyncio.sleep(0.03)
        pipe.send_text("\r")
        assert await asyncio.wait_for(task, 1) == "/context "

        task = await _start(session)
        pipe.send_text("/c\t")
        await asyncio.sleep(0.08)
        pipe.send_text("\x1b\r")
        assert await asyncio.wait_for(task, 1) == "/c"

        task = await _start(session)
        pipe.send_text("/c\t")
        await asyncio.sleep(0.08)
        pipe.send_text("l\t\r")
        assert await asyncio.wait_for(task, 1) == "/clear "
    finally:
        await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_pipe_menu_accepts_quit_before_second_enter_submits(
    prompt_factory,
) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(commands={"/query": "Query", "/quit": "Exit this session"})
    )
    try:
        task = await _start(session)
        pipe.send_text("/q\t")
        await asyncio.sleep(0.08)
        pipe.send_text("\x1b[B\x1b[B\r")
        await asyncio.sleep(0.03)
        assert not task.done(), "first Enter must only accept /quit from the menu"
        pipe.send_text("\r")
        assert await asyncio.wait_for(task, 1) == "/quit "
    finally:
        await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_pipe_typing_slash_opens_and_refilters_top_level_menu(
    prompt_factory,
) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(
            commands={
                "/clear": "Clear",
                "/config": "Config",
                "/provider": "Provider",
            }
        )
    )
    task = None
    try:
        task = await _start(session)
        pipe.send_text("/")
        state = await _wait_until_settled_complete_state(
            session, ["/clear", "/config", "/provider"]
        )
        assert state.current_completion is None
        assert session.default_buffer.text == "/"

        pipe.send_text("c")
        state = await _wait_until_settled_complete_state(session, ["/clear", "/config"])
        assert state.current_completion is None
        assert session.default_buffer.text == "/c"

        pipe.send_text("l")
        state = await _wait_until_settled_complete_state(session, ["/clear"])
        assert state.current_completion is None
        assert session.default_buffer.text == "/cl"
    finally:
        if task is not None:
            await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_pipe_disabled_auto_popup_keeps_tab_commands_arguments_and_aliases(
    prompt_factory,
) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(
            commands={
                "/clear": "Clear",
                "/config": "Config",
                "/provider": "Provider controls",
                "/quit": "Alias for /exit",
            }
        ),
        auto_popup_enabled=False,
    )
    task = None
    try:
        task = await _start(session)
        pipe.send_text("/")
        await _wait_until_completion_closed(session, "/")

        pipe.send_text("c\t")
        await _wait_until_settled_complete_state(session, ["/clear", "/config"])
        pipe.send_text("\t")
        await _wait_until_selected_completion(session, "/clear")
        pipe.send_text("\r")
        await _wait_until_completion_closed(session, "/clear ")
        assert not task.done(), "first Enter must only accept the menu selection"
        pipe.send_text("\r")
        assert await asyncio.wait_for(task, 1) == "/clear "

        task = await _start(session)
        pipe.send_text("/qui\t\r")
        assert await asyncio.wait_for(task, 1) == "/quit "

        task = await _start(session)
        pipe.send_text("/provider \t")
        await _wait_until_settled_complete_state(
            session, ["auto", "use", "test", "models"]
        )
        pipe.send_text("\t")
        await _wait_until_selected_completion(session, "auto")
    finally:
        if task is not None:
            await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_pipe_enter_accepts_auto_provider_completion_without_opening_arguments(
    prompt_factory,
) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(commands={"/provider": "Provider controls"})
    )
    task = None
    try:
        task = await _start(session)
        pipe.send_text("/pro")
        state = await _wait_until_settled_complete_state(session, ["/provider"])
        assert state.current_completion is None

        pipe.send_text("\r")
        await _wait_until_completion_closed(session, "/provider ")
        assert not task.done(), "first Enter must only accept the menu selection"

        pipe.send_text("\r")
        assert await asyncio.wait_for(task, 1) == "/provider "
    finally:
        if task is not None:
            await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.parametrize(
    ("settings_yaml", "expected"),
    [
        (None, True),
        ("{}", True),
        ("ui: {slash_popup: {enabled: false}}", False),
        ("ui: {slash_popup: {enabled: true}}", True),
        ("ui: false", True),
        ("ui: []", True),
        ("ui: {slash_popup: false}", True),
        ("ui: {slash_popup: []}", True),
        ("ui: {slash_popup: {enabled: 'false'}}", True),
        ("ui: {slash_popup: {enabled: null}}", True),
        ("ui: {slash_popup: {enabled: 0}}", True),
    ],
)
def test_slash_popup_setting_defaults_except_for_yaml_booleans(
    tmp_path: Path, settings_yaml: str | None, expected: bool
) -> None:
    global_settings = tmp_path / "global" / "settings.yaml"
    if settings_yaml is not None:
        global_settings.parent.mkdir()
        global_settings.write_text(settings_yaml, encoding="utf-8")
    settings = AppSettings(
        SettingsPaths(
            global_settings=global_settings,
            project_settings=tmp_path / "project" / "settings.yaml",
            local_settings=tmp_path / "project" / "settings.local.yaml",
        )
    )

    assert settings.get_slash_popup_enabled() is expected


def test_slash_popup_setting_uses_global_project_local_precedence(tmp_path: Path) -> None:
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / ".amplifier" / "settings.yaml",
        local_settings=tmp_path / "project" / ".amplifier" / "settings.local.yaml",
    )
    for path, enabled in (
        (paths.global_settings, False),
        (paths.project_settings, True),
        (paths.local_settings, False),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"ui: {{slash_popup: {{enabled: {'true' if enabled else 'false'}}}}}",
            encoding="utf-8",
        )

    assert AppSettings(paths).get_slash_popup_enabled() is False
    paths.local_settings.unlink()
    assert AppSettings(paths).get_slash_popup_enabled() is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings_yaml", "initial_transcript", "expected"),
    [
        (None, None, True),
        ("ui: {slash_popup: {enabled: false}}", None, False),
        ("ui: {slash_popup: {enabled: true}}", [{"role": "user", "content": "old"}], True),
    ],
)
async def test_interactive_chat_resolves_slash_popup_once_before_prompt_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings_yaml: str | None,
    initial_transcript: list[dict] | None,
    expected: bool,
) -> None:
    main_module = importlib.import_module("amplifier_app_cli.main")
    settings_module = importlib.import_module("amplifier_app_cli.lib.settings")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    app_home = tmp_path / "home" / ".amplifier"
    if settings_yaml is not None:
        app_home.mkdir(parents=True)
        (app_home / "settings.yaml").write_text(settings_yaml, encoding="utf-8")
    monkeypatch.setattr(settings_module, "get_amplifier_home", lambda: app_home)

    context = MagicMock()
    context.get_messages = AsyncMock(return_value=[])
    coordinator = MagicMock()
    coordinator.session_state = {}
    coordinator.get.side_effect = (
        lambda key: context if key == "context" else {} if key == "providers" else None
    )
    coordinator.get_capability.return_value = None
    session = SimpleNamespace(
        coordinator=coordinator,
        config={},
        execute=AsyncMock(),
    )
    initialized = SimpleNamespace(
        session=session,
        session_id="test-session-id",
        configurator=None,
        cleanup=AsyncMock(),
    )
    prompt_session = MagicMock()
    prompt_session.prompt_async = AsyncMock(side_effect=EOFError)
    create_prompt_session = MagicMock(return_value=prompt_session)
    settings_reads = 0
    real_get_merged_settings = settings_module.AppSettings.get_merged_settings

    def count_settings_reads(instance):
        nonlocal settings_reads
        settings_reads += 1
        return real_get_merged_settings(instance)

    monkeypatch.setattr(
        settings_module.AppSettings, "get_merged_settings", count_settings_reads
    )
    monkeypatch.setattr(
        main_module,
        "create_initialized_session",
        AsyncMock(return_value=initialized),
    )
    monkeypatch.setattr(main_module, "_create_prompt_session", create_prompt_session)
    monkeypatch.setattr(main_module, "SessionStore", MagicMock())
    monkeypatch.setattr(main_module, "console", MagicMock())
    monkeypatch.setattr(
        main_module, "patch_stdout", lambda *_args, **_kwargs: contextlib.nullcontext()
    )
    monkeypatch.setattr(main_module, "close_dedicated_tty_input", MagicMock())
    monkeypatch.setattr(main_module, "get_effective_config_summary", MagicMock())
    monkeypatch.setattr(
        importlib.import_module("amplifier_app_cli.incremental_save"),
        "register_incremental_save",
        MagicMock(),
    )
    monkeypatch.setattr(
        importlib.import_module("amplifier_app_cli.goal_progress_hook"),
        "register_goal_progress_hook",
        MagicMock(),
    )
    monkeypatch.setattr(
        importlib.import_module("amplifier_app_cli.goal_circuit_breaker"),
        "register_goal_circuit_breaker",
        MagicMock(),
    )

    await main_module.interactive_chat(
        config={},
        search_paths=[tmp_path],
        verbose=False,
        bundle_name="test-bundle",
        initial_transcript=initial_transcript,
    )

    assert create_prompt_session.call_args.kwargs["auto_popup_enabled"] is expected
    assert settings_reads == 1
    prompt_session.prompt_async.assert_awaited_once()
    session.execute.assert_not_awaited()
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_text", "expected_text"),
    [
        ("write /provider", "write /provider"),
        ("https://example.test/", "https://example.test/"),
        ("first\x0a/provider", "first\n/provider"),
    ],
)
async def test_pipe_auto_completion_ignores_non_top_level_slash_text(
    prompt_factory, input_text, expected_text
) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(commands={"/provider": "Provider controls"})
    )
    task = None
    try:
        task = await _start(session)
        pipe.send_text(input_text)
        await _wait_until_completion_closed(session, expected_text)
    finally:
        if task is not None:
            await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_pipe_midtoken_edits_do_not_reopen_and_tab_still_completes_arguments(
    prompt_factory,
) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(commands={"/provider": "Provider controls"})
    )
    task = None
    try:
        task = await _start(session)
        pipe.send_text("/provider")
        await _wait_until_settled_complete_state(session, ["/provider"])
        pipe.send_text("\x1b[Dx")
        await _wait_until_completion_closed(session, "/providexr")
    finally:
        if task is not None:
            await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_pipe_automatic_completion_does_not_race_into_arguments(
    prompt_factory,
) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(commands={"/provider": "Provider controls"})
    )
    task = None
    try:
        task = await _start(session)
        pipe.send_text("/provider ")
        await _wait_until_completion_closed(session, "/provider ")

        pipe.send_text("\t")
        state = await _wait_until_settled_complete_state(
            session, ["auto", "use", "test", "models"]
        )
        assert state.current_completion is None
        pipe.send_text("\t")
        await _wait_until_selected_completion(session, "auto")
        pipe.send_text("\t")
        await _wait_until_selected_completion(session, "use")
        pipe.send_text("\x1b[Z")
        await _wait_until_selected_completion(session, "auto")
    finally:
        if task is not None:
            await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_pipe_rapid_provider_argument_text_does_not_open_a_menu(
    prompt_factory,
) -> None:
    session, pipe, context = prompt_factory(
        CompletionSnapshot(commands={"/provider": "Provider controls"})
    )
    task = None
    try:
        task = await _start(session)
        pipe.send_text("/provider u")
        await _wait_until_completion_closed(session, "/provider u")
    finally:
        if task is not None:
            await _cancel_pending_prompt(task)
        context.__exit__(None, None, None)


def test_exit_commands_are_static_and_cannot_be_shadowed() -> None:
    modes = MagicMock()
    modes.get_shortcuts.return_value = {"exit": "mode-exit", "quit": "mode-quit"}
    skills = MagicMock()
    skills.get_shortcuts.return_value = {
        "exit": {"name": "exit-skill", "description": "Shadow exit"},
        "quit": {"name": "quit-skill", "description": "Shadow quit"},
    }
    coordinator = MagicMock()
    coordinator.session_state = {"active_mode": None, "mode_discovery": modes}
    coordinator.get_capability.side_effect = (
        lambda name: skills if name == "skills_discovery" else None
    )
    coordinator.get.return_value = {}
    processor = CommandProcessor(SimpleNamespace(coordinator=coordinator))

    assert processor.process_input("/EXIT") == (
        "exit",
        {"args": "", "command": "/exit"},
    )
    assert processor.process_input("/quit later") == (
        "exit",
        {"args": "later", "command": "/quit"},
    )
    assert CommandProcessor.COMMANDS["/exit"]["description"] == "Exit this session"
    assert CommandProcessor.COMMANDS["/quit"]["description"] == "Alias for /exit"

    help_text = processor._format_help()
    assert "  /exit        - Exit this session" in help_text
    assert "  /quit        - Alias for /exit" in help_text

    snapshot = build_completion_snapshot(processor)
    assert snapshot.commands["/exit"] == "Exit this session"
    assert snapshot.commands["/quit"] == "Alias for /exit"
    engine = SlashCompletionEngine(snapshot)
    assert _values(engine, "/e") == ["/exit"]
    assert _values(engine, "/q") == ["/quit"]


def test_command_processor_aliases_are_session_scoped_and_canonical() -> None:
    skills = _Discovery()
    first = MagicMock()
    first.coordinator.session_state = {
        "active_mode": None,
        "mode_discovery": MagicMock(get_shortcuts=lambda: {"p": "plan"}),
    }
    first.coordinator.get_capability.side_effect = (
        lambda key: skills if key == "skills_discovery" else None
    )
    processor = CommandProcessor(first)

    assert processor.process_input("/p")[1]["args"] == "plan"
    assert processor.process_input("/plan")[1]["args"] == "plan"
    assert processor.process_input("/rvw")[1]["skill_name"] == "review"
    assert processor.process_input("/skill rvw")[1]["skill_name"] == "review"

    second = MagicMock()
    second.coordinator.session_state = {"active_mode": None}
    second.coordinator.get_capability.return_value = None
    isolated = CommandProcessor(second)
    assert isolated.process_input("/rvw")[0] == "unknown_command"