"""Restore cumulative session cost on resume (issue #284).

Session LLM cost is accumulated **in-memory** in each provider module's
``mount()`` closure (a ``_totals`` dict) and contributed to the kernel's
``session.cost`` channel. When a session is resumed the provider re-mounts with
that accumulator back at zero, so the cumulative session-cost counter restarts
from zero. (Per-turn cost still displays correctly; only the running session
total is lost.)

This module reads the prior cumulative cost from the session's persisted
``events.jsonl`` -- every ``llm:response`` event carries ``data.usage.cost_usd``
-- and re-seeds the running total by registering a synthetic ``session.cost``
contributor on the *resumed session's own coordinator*. This mirrors the
``register_contributor`` pattern already used by
``amplifier_foundation.bridge_child_cost`` to bridge child-session cost into a
parent.

Design notes:
- ``register_contributor`` APPENDS (the kernel never overwrites on duplicate
  name), and ``collect_contributions`` sums every registered contributor. The
  fresh per-mount provider accumulator starts at zero on resume and only counts
  turns executed *after* the resume, so the historical contributor and the live
  provider contributor never double-count the same spend.
- Everything here is best-effort and must never break session startup: missing
  or corrupt event files simply yield no restored cost.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from amplifier_foundation.session.history import SessionHistoryStore

logger = logging.getLogger(__name__)

# Channel + event/field names defined by the kernel and provider modules.
SESSION_COST_CHANNEL = "session.cost"
_LLM_RESPONSE_EVENT = "llm:response"


def session_events_path(session_dir: Path) -> Path:
    """Resolve CI's reader-visible relocation before the old CLI log fallback.

    Foundation deliberately does not choose process environment policy. The CI
    base path is a projects root; preserve the native project slug/session ID.
    Like CI, empty, unexpanded, or relative values are not relocation roots.
    """
    raw_root = os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", "").strip()
    root = Path(raw_root).expanduser() if raw_root and "${" not in raw_root else None
    events_path = SessionHistoryStore(session_dir).events_path
    if root is not None and root.is_absolute():
        project_slug = session_dir.parent.parent.name
        capture_dir = root / project_slug / "sessions" / session_dir.name
        events_path = SessionHistoryStore(capture_dir).events_path
    # Never combine both captures: that can count the same model call twice.
    return events_path if events_path.exists() else session_dir / "events.jsonl"


def sum_prior_cost_usd(events_path: Path, *, session_id: str | None = None) -> Decimal | None:
    """Read cost from the shared event reader without constructing a message cache.

    The path is explicit so old CLI root event logs remain supported when the
    host chooses that fallback. CI records are normalized/scoped by Foundation.
    Malformed events and invalid costs never prevent session startup.
    """
    session_dir = events_path.parent
    if session_dir.name == "context-intelligence":
        session_dir = session_dir.parent
    history = SessionHistoryStore(session_dir, events_path=events_path, session_id=session_id)
    total: Decimal | None = None
    for event in history.iter_events():
        if event.get("event") != _LLM_RESPONSE_EVENT:
            continue
        usage = event["data"].get("usage")
        cost = usage.get("cost_usd") if isinstance(usage, dict) else None
        if cost is None:
            continue
        try:
            amount = Decimal(str(cost))
            if not amount.is_finite() or amount < 0:
                continue
            total = (total or Decimal("0")) + amount
        except (InvalidOperation, ValueError):
            continue
    return total


def restore_session_cost(
    coordinator: Any,
    session_id: str,
    events_path: Path,
) -> Decimal | None:
    """Re-seed cumulative session cost on resume via a synthetic contributor.

    Reads the prior cumulative cost from ``events_path`` and, when cost data
    exists, registers a ``session.cost`` contributor on ``coordinator`` so that
    ``collect_contributions("session.cost")`` reports the pre-resume total
    alongside the fresh per-mount provider contributions.

    Returns the restored total (a ``Decimal``), or ``None`` when there was no
    prior cost to restore or registration failed. Never raises.
    """
    prior_total = sum_prior_cost_usd(events_path, session_id=session_id)
    if prior_total is None or prior_total <= 0:
        return None

    try:
        # Freeze the total into the callback default so it is captured by value,
        # and stringify to match the provider modules' contributor payloads
        # (Decimal is not JSON-serializable; sum_cost_usd accepts str or Decimal).
        coordinator.register_contributor(
            SESSION_COST_CHANNEL,
            f"history:{session_id}",
            lambda total=prior_total: {"cost_usd": str(total)},
        )
    except Exception:
        logger.warning(
            "Failed to restore prior session cost for %s; continuing without it",
            session_id,
            exc_info=True,
        )
        return None

    logger.info(
        "Restored prior session cost $%s for resumed session %s",
        prior_total,
        session_id,
    )
    return prior_total
