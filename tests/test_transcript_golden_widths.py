"""Width-matrix goldens for every typed transcript block.

Two layers per (block, width) case:

1. Readable snapshot goldens — the exact rendered screen text, checked in at
   ``tests/goldens/transcript/<name>_<width>.txt``. A failure prints a unified
   diff of the screen; review it as a UI diff. Regenerate with
   ``uv run python tests/regen_goldens.py --write``.
2. ``GOLDEN_MARKERS`` — semantic must-contain assertions that survive regens.
"""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from prompt_toolkit.utils import get_cwidth
from rich.console import Console

from amplifier_app_cli.ui.transcript_blocks import AnswerBlock, BlockedBlock
from amplifier_app_cli.ui.transcript_blocks import CodeExcerptBlock, DebugBlock
from amplifier_app_cli.ui.transcript_blocks import DiffBlock
from amplifier_app_cli.ui.transcript_blocks import NarrationBlock, PlanBlock
from amplifier_app_cli.ui.transcript_blocks import PlanItem, PlanItemStatus
from amplifier_app_cli.ui.transcript_blocks import RecapBlock, StatusBlock
from amplifier_app_cli.ui.transcript_blocks import Telemetry, ToolBlock, ToolStatus
from amplifier_app_cli.ui.transcript_blocks import TranscriptRenderer
from amplifier_app_cli.ui.transcript_blocks import TurnTerminatorBlock, UserBlock

from helpers import assert_matches_golden

GOLDEN_DIR = Path(__file__).resolve().parent / "goldens" / "transcript"
GOLDEN_WIDTHS = (40, 80, 120)
GALLERY_WIDTHS = (40, 80, 97, 120)  # 97 = deliberately awkward width


MARKDOWN_GOLDEN = """# Result

**Bold** and *italic* with [docs](https://example.test/docs).

> Quoted evidence

- first item
- second item

| Check | State |
| --- | --- |
| tests | pass |

```python
value = "a deliberately long value that must wrap safely"
```
"""


def _blocks():
    telemetry = Telemetry(68, 83_900, 91, Decimal("0.17"))
    return {
        "user": UserBlock("Please verify the persistence boundary", mode="build"),
        "answer": AnswerBlock(MARKDOWN_GOLDEN, label="Amplifier"),
        "narration": NarrationBlock("Checking the durable session store", telemetry),
        "tool_done": ToolBlock(
            "Ran 1 shell command",
            ToolStatus.COMPLETED,
            output=("1214 passed", "build succeeded"),
        ),
        "tool_running": ToolBlock(
            "Running test suite",
            ToolStatus.RUNNING,
            command="uv run pytest tests/test_session_store.py --maxfail=1",
        ),
        "tool_failed": ToolBlock(
            "Test suite failed",
            ToolStatus.FAILED,
            output=("1 failed",),
        ),
        "tool_elided": ToolBlock(
            "Ran 1 shell command",
            ToolStatus.COMPLETED,
            output=tuple(
                f"session store check {index:02d} ok" for index in range(1, 21)
            ),
            expanded=True,
        ),
        "diff": DiffBlock(
            path="amplifier_app_cli/session_store.py",
            diff_text=(
                "@@ -41,4 +41,5 @@\n"
                " def save_session(value):\n"
                "-    return store.write(value)\n"
                "+    payload = sanitize(value)\n"
                "+    return durable_store.write(payload)\n"
                " \n"
                "\\ No newline at end of file"
            ),
            added=2,
            removed=1,
            move_path="amplifier_app_cli/durable_session_store.py",
        ),
        "blocked": BlockedBlock(
            "git push --force origin main",
            "outside user authorization · finding safer path",
        ),
        "code": CodeExcerptBlock(
            "def save_session(value):\n    return durable_store.write(value)",
            "python",
            start_line=41,
            changed_lines=frozenset({42}),
        ),
        "plan": PlanBlock(
            "Refactor session store",
            (
                PlanItem("Audit persistence paths", PlanItemStatus.COMPLETED),
                PlanItem("Migrate durable history", PlanItemStatus.ACTIVE),
                PlanItem("Add reconciliation", PlanItemStatus.PENDING),
            ),
            telemetry,
        ),
        "status": StatusBlock(telemetry, steering_hint="type to steer"),
        "recap": RecapBlock(
            "durable chat history",
            "resume migration from the active checkpoint",
        ),
        "debug": DebugBlock(("provider=openai", "request=abc"), expanded=False),
        "terminator": TurnTerminatorBlock(
            telemetry,
            "3 files · +142/-38 · tests ✔",
        ),
    }


