"""Width-matrix golden semantics for every typed transcript block."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from io import StringIO

import pytest
from prompt_toolkit.utils import get_cwidth
from rich.console import Console

from amplifier_app_cli.ui.transcript_blocks import AnswerBlock, BlockedBlock
from amplifier_app_cli.ui.transcript_blocks import CodeExcerptBlock, DebugBlock
from amplifier_app_cli.ui.transcript_blocks import NarrationBlock, PlanBlock
from amplifier_app_cli.ui.transcript_blocks import PlanItem, PlanItemStatus
from amplifier_app_cli.ui.transcript_blocks import RecapBlock, StatusBlock
from amplifier_app_cli.ui.transcript_blocks import Telemetry, ToolBlock, ToolStatus
from amplifier_app_cli.ui.transcript_blocks import TranscriptRenderer
from amplifier_app_cli.ui.transcript_blocks import TurnTerminatorBlock, UserBlock


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
    "blocked": ("⊘", "git push --force", "finding safer path"),
    "code": ("41", "save_session", "durable_store"),
    "plan": ("·", "Refactor session store", "✔", "■", "□"),
    "status": ("✳", "working", "esc to interrupt", "type to steer"),
    "recap": ("✳", "Goal:", "Next:"),
    "debug": ("2 lines", "ctrl-o expand"),
    "terminator": ("$0.17", "3 files", "tests ✔"),
}


EXACT_GOLDEN_SHA256 = {
    (40, "user"): "5e60b049b9d0e753bfce401e083c1fa9e5b27065d31cd5b2cc43a7adc61e24ce",
    (40, "answer"): "0a56f0138e2e756cd5b1d6d7a09d517d9a9a85063150d87fa30ee768f6d1a946",
    (
        40,
        "narration",
    ): "37f712475d2fbfdf01b2f7cec9fb30a8b8e28e4bcee8672c2b46194d7f8d182f",
    (
        40,
        "tool_done",
    ): "b1afbdcb6a702b505f5b7f7505e0b8467b2024b1ba186e06a0631d86df3fdbce",
    (
        40,
        "tool_running",
    ): "e763768dde7b48163139bbafb43b3546a20b8cb15e7e3c20d0c2e54b03f9064c",
    (
        40,
        "tool_failed",
    ): "d246f36091ec1710d647f19f0b000884ee8a7afb6f2db1ed5047fe0f912dcd18",
    (40, "blocked"): "f0e1ee6aa0e576ac275b72facb1bb7bfba85729271a9fd36a58d49ed0f556f5e",
    (40, "code"): "1e79719008d29558ab392d83960df48bfe008903e9c3f8b81f888ec5367e005c",
    (40, "plan"): "bee9de6e838680d9bb429f9177a55df42e07f81c310d2087f17b438b4de52b78",
    (40, "status"): "93544f73317709ce482adb7844ff5b90107bf197041eb3fa759b100bf4244b70",
    (40, "recap"): "6c19d8d019dba97a93fe6a0ba632cc854f2e5a167b25bbb88765b0b087608941",
    (40, "debug"): "cc4bf0e20a26075c7be5d89041c8d46bd77f2af31091a0696e411956911d0f68",
    (
        40,
        "terminator",
    ): "3555368786aaa0466695ddc97de4c0d54f61476fea42b02c87c930f551f59e56",
    (80, "user"): "bb173e42c68b8ca7fb083d9ed6fd47fdef4837933edd25d5703428454bd1cc23",
    (80, "answer"): "d49a7d8a71399cbd876b8cce9ec9c6e345bb81cae397a78febc70dfa936e130e",
    (
        80,
        "narration",
    ): "2f764fa278ae350bad7ba0b73fd2554fdcf0081ef6b857077a6ff0d702e6f631",
    (
        80,
        "tool_done",
    ): "b1afbdcb6a702b505f5b7f7505e0b8467b2024b1ba186e06a0631d86df3fdbce",
    (
        80,
        "tool_running",
    ): "b6301ba6a952742079c40542641e5fef6c54a334ec565b70f6df17d107bcaf86",
    (
        80,
        "tool_failed",
    ): "d246f36091ec1710d647f19f0b000884ee8a7afb6f2db1ed5047fe0f912dcd18",
    (80, "blocked"): "13a3c9a03c1a14d2b94946b9e193ee592d41dd4cc5329b276434dad30ea96c67",
    (80, "code"): "36f2edcbfc5524ec30299b5db9748408131d2113aa38bf951a728101ac31aa56",
    (80, "plan"): "7a5edf963c2481f549672ffda2ea5040abb6afbe3e16ac700247232e991c7dc7",
    (80, "status"): "4b05dcfc07027a6509c45c3cf4cce645b617ff62825a3b04cf24a02b957fc532",
    (80, "recap"): "21b9cde69744cfac11e53b3258a0be5b440ac068ce4c198eafe9989d1a3df91c",
    (80, "debug"): "cc4bf0e20a26075c7be5d89041c8d46bd77f2af31091a0696e411956911d0f68",
    (
        80,
        "terminator",
    ): "33b4098e2a70150a4532d42d472e91accaad1e345aa33dcdc5a5b6b02b2eae09",
    (120, "user"): "bb173e42c68b8ca7fb083d9ed6fd47fdef4837933edd25d5703428454bd1cc23",
    (120, "answer"): "b06a506d2eb297d35bd5cbf5da4e785d4fe55bf61621be964f90591e5a12dcd8",
    (
        120,
        "narration",
    ): "2f764fa278ae350bad7ba0b73fd2554fdcf0081ef6b857077a6ff0d702e6f631",
    (
        120,
        "tool_done",
    ): "b1afbdcb6a702b505f5b7f7505e0b8467b2024b1ba186e06a0631d86df3fdbce",
    (
        120,
        "tool_running",
    ): "b6301ba6a952742079c40542641e5fef6c54a334ec565b70f6df17d107bcaf86",
    (
        120,
        "tool_failed",
    ): "d246f36091ec1710d647f19f0b000884ee8a7afb6f2db1ed5047fe0f912dcd18",
    (
        120,
        "blocked",
    ): "a7e6a4fa1628fb6666f37ba9ff9e862cd53384964e3e4b61c5126fd8deb01570",
    (120, "code"): "36f2edcbfc5524ec30299b5db9748408131d2113aa38bf951a728101ac31aa56",
    (120, "plan"): "7a5edf963c2481f549672ffda2ea5040abb6afbe3e16ac700247232e991c7dc7",
    (120, "status"): "29205e6db3cb7ce15c606aba0106a80fdb6df1e6f6ff657797d9aab6cb858310",
    (120, "recap"): "21b9cde69744cfac11e53b3258a0be5b440ac068ce4c198eafe9989d1a3df91c",
    (120, "debug"): "cc4bf0e20a26075c7be5d89041c8d46bd77f2af31091a0696e411956911d0f68",
    (
        120,
        "terminator",
    ): "0327be8c29739375caa1f93ca3b33e5841ac4e3ca35f3cdab76174da4bdef057",
}


@pytest.mark.parametrize("width", [40, 80, 120])
@pytest.mark.parametrize("name", tuple(GOLDEN_MARKERS))
def test_typed_block_golden_semantics_fit_width(name: str, width: int) -> None:
    output = StringIO()
    console = Console(
        file=output,
        width=width,
        color_system=None,
        force_terminal=False,
        legacy_windows=False,
    )

    TranscriptRenderer(console).render(_blocks()[name])

    rendered = output.getvalue()
    normalized = " ".join(rendered.split())
    for marker in GOLDEN_MARKERS[name]:
        assert marker in normalized
    assert all(get_cwidth(line) <= width for line in rendered.splitlines())
    assert sha256(rendered.encode()).hexdigest() == EXACT_GOLDEN_SHA256[(width, name)]


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
