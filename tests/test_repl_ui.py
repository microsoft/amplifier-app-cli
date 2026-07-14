"""Tests for the interactive REPL UI helpers."""

from prompt_toolkit.document import Document
from prompt_toolkit.utils import get_cwidth
import pytest

from amplifier_app_cli.ui.repl import SlashCommandCompleter
from amplifier_app_cli.ui.repl import build_terminal_title
from amplifier_app_cli.ui.repl import format_activity_result
from amplifier_app_cli.ui.repl import format_activity_start
from amplifier_app_cli.ui.repl import format_bottom_toolbar_text
from amplifier_app_cli.ui.repl import format_prompt_text
from amplifier_app_cli.ui.repl import format_task_pane_text
from amplifier_app_cli.ui.repl import summarize_text
from amplifier_app_cli.ui.repl import supports_layered_ui
from amplifier_app_cli.ui.repl import terminal_title_sequence
from amplifier_app_cli.ui.repl import terminal_notification_sequence
from amplifier_app_cli.ui.repl import terminal_tab_color_sequence
from amplifier_app_cli.ui.task_status import TaskStatusTracker


def _completion_texts(completer: SlashCommandCompleter, text: str) -> list[str]:
    return [item.text for item in completer.get_completions(Document(text), None)]


class _TerminalStream:
    def __init__(self, is_terminal: bool):
        self.is_terminal = is_terminal

    def isatty(self) -> bool:
        return self.is_terminal


def test_layered_ui_requires_interactive_input_and_output():
    terminal = _TerminalStream(True)
    redirected = _TerminalStream(False)

    assert supports_layered_ui(terminal, terminal) is True
    assert supports_layered_ui(redirected, terminal) is False
    assert supports_layered_ui(terminal, redirected) is False


def test_layered_ui_rejects_streams_without_tty_support():
    assert supports_layered_ui(object(), _TerminalStream(True)) is False


def test_slash_command_completer_suggests_base_commands():
    completer = SlashCommandCompleter(
        {
            "/help": {"description": "Show available commands"},
            "/status": {"description": "Show session status"},
        }
    )

    assert "/help" in _completion_texts(completer, "/he")


def test_slash_command_completer_uses_palette_source_metadata():
    completer = SlashCommandCompleter(
        {"/help": {"description": "Show available commands"}}
    )

    completion = next(completer.get_completions(Document("/he"), None))

    assert "built-in" in str(completion.display_meta)
    assert "Show available commands" in str(completion.display_meta)


def test_slash_command_completer_suggests_mode_shortcuts():
    completer = SlashCommandCompleter(
        {"/help": {"description": "Show available commands"}},
        mode_shortcuts={"plan": "plan"},
    )

    assert "/plan" in _completion_texts(completer, "/pla")


def test_slash_command_completer_suggests_modes_after_mode_command():
    completer = SlashCommandCompleter(
        {"/mode": {"description": "Set mode"}},
        mode_names=["plan", "brainstorm"],
    )

    assert "plan" in _completion_texts(completer, "/mode pl")


@pytest.mark.parametrize("command", ["/effort x", "/strength x"])
def test_slash_command_completer_suggests_reasoning_strength(command):
    completer = SlashCommandCompleter(
        {
            "/effort": {"description": "Set effort"},
            "/strength": {"description": "Set strength"},
        }
    )

    assert "xhigh" in _completion_texts(completer, command)


def test_slash_command_completer_uses_lazily_discovered_models():
    models = ["gpt-5.5"]
    completer = SlashCommandCompleter(
        {"/model": {"description": "Set model"}},
        model_names=lambda: tuple(models),
    )

    assert _completion_texts(completer, "/model gpt-") == ["gpt-5.5"]
    models.append("gpt-5.6")
    assert "gpt-5.6" in _completion_texts(completer, "/model gpt-")


def test_slash_command_completer_suggests_skills_after_skill_command():
    completer = SlashCommandCompleter(
        {"/skill": {"description": "Load skill"}},
        skill_names=["simplify", "debug"],
    )

    assert "simplify" in _completion_texts(completer, "/skill sim")


def test_slash_command_completer_suggests_config_subcommands():
    completer = SlashCommandCompleter({"/config": {"description": "Show config"}})

    assert "tools" in _completion_texts(completer, "/config to")


def test_prompt_text_includes_active_mode():
    prompt = str(format_prompt_text("plan"))

    assert "amplifier" in prompt
    assert "plan" in prompt


def test_bottom_toolbar_includes_live_session_context():
    toolbar = format_bottom_toolbar_text(
        bundle_name="bundle:dev",
        session_id="12345678-abcdef",
        active_mode="plan",
    )

    assert toolbar.startswith("plan mode on · dev · 1234 · $0.00")
    assert "bundle " not in toolbar
    assert "session " not in toolbar
    assert "/ commands" in toolbar
    assert "shift-tab mode" in toolbar


