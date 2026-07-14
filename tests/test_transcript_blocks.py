from decimal import Decimal
from datetime import UTC, datetime
from io import StringIO

import pytest
from rich.console import Console

from amplifier_app_cli.ui.transcript_blocks import BlockedBlock
from amplifier_app_cli.ui.transcript_blocks import AnswerBlock
from amplifier_app_cli.ui.transcript_blocks import CodeExcerptBlock
from amplifier_app_cli.ui.transcript_blocks import DebugBlock
from amplifier_app_cli.ui.transcript_blocks import NarrationBlock
from amplifier_app_cli.ui.transcript_blocks import PlanBlock
from amplifier_app_cli.ui.transcript_blocks import PlanItem
from amplifier_app_cli.ui.transcript_blocks import PlanItemStatus
from amplifier_app_cli.ui.transcript_blocks import RecapBlock
from amplifier_app_cli.ui.transcript_blocks import StatusBlock
from amplifier_app_cli.ui.transcript_blocks import SessionHeaderBlock
from amplifier_app_cli.ui.transcript_blocks import Telemetry
from amplifier_app_cli.ui.transcript_blocks import ToolBlock
from amplifier_app_cli.ui.transcript_blocks import ToolStatus
from amplifier_app_cli.ui.transcript_blocks import TranscriptRenderer
from amplifier_app_cli.ui.transcript_blocks import TurnTerminatorBlock
from amplifier_app_cli.ui.transcript_blocks import UserBlock
from amplifier_app_cli.ui.transcript_blocks import telemetry_from_usage
from amplifier_app_cli.ui.transcript_blocks import tool_block_from_activity
from amplifier_app_cli.ui.runtime_values import BoundedText
from amplifier_app_cli.ui.runtime_values import ToolActivitySnapshot
from amplifier_app_cli.ui.runtime_values import ToolActivityStatus
from amplifier_app_cli.ui.runtime_values import UsageTotalsSnapshot


def _render(*blocks, width: int = 100) -> str:
    stream = StringIO()
    console = Console(file=stream, no_color=True, width=width, highlight=False)
    renderer = TranscriptRenderer(console)
    for block in blocks:
        renderer.render(block)
    return stream.getvalue()


def test_answer_markdown_preserves_command_placeholders() -> None:
    output = _render(
        AnswerBlock("Use `/model <provider> <model>` or `/permissions preset <name>`.")
    )

    assert "/model <provider> <model>" in output
    assert "/permissions preset <name>" in output


def test_telemetry_formats_compact_suffix() -> None:
    telemetry = Telemetry(
        elapsed_seconds=68,
        tokens=83_900,
        cached_percent=91,
        cost=Decimal("0.17"),
    )

    assert telemetry.suffix() == "(1m 08s · ↓ 83.9k tok, 91% cached · $0.17)"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"elapsed_seconds": -1},
        {"tokens": -1},
        {"cached_percent": 101},
        {"cost": "NaN"},
        {"cost": "-0.01"},
    ],
)
def test_telemetry_rejects_invalid_values(kwargs) -> None:
    with pytest.raises(ValueError):
        Telemetry(**kwargs)


def test_renderer_enforces_core_block_prefixes_and_plan_states() -> None:
    telemetry = Telemetry(elapsed_seconds=6.1, tokens=1_250, cost="0.04")
    output = _render(
        UserBlock("ship it", mode="build"),
        NarrationBlock("Checking the changed paths", telemetry),
        ToolBlock(
            "Running 1 shell command",
            ToolStatus.RUNNING,
            command="uv run pytest -q",
        ),
        PlanBlock(
            "Verify release",
            (
                PlanItem("Inspect diff", PlanItemStatus.COMPLETED),
                PlanItem("Run tests", PlanItemStatus.ACTIVE),
                PlanItem("Publish", PlanItemStatus.PENDING),
            ),
            telemetry,
        ),
        BlockedBlock("git push --force origin main", "outside authorization"),
        RecapBlock("verified release", "open the pull request"),
    )

    for expected in (
        "❯ [build]",
        "● Checking",
        "└ uv run",
        "✔ Inspect",
        "■ Run",
        "□ Publish",
        "⊘ git",
        "✳ Goal:",
    ):
        assert expected in output


def test_renderer_compacts_large_user_payload_without_losing_context_preview() -> None:
    payload = "Review this proposal: " + ("x" * 900) + " TAIL_SENTINEL"

    output = _render(UserBlock(payload, mode="chat"))

    assert f"[Pasted text · {len(payload):,} chars]" in output
    assert "Review this proposal:" in output
    assert "TAIL_SENTINEL" not in output
    assert payload not in output


def test_tool_and_debug_output_collapse_by_default() -> None:
    output = _render(
        ToolBlock(
            "Ran 1 shell command",
            ToolStatus.COMPLETED,
            output=("first", "second"),
        ),
        DebugBlock(("secret one", "secret two")),
    )

    assert output.count("2 lines · ctrl-o expand") == 1
    assert "  ● Ran 1 shell command\n" in output
    assert "secret one" not in output
    assert "first" not in output


