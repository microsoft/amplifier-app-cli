"""Exact-width goldens for the persistent two-zone footer."""

import pytest
from prompt_toolkit.utils import get_cwidth

from amplifier_app_cli.ui.footer import format_bottom_toolbar_text


_COMMON = {
    "bundle_name": "foundation",
    "session_id": "32595fdc",
    "active_mode": "chat",
    "tasks_available": True,
    "session_cost": "0.80",
    "trust_summary": ("auto read,test · ask net,outside-project,spend,subagent,write"),
    "last_yield": "▲",
}


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (
            80,
            "chat · a:r,t ?:w,n,+3 · foundation · 3259 · $0.80▲"
            "        / · shift-tab · ctrl-t",
        ),
        (
            120,
            "chat · auto read,test · ask write,net,spend,+2 · foundation · 3259 · $0.80 ▲"
            "  / commands · shift-tab mode · ctrl-t tasks",
        ),
        (
            198,
            "chat · auto read,test · ask write,net,spend,+2 · foundation · 3259 · $0.80 ▲"
            + " " * 80
            + "/ commands · shift-tab mode · ctrl-t tasks",
        ),
    ],
)
def test_idle_footer_golden(width: int, expected: str) -> None:
    rendered = format_bottom_toolbar_text(**_COMMON, max_width=width)

    assert rendered == expected
    assert get_cwidth(rendered) == width
    assert "..." not in rendered


@pytest.mark.parametrize("width", [80, 120, 198])
def test_running_footer_stays_one_line_and_prioritizes_interrupt(width: int) -> None:
    rendered = format_bottom_toolbar_text(
        **_COMMON,
        is_running=True,
        max_width=width,
    )

    assert get_cwidth(rendered) == width
    assert "\n" not in rendered
    assert "esc" in rendered
    assert "tab complete" not in rendered


def test_classifier_mode_displays_its_effective_permission_posture() -> None:
    rendered = format_bottom_toolbar_text(
        bundle_name="foundation",
        session_id="32595fdc",
        active_mode="auto",
        tasks_available=True,
        session_cost="0.80",
        trust_summary="classifier-gated",
        max_width=120,
    )

    assert rendered.startswith("auto · auto read,write · check test,net,spend,+2")
    assert "classifier-gated" not in rendered


def test_needs_you_replaces_hints_before_losing_required_state() -> None:
    rendered = format_bottom_toolbar_text(
        **_COMMON,
        needs_attention_count=2,
        max_width=80,
    )

    assert "needs-you 2" in rendered
    assert "foundation · 3259 · $0.80▲" in rendered
    assert get_cwidth(rendered) <= 80


def test_footer_records_mode_before_the_effective_permission_dial() -> None:
    rendered = format_bottom_toolbar_text(
        **{**_COMMON, "active_mode": "brainstorm"},
        max_width=120,
    )

    assert rendered.startswith("brainstorm · auto r,t · ask w,n,$,+2")
    assert "brainstorm mode on" not in rendered
    assert "shift-tab" in rendered


def test_permission_posture_is_independent_of_conversation_mode() -> None:
    rendered = format_bottom_toolbar_text(
        **{**_COMMON, "active_mode": "brainstorm"},
        permission_mode="bypass",
        max_width=120,
    )

    assert rendered.startswith("brainstorm · bypass permissions on")
    assert "brainstorm mode on" not in rendered


def test_approval_replaces_generic_hints_with_decision_controls() -> None:
    rendered = format_bottom_toolbar_text(
        **_COMMON,
        is_running=True,
        approval_pending=True,
        max_width=120,
    )

    assert rendered.endswith("arrows select · enter confirm · esc deny")
    assert "esc interrupt" not in rendered