def test_bottom_toolbar_switches_to_running_hints():
    toolbar = format_bottom_toolbar_text(
        bundle_name="bundle:dev",
        session_id="12345678-abcdef",
        active_mode="plan",
        is_running=True,
    )

    assert "esc interrupt" in toolbar
    assert "enter queues" not in toolbar
    assert "ctrl-c interrupt" not in toolbar
    assert "ctrl-d exit" not in toolbar


def test_bottom_toolbar_leaves_activity_in_the_dedicated_working_row():
    toolbar = format_bottom_toolbar_text(
        bundle_name="bundle:dev",
        session_id="12345678-abcdef",
        active_mode="plan",
        is_running=True,
        activity_label="⠋ working",
    )

    assert "⠋ working" not in toolbar
    assert "esc interrupt" in toolbar


def test_bottom_toolbar_shows_queued_count_while_running():
    toolbar = format_bottom_toolbar_text(
        bundle_name="bundle:dev",
        session_id="12345678-abcdef",
        active_mode="plan",
        is_running=True,
        queued_count=2,
    )

    assert "queued 2" in toolbar
    assert "esc interrupt" in toolbar


def test_bottom_toolbar_advertises_tasks_without_transient_counts():
    toolbar = format_bottom_toolbar_text(
        bundle_name="bundle:dev",
        session_id="12345678-abcdef",
        active_mode="plan",
        tasks_available=True,
        image_paste_available=True,
        task_summary="todo 1/3 | agents 2 running/1 done",
    )

    assert "ctrl-t tasks" in toolbar
    assert "ctrl-v paste image" not in toolbar
    assert "todo 1/3" not in toolbar
    assert "agents 2 running/1 done" not in toolbar


def test_bottom_toolbar_keeps_primary_shortcuts_visible_at_narrow_width():
    toolbar = format_bottom_toolbar_text(
        bundle_name="bundle:" + "very-long-bundle-name" * 3,
        session_id="12345678-abcdef",
        active_mode="plan",
        tasks_available=True,
        image_paste_available=True,
        task_summary="todo 1/3 | agents 2 running/1 done",
        max_width=60,
    )

    assert get_cwidth(toolbar) <= 60
    assert toolbar.startswith("plan")
    assert "shift-tab" in toolbar
    assert "ctrl-t" in toolbar


def test_bottom_toolbar_has_state_and_hint_zones_with_cost_and_trust():
    toolbar = format_bottom_toolbar_text(
        bundle_name="bundle:foundation",
        session_id="017954f1-long",
        active_mode="build",
        tasks_available=True,
        session_cost="0.57",
        trust_summary="auto read,test · ask write,net,spend",
        last_yield="▲",
        max_width=140,
    )

    assert toolbar.startswith("build · auto read,test · ask write,net,spend")
    assert " · foundation · 0179 · " in toolbar
    assert "$0.57" in toolbar
    assert "▲" in toolbar
    assert toolbar.endswith("/ commands · shift-tab mode · ctrl-t tasks")


def test_bottom_toolbar_never_exposes_more_than_three_hints():
    toolbar = format_bottom_toolbar_text(
        bundle_name="foundation",
        session_id="017954f1",
        active_mode="chat",
        tasks_available=True,
        image_paste_available=True,
    )

    hint_zone = toolbar.split("  ", maxsplit=1)[1]
    assert hint_zone.split(" · ") == [
        "/ commands",
        "shift-tab mode",
        "ctrl-t tasks",
    ]


def test_bottom_toolbar_preserves_cost_and_risk_at_narrow_width():
    toolbar = format_bottom_toolbar_text(
        bundle_name="foundation",
        session_id="017954f1",
        active_mode="auto",
        session_cost="12.34",
        trust_summary="classifier-gated",
        max_width=42,
    )

    assert toolbar.startswith("auto")
    assert "$12.34" in toolbar
    assert get_cwidth(toolbar) <= 42


@pytest.mark.parametrize("width", [60, 42, 30])
def test_bottom_toolbar_compacts_full_trust_dial_before_spend(width):
    toolbar = format_bottom_toolbar_text(
        bundle_name="foundation",
        session_id="017954f1",
        active_mode="auto",
        session_cost="12.34",
        trust_summary=("auto read,test · ask net,outside-project,spend,subagent,write"),
        tasks_available=True,
        max_width=width,
    )

    assert "$12.34" in toolbar
    assert toolbar.startswith("auto")
    assert get_cwidth(toolbar) <= width


def test_task_pane_prioritizes_new_running_agents_over_old_history():
    tracker = TaskStatusTracker("root")
    for index in range(9):
        child_id = f"completed-{index}"
        tracker.consume(
            "delegate:agent_spawned",
            {"agent": f"worker-{index}", "sub_session_id": child_id},
        )
        tracker.consume(
            "delegate:agent_completed",
            {"agent": f"worker-{index}", "sub_session_id": child_id},
        )
    tracker.consume(
        "delegate:agent_spawned",
        {"agent": "active-reviewer", "sub_session_id": "active-child"},
    )

    rendered = "".join(
        text
        for _, text in format_task_pane_text(
            tracker=tracker,
            session_id="root",
            is_running=True,
            max_lines=16,
        )
    )

    assert "active-reviewer" in rendered
    assert "[running]" in rendered
    assert len(rendered.splitlines()) <= 16