def test_debug_always_show_policy_expands_collapsed_blocks() -> None:
    stream = StringIO()
    renderer = TranscriptRenderer(
        Console(file=stream, no_color=True),
        show_debug=lambda: True,
    )

    renderer.render(DebugBlock(("provider=openai",), expanded=False))

    assert "provider=openai" in stream.getvalue()
    assert "ctrl-o expand" not in stream.getvalue()


def test_debug_reports_total_and_omitted_lines() -> None:
    output = _render(
        DebugBlock(
            ("visible one", "visible two"),
            expanded=True,
            total_lines=5,
        )
    )

    assert "visible one" in output
    assert "3 additional lines omitted (5 total)" in output


def test_plan_render_profile_suppresses_tool_and_debug_detail() -> None:
    stream = StringIO()
    renderer = TranscriptRenderer(
        Console(file=stream, no_color=True), render_profile="plan"
    )

    renderer.render(ToolBlock("Ran shell", ToolStatus.COMPLETED))
    renderer.render(DebugBlock(("internal",), expanded=True))
    renderer.render(NarrationBlock("Planning the next step"))

    assert "Ran shell" not in stream.getvalue()
    assert "internal" not in stream.getvalue()
    assert "Planning the next step" in stream.getvalue()


def test_divergent_render_profile_keeps_answers_but_hides_plans() -> None:
    stream = StringIO()
    renderer = TranscriptRenderer(
        Console(file=stream, no_color=True), render_profile="divergent"
    )

    renderer.render(PlanBlock("Converged plan", ()))
    renderer.render(AnswerBlock("Several possible directions"))

    assert "Converged plan" not in stream.getvalue()
    assert "Several possible directions" in stream.getvalue()


def test_expanded_debug_and_tool_output_are_visible() -> None:
    output = _render(
        ToolBlock("Ran command", ToolStatus.COMPLETED, output=("ok",), expanded=True),
        DebugBlock(("detail",), expanded=True),
    )

    assert "ok" in output
    assert "detail" in output


def test_code_excerpt_has_line_numbers_and_changed_line_marker() -> None:
    output = _render(
        CodeExcerptBlock(
            "before = 1\nafter = 2",
            language="python",
            start_line=20,
            changed_lines=frozenset({21}),
        )
    )

    assert "20" in output
    assert "21" in output
    assert "after" in output


def test_status_and_turn_terminator_compress_telemetry() -> None:
    telemetry = Telemetry(elapsed_seconds=42, tokens=16_900, cost="0.11")
    output = _render(
        StatusBlock(telemetry, steering_hint="type to steer"),
        TurnTerminatorBlock(telemetry, "3 files · tests passed"),
    )

    assert (
        "✳ working · 42s · ↓ 16.9k tok · $0.11 · esc to interrupt · type to steer"
        in output
    )
    assert "3 files · tests passed" in output


def test_session_header_keeps_startup_identity_out_of_agent_narration() -> None:
    output = _render(
        SessionHeaderBlock(
            "Amplifier 2026.06.10 · core 1.3.0",
            "Bundle: foundation · Provider: OpenAI · gpt-5.5 · session c824b6",
        )
    )

    assert output.splitlines() == [
        "Amplifier 2026.06.10 · core 1.3.0",
        "Bundle: foundation · Provider: OpenAI · gpt-5.5 · session c824b6",
    ]
    assert "●" not in output


def test_blocks_strip_terminal_control_characters() -> None:
    output = _render(NarrationBlock("safe\x1b]0;owned\x07 text"))

    assert "\x1b" not in output
    assert "\x07" not in output
    assert "safe]0;owned text" in output


def test_runtime_snapshots_adapt_to_tool_and_telemetry_blocks() -> None:
    usage = UsageTotalsSnapshot(
        request_count=1,
        input_tokens=1_000,
        output_tokens=250,
        total_tokens=1_250,
        cache_read_tokens=800,
        cache_write_tokens=0,
        reasoning_tokens=0,
        cost_usd=Decimal("0.04"),
        cost_complete=True,
        duration_seconds=6.1,
    )
    now = datetime.now(UTC)
    activity = ToolActivitySnapshot(
        tool_call_id="call-1",
        session_id="root",
        tool_name="shell",
        status=ToolActivityStatus.SUCCEEDED,
        command="uv run pytest -q",
        summary="Ran 1 shell command",
        input=BoundedText("{}", 2, 1, False),
        result=BoundedText("1147 passed", 11, 1, False),
        parallel_group_id="",
        started_at=now,
        completed_at=now,
        duration_seconds=1.2,
    )

    telemetry = telemetry_from_usage(usage)
    block = tool_block_from_activity(activity)
    output = _render(block, TurnTerminatorBlock(telemetry))

    assert telemetry.suffix() == "(6.1s · ↓ 1.2k tok, 80% cached · $0.04)"
    assert "Ran 1 shell command" in output
    assert "ctrl-o expand" not in output
