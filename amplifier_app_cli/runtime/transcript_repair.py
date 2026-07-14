"""Repair interrupted live transcripts before the next provider turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from amplifier_app_cli.runtime.session_access import session_coordinator


logger = logging.getLogger(__name__)


async def repair_interactive_transcript(
    session: object,
    *,
    persist: Callable[[], Awaitable[None]],
) -> bool:
    """Repair recoverable context damage and persist it; never block a turn."""
    context = session_coordinator(session).get("context")
    if context is None or not hasattr(context, "get_messages"):
        return False
    try:
        messages = await context.get_messages()
        if not messages:
            return False

        from amplifier_foundation.session import diagnose_transcript
        from amplifier_foundation.session import repair_transcript

        diagnosis = diagnose_transcript(messages)
        if diagnosis["status"] != "broken":
            return False
        repaired = repair_transcript(messages, diagnosis)
        if hasattr(context, "set_messages"):
            await context.set_messages(repaired)
        await persist()
        failure_modes = diagnosis.get("failure_modes", [])
        orphan_ids = diagnosis.get("orphaned_tool_ids", [])
        logger.warning(
            "Pre-turn transcript repair: %s (orphaned tool calls: %s).",
            ", ".join(failure_modes),
            ", ".join(orphan_ids) if orphan_ids else "none",
        )
        return True
    except ImportError:
        return False
    except Exception as error:
        logger.debug("Pre-turn transcript repair failed: %s", error)
        return False


__all__ = ["repair_interactive_transcript"]
