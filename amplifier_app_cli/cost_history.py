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

import json
import logging
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Channel + event/field names defined by the kernel and provider modules.
SESSION_COST_CHANNEL = "session.cost"
_LLM_RESPONSE_EVENT = "llm:response"


def sum_prior_cost_usd(events_path: Path) -> Decimal | None:
    """Sum ``cost_usd`` across every ``llm:response`` event in ``events_path``.

    Returns the cumulative cost as a ``Decimal``, or ``None`` when the file is
    missing/unreadable or contains no cost data. Never raises.

    The file is read one line at a time to stay memory-safe: ``llm:response``
    lines can be very large (they may carry full request payloads), so parsed
    events are never all held in memory at once. A cheap substring pre-filter
    skips JSON-parsing unrelated lines entirely.
    """
    if not events_path.is_file():
        return None

    total: Decimal | None = None
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if _LLM_RESPONSE_EVENT not in line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event") != _LLM_RESPONSE_EVENT:
                    continue

                data = event.get("data")
                usage = data.get("usage") if isinstance(data, dict) else None
                cost = usage.get("cost_usd") if isinstance(usage, dict) else None
                if cost is None:
                    continue
                try:
                    total = (total or Decimal("0")) + Decimal(str(cost))
                except (InvalidOperation, ValueError):
                    continue
    except OSError:
        logger.debug(
            "Could not read events for prior session cost: %s",
            events_path,
            exc_info=True,
        )
        return None

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
    prior_total = sum_prior_cost_usd(events_path)
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
