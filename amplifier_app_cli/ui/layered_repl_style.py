"""Color roles for the layered terminal application."""

from prompt_toolkit.styles import Style


LAYERED_REPL_STYLE = Style.from_dict(
    {
        "output": "fg:#d1d5db",
        "output.muted": "fg:#71717a italic",
        "selected": "bg:#475569 fg:#ffffff",
        "stream.label": "fg:#67e8f9 bold",
        "stream.thinking": "fg:#a1a1aa italic",
        "stream.text": "fg:#e4e4e7",
        "status": "fg:#8b93a3",
        "status.risk": "fg:#e06c75 bold",
        "plan": "fg:#c9d1e0",
        "plan.header": "fg:#e0a458",
        "plan.done": "fg:#7ec699",
        "plan.active": "fg:#eef2f8 bold",
        "plan.pending": "fg:#6b7487",
        "steering": "fg:#e0a458",
        "tools": "fg:#6b7487",
        "working": "fg:#6b7487",
        "working.glyph": "fg:#e0a458",
        "working.title": "fg:#8b93a3",
        "working.tree": "fg:#4a5163",
        "working.agent": "fg:#a1a1aa",
        "notice": "fg:#6b7487",
        "palette": "fg:#a1a1aa",
        "palette.selected": "bg:#303038 fg:#f4f4f5",
        "palette.phase": "fg:#e0a458",
        "palette.command": "fg:#79d88f bold",
        "palette.source": "fg:#67e8f9",
        "rewind": "fg:#e0a458",
        "evidence": "fg:#6fc3c3",
        "approval": "bg:#2b2930 fg:#d6d9e0",
        "approval.focus": "bg:#2b2930 fg:#e0a458 bold",
        "approval.option": "bg:#2b2930 fg:#858b98",
        "approval.selected": "bg:#5a4728 fg:#ffffff bold",
        "tasks": "fg:#d4d4d8",
        "tasks.title": "fg:#f4f4f5 bold",
        "tasks.section": "fg:#a1a1aa bold",
        "tasks.running": "fg:#67e8f9",
        "tasks.completed": "fg:#86efac",
        "tasks.failed": "fg:#fca5a5",
        "tasks.muted": "fg:#a1a1aa",
        "prompt": "bg:#353c48 fg:#79d88f bold",
        "mode.chat": "fg:#6b7487",
        "mode.plan": "fg:#7aa2f7",
        "mode.brainstorm": "fg:#6fc3c3",
        "mode.build": "fg:#7ec699",
        "mode.auto": "fg:#e0a458 bold",
        "mode.bypass": "fg:#e06c75 bold",
        "input": "bg:#353c48 fg:#f4f4f5",
    }
)


__all__ = ["LAYERED_REPL_STYLE"]