def test_task_pane_respects_small_terminal_line_budget():
    rendered = "".join(
        text
        for _, text in format_task_pane_text(
            tracker=TaskStatusTracker("root"),
            session_id="root",
            is_running=False,
            max_lines=4,
        )
    )

    assert len(rendered.splitlines()) <= 4


def test_task_pane_keeps_deepest_running_child_visible():
    tracker = TaskStatusTracker("root")
    parent_id = "root"
    for index in range(10):
        child_id = f"level-{index}"
        tracker.consume(
            "delegate:agent_spawned",
            {
                "agent": f"nested-{index}",
                "sub_session_id": child_id,
                "parent_session_id": parent_id,
            },
        )
        parent_id = child_id

    rendered = "".join(
        text
        for _, text in format_task_pane_text(
            tracker=tracker,
            session_id="root",
            is_running=True,
            max_lines=12,
        )
    )

    assert "nested-9" in rendered
    assert "more agents" in rendered


def test_task_pane_rows_fit_narrow_terminal_width():
    tracker = TaskStatusTracker("root")
    tracker.set_todos(
        [
            {
                "content": "Inspect " + "界" * 50,
                "activeForm": "Inspecting " + "界" * 50,
                "status": "in_progress",
            }
        ]
    )
    tracker.consume(
        "delegate:agent_spawned",
        {
            "agent": "reviewer-with-a-very-long-name",
            "sub_session_id": "narrow-child",
            "task": "Review " + "wide output " * 20,
        },
    )

    rendered = "".join(
        text
        for _, text in format_task_pane_text(
            tracker=tracker,
            session_id="root-session",
            is_running=True,
            max_lines=10,
            max_columns=40,
        )
    )

    assert all(get_cwidth(line) <= 40 for line in rendered.splitlines())


def test_task_pane_handles_parent_cycles_without_exposing_summaries():
    tracker = TaskStatusTracker("root")
    for index in range(10):
        tracker.consume(
            "session:fork",
            {
                "child_session_id": f"child-{index}",
                "parent_session_id": "root",
                "agent_name": f"worker-{index}",
                "summary": "secret prompt text",
            },
        )
    nodes = tracker.nodes()
    nodes[-1].parent_id = nodes[-2].session_id
    nodes[-2].parent_id = nodes[-1].session_id

    rendered = "".join(
        text
        for _, text in format_task_pane_text(
            tracker=tracker,
            session_id="root",
            is_running=True,
            max_lines=12,
        )
    )

    assert "secret prompt text" not in rendered


def test_summarize_text_collapses_and_truncates_long_input():
    summary = summarize_text("line 1\nline 2\t" + "x" * 100, max_chars=24)

    assert "\n" not in summary
    assert "\t" not in summary
    assert len(summary) <= 24
    assert summary.endswith("...")


def test_activity_lines_are_prompt_specific_and_elapsed():
    start = format_activity_start("fix the routing display")
    done = format_activity_result("done", 65)

    assert "Working:" in start
    assert "fix the routing display" in start
    assert "Done in 1m 05s" in done


def test_build_terminal_title_includes_context():
    title = build_terminal_title(
        cwd="/tmp/amplifier-app-cli",
        bundle_name="bundle:dev",
        session_id="12345678-abcdef",
        active_mode="plan",
        task_summary="make the terminal UI better",
        is_running=True,
        agent_count=2,
        needs_count=1,
    )

    assert "amplifier-app-cli" in title
    assert "Amplifier" in title
    assert "working" in title
    assert "✳" in title
    assert "make the terminal UI better" in title
    assert "mode plan" in title
    assert "agents 2" in title
    assert "needs 1" in title
    assert "dev" in title
    assert "12345678" in title


def test_terminal_title_sequence_strips_control_characters():
    sequence = terminal_title_sequence("safe\x1b]0;bad\a\x9dhidden\x9c title")

    assert sequence.startswith("\033]0;")
    assert sequence.endswith("\a")
    payload = sequence[len("\033]0;") : -1]
    assert "\x1b" not in payload
    assert "\a" not in payload
    assert "\x9d" not in payload
    assert "\x9c" not in payload
    assert "safe" in payload
    assert "bad" in payload


def test_ambient_terminal_sequences_are_zero_width_and_sanitized():
    assert "brightness;224" in terminal_tab_color_sequence("running")
    assert "brightness;224" in terminal_tab_color_sequence("needs-you")
    assert "default" in terminal_tab_color_sequence("idle")

    notification = terminal_notification_sequence(
        "Amplifier\x1b]0;bad", "tests pass\a\nnext"
    )
    assert notification.startswith("\x1b]777;notify;")
    assert notification.endswith("\a")
    assert "\n" not in notification