GOLDEN_MARKERS = {
    "user": ("❯", "[build]", "persistence boundary"),
    "answer": ("Amplifier:", "Result", "Bold", "Quoted evidence", "tests", "value"),
    "narration": ("●", "durable session store", "$0.17"),
    "tool_done": ("●", "Ran 1 shell command"),
    "tool_running": ("●", "Running test suite", "uv run pytest"),
    "tool_failed": ("●", "Test suite failed", "ctrl-o expand"),
    "tool_elided": (
        "●",
        "check 01",
        "check 08",
        "… +8 lines",
        "ctrl-o again",
        "check 17",
        "check 20",
    ),
    "diff": (
        "session_store.py",
        "→",
        "+2",
        "−1",
        "@@ -41,4 +41,5 @@",
        "durable_store",
    ),
    "blocked": ("⊘", "git push --force", "finding safer path"),
    "code": ("41", "save_session", "durable_store"),
    "plan": ("·", "Refactor session store", "✔", "■", "□"),
    "status": ("✳", "working", "esc to interrupt", "type to steer"),
    "recap": ("✳", "Goal:", "Next:"),
    "debug": ("2 lines", "ctrl-o expand"),
    "terminator": ("$0.17", "3 files", "tests ✔"),
}


def _console(output: StringIO, width: int) -> Console:
    return Console(
        file=output,
        width=width,
        color_system=None,
        force_terminal=False,
        legacy_windows=False,
    )


def render_block(name: str, width: int) -> str:
    """Render one typed block exactly like the golden test does."""
    output = StringIO()
    TranscriptRenderer(_console(output, width)).render(_blocks()[name])
    return output.getvalue()


def render_gallery(width: int) -> str:
    """Render every typed block in sequence into one transcript screen."""
    output = StringIO()
    renderer = TranscriptRenderer(_console(output, width))
    blocks = _blocks()
    for name in GOLDEN_MARKERS:
        renderer.render(blocks[name])
    return output.getvalue()


@pytest.mark.parametrize("width", GOLDEN_WIDTHS)
@pytest.mark.parametrize("name", tuple(GOLDEN_MARKERS))
def test_typed_block_golden_semantics_fit_width(name: str, width: int) -> None:
    rendered = render_block(name, width)

    normalized = " ".join(rendered.split())
    for marker in GOLDEN_MARKERS[name]:
        assert marker in normalized
    assert all(get_cwidth(line) <= width for line in rendered.splitlines())
    assert_matches_golden(rendered, GOLDEN_DIR / f"{name}_{width}.txt")


@pytest.mark.parametrize("width", GALLERY_WIDTHS)
def test_gallery_renders_all_blocks_in_sequence(width: int) -> None:
    rendered = render_gallery(width)

    assert all(get_cwidth(line) <= width for line in rendered.splitlines())
    assert_matches_golden(rendered, GOLDEN_DIR / f"gallery_{width}.txt")


@pytest.mark.parametrize("width", [40, 80, 120])
def test_markdown_constructs_render_as_content_not_raw_markup(width: int) -> None:
    output = StringIO()
    console = Console(file=output, width=width, color_system=None)

    TranscriptRenderer(console).render(AnswerBlock(MARKDOWN_GOLDEN))

    rendered = output.getvalue()
    assert "# Result" not in rendered
    assert "**Bold**" not in rendered
    assert "```python" not in rendered
    assert all(get_cwidth(line) <= width for line in rendered.splitlines())
