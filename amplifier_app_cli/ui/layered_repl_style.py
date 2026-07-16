"""Theme tokens and color roles for the layered terminal application.

Single source for the TUI v3 palette (docs/designs/tui-v3-cohesive.md, section 1).
``slate`` is the default theme; ``graphite`` (warm) and ``carbon`` (cool, high
contrast) are alternates behind the same token names. There is no runtime
theme-selection mechanism yet — switch by pointing ``TOKENS`` at another entry
in ``THEMES``.
"""

from prompt_toolkit.styles import Style


SLATE_TOKENS: dict[str, str] = {
    "bg_term": "#232937",
    "bg_chrome": "#191d27",
    "bg_tab": "#2b3243",
    "fg": "#c9d1e0",
    "bright": "#eef2f8",
    "dim": "#6b7487",
    "dimmer": "#4a5163",
    "green": "#7ec699",
    "orange": "#e0a458",
    "red": "#e06c75",
    "blue": "#7aa2f7",
    "teal": "#6fc3c3",
    "rule": "#333b4d",
}

GRAPHITE_TOKENS: dict[str, str] = {
    "bg_term": "#211e1a",
    "bg_chrome": "#181512",
    "bg_tab": "#2c2722",
    "fg": "#d6cfc4",
    "bright": "#f2ede4",
    "dim": "#8a8175",
    "dimmer": "#575047",
    "green": "#98c28b",
    "orange": "#dba15c",
    "red": "#d97371",
    "blue": "#90a4d8",
    "teal": "#80bcae",
    "rule": "#3a352e",
}

CARBON_TOKENS: dict[str, str] = {
    "bg_term": "#14171d",
    "bg_chrome": "#0f1116",
    "bg_tab": "#1f242e",
    "fg": "#cdd6e4",
    "bright": "#f4f7fc",
    "dim": "#65718a",
    "dimmer": "#3d4657",
    "green": "#6fd39c",
    "orange": "#e9b14f",
    "red": "#ef6e7b",
    "blue": "#6f9df2",
    "teal": "#57c8c8",
    "rule": "#2a3140",
}

THEMES: dict[str, dict[str, str]] = {
    "slate": SLATE_TOKENS,
    "graphite": GRAPHITE_TOKENS,
    "carbon": CARBON_TOKENS,
}

TOKENS: dict[str, str] = THEMES["slate"]


def style_from_tokens(tokens: dict[str, str]) -> Style:
    """Map the section 1 tokens onto the layered REPL's style classes."""
    t = tokens
    return Style.from_dict(
        {
            "transcript": f"bg:{t['bg_term']} fg:{t['fg']}",
            "rule": f"fg:{t['rule']}",
            "output": f"fg:{t['fg']}",
            "output.muted": f"fg:{t['dim']} italic",
            "selected": f"bg:{t['bg_tab']} fg:{t['bright']}",
            "stream.label": f"fg:{t['teal']} bold",
            "stream.thinking": f"fg:{t['dim']} italic",
            "stream.text": f"fg:{t['fg']}",
            "status": f"bg:{t['bg_chrome']} fg:{t['dim']}",
            "status.risk": f"fg:{t['red']} bold",
            "plan": f"fg:{t['fg']}",
            "plan.header": f"fg:{t['orange']}",
            "plan.done": f"fg:{t['green']}",
            "plan.active": f"fg:{t['bright']} bold",
            "plan.pending": f"fg:{t['dim']}",
            "steering": f"fg:{t['teal']}",
            "steering.hint": f"fg:{t['dimmer']}",
            "tools": f"fg:{t['dim']}",
            "working": f"fg:{t['dim']}",
            "working.glyph": f"fg:{t['orange']}",
            "working.title": f"fg:{t['dim']}",
            "working.tree": f"fg:{t['dimmer']}",
            "working.agent": f"fg:{t['dim']}",
            "notice": f"fg:{t['dim']}",
            "palette": f"fg:{t['dim']}",
            "palette.selected": f"bg:{t['bg_tab']} fg:{t['fg']}",
            "palette.phase": f"fg:{t['dimmer']}",
            "palette.command": f"fg:{t['teal']} bold",
            "palette.source": f"fg:{t['dimmer']}",
            "rewind": f"fg:{t['orange']}",
            "queued": f"fg:{t['orange']}",
            "evidence": f"fg:{t['teal']}",
            "approval": f"bg:{t['bg_chrome']} fg:{t['fg']}",
            "approval.focus": f"bg:{t['bg_chrome']} fg:{t['orange']} bold",
            "approval.option": f"bg:{t['bg_chrome']} fg:{t['dim']}",
            "approval.selected": f"bg:{t['bg_tab']} fg:{t['bright']} bold",
            "tasks": f"fg:{t['fg']}",
            "tasks.title": f"fg:{t['bright']} bold",
            "tasks.section": f"fg:{t['dim']} bold",
            "tasks.running": f"fg:{t['teal']}",
            "tasks.completed": f"fg:{t['green']}",
            "tasks.failed": f"fg:{t['red']}",
            "tasks.muted": f"fg:{t['dim']}",
            "prompt": f"bg:{t['bg_chrome']} fg:{t['green']} bold",
            "mode.chat": f"fg:{t['dim']}",
            "mode.plan": f"fg:{t['blue']}",
            "mode.brainstorm": f"fg:{t['teal']}",
            "mode.build": f"fg:{t['green']}",
            "mode.auto": f"fg:{t['orange']} bold",
            "mode.bypass": f"fg:{t['red']} bold",
            "input": f"bg:{t['bg_chrome']} fg:{t['bright']}",
        }
    )


LAYERED_REPL_STYLE = style_from_tokens(TOKENS)


__all__ = [
    "CARBON_TOKENS",
    "GRAPHITE_TOKENS",
    "LAYERED_REPL_STYLE",
    "SLATE_TOKENS",
    "THEMES",
    "TOKENS",
    "style_from_tokens",
]
