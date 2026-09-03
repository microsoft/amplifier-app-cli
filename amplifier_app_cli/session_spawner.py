"""Session spawning for agent delegation.

Implements sub-session creation with configuration inheritance and overlays.
"""

import copy
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession
from amplifier_foundation import generate_sub_session_id
from amplifier_foundation import bridge_child_cost
from amplifier_foundation import RUNTIME_SKILL_OVERLAY_CAPABILITY

from .agent_config import merge_configs

logger = logging.getLogger(__name__)


# =============================================================================
# Partial-output preservation for delegates that never finish
# =============================================================================
# When tool-delegate's wall-clock timeout fires it cancels the spawn coroutine.
# `child_session.execute()` raises CancelledError, the post-run block below is
# skipped, and everything the agent produced is discarded -- turning a hang into
# data loss. This registry keeps the agent's own assistant text so the delegate
# tool can hand the caller an INCOMPLETE-with-partial result instead of an empty
# failure.
#
# This is the PRODUCER half of the contract whose CONSUMER shipped in
# amplifier-foundation f42f48c (tool-delegate). That side reads an optional
# `session.partial` capability:
#
#     (sub_session_id: str) -> {"text": str, "segments": int, "source": str} | None
#
# and degrades to `partial_available: false` when it is absent, returns None, or
# raises. Nothing here may raise into the timeout path.
#
# NOTE: the transcript is separately checkpointed mid-run (see
# _install_transcript_checkpoint below) so a timed-out sub-session stays
# RESUMABLE. That is a different property from this one: the checkpoint makes
# the work reachable by a later resume, this registry makes it readable by the
# delegate call that timed out, at the moment it gives up.
#
# WHY THE RECORD IS PUBLISHED EAGERLY (and not only on the cancellation path)
#
# The obvious shape -- accumulate privately, publish from an `except
# BaseException:` around execute() -- DOES NOT WORK against the consumer that
# actually shipped, and fails silently rather than loudly. tool-delegate's
# `_await_child_with_deadline` deliberately does NOT wait for a child that is
# slow to unwind: at the deadline it calls `child_task.cancel()`, DETACHES the
# task, and raises immediately. Its `except _DelegateTimeoutExpired:` handler
# then calls `session.partial` straight away -- while the cancelled child task
# has not yet been scheduled to run its own exception handlers. A partial
# published from the child's unwind is therefore published AFTER the consumer
# has already read and reported `partial_available: false`.
#
# Measured, cross-repo, against foundation f42f48c: the app logged "preserved 2
# partial text segment(s), 42 chars" AFTER the delegate had logged "No partial
# output could be recovered". Same run, wrong order, and both halves' own unit
# tests passed. This is exactly the drift the round-trip check exists to catch.
#
# So the record is entered in the registry when the accumulator is installed and
# is updated in place as text arrives -- readable at ANY instant, with no
# dependence on cancellation ordering -- and is REMOVED when the sub-session
# completes normally. `get_partial_output` reads a snapshot; nothing here awaits.

# WHY `text` BLOCKS ALONE WERE NOT ENOUGH (model_performance-eem)
#
# This accumulator originally collected `content_block:end` payloads where
# `block["type"] == "text"` and nothing else. Wired correctly, tested on both
# sides, and STRUCTURALLY INCAPABLE of ever firing on a real workload.
#
# Measured by lane k64 across 18 delegate legs in 7 runs
# (openai-evals-team-ci probes/k64-delegate-timeout-eval/TEXT-WINDOW-TABLE.md):
#
#   * a leg emits AT MOST ONE `text` block (16 legs: exactly 1; 2 legs: zero);
#   * it lands in the final 0.19-0.72 s (mean 0.331 s) of a leg lasting
#     5.4-222.0 s -- about 0.5% of the leg;
#   * everything before it is `thinking` (1-25 blocks/leg) and `tool_call`
#     (0-5).
#
# So a delegate killed by a per-delegate timeout had, by construction,
# accumulated nothing. The one real timeout k64 observed had done 10 thinking
# blocks, 45 tool calls and 11 provider responses -- and correctly returned
# `partial_available: false`, because there was a great deal of work and no
# *text*.
#
# The accumulator therefore also collects `thinking` and `tool_call` blocks,
# in a SEPARATE channel used only when no assistant text exists at all. That
# split is not tidiness, it is the honesty constraint below.
#
# THE HONESTY CONSTRAINT
#
# The CONSUMER (amplifier-foundation f42f48c) picks its own guidance string
# from `bool(text)`, and that string says the partial "is unfinished work
# salvaged from the agent mid-flight -- it has NOT been checked, concluded,
# or self-reviewed". True of assistant prose. NOT true of raw thinking: prose
# is at least addressed to a reader, private reasoning never was. Handing a
# model its own unreviewed reasoning under that sentence is its own defect.
#
# foundation is a different repo and this change does not cross that boundary,
# so honesty is carried the two ways the PRODUCER owns:
#
#   1. `source` becomes "spawn-accumulator:reasoning", distinct from the
#      "spawn-accumulator" a text partial still returns, so a consumer can
#      branch without parsing prose;
#   2. the payload labels itself, at the head AND the tail -- the tail because
#      foundation truncates to the LAST `partial_max_chars` characters
#      (default 20,000), which 25 thinking blocks routinely exceed, so a
#      head-only label is lost on exactly the long partials that need it.
#
# A leg that DID emit assistant text still returns the pre-widening record
# byte for byte -- same text, same segments, same source, therefore the same
# guidance string. The widening only reaches cases that previously returned
# nothing at all.

_PARTIAL_OUTPUTS: dict[str, dict] = {}
_PARTIAL_MAX_SESSIONS = 64

# CHOSEN, NOT MEASURED. `chunks` was effectively self-limiting (at most one
# text block per leg); reasoning is not -- k64 saw up to 25 thinking blocks on
# a single leg, and the wall-clock backstop allows legs of hours. Retain the
# most recent reasoning up to this budget, oldest-first, so the registry
# cannot grow without bound. 5x foundation's 20,000-char forward cap: large
# enough that trimming here is not what the consumer sees, small enough that
# 64 concurrent records stay bounded.
_PARTIAL_REASONING_MAX_CHARS = 100_000

# Per-tool-call rendering limits. A tool input can carry a whole file body;
# the trace is for "what was it doing", not for replaying the call.
_PARTIAL_TOOL_ARGS_SHOWN = 6
_PARTIAL_TOOL_ARG_MAX_CHARS = 120

_RECOVERED_HEADER = (
    "[RECOVERED FROM AN UNFINISHED DELEGATE -- NOT DRAFT OUTPUT]\n"
    "This delegate was killed before it wrote any answer at all. What follows "
    "is NOT prose the agent composed for a reader: it is the agent's own "
    "private reasoning and the trace of the tool calls it made, recovered "
    "from its event stream. None of it was checked, concluded, self-reviewed, "
    "or addressed to anyone. Read it as evidence of what the agent was doing "
    "and what it had already looked at -- never as a partial answer.\n"
)

_RECOVERED_FOOTER = (
    "\n[END OF RECOVERED WORK -- unreviewed agent reasoning and tool-call "
    "trace, not a partial answer]"
)


def _describe_tool_call(block: dict) -> str:
    """One line naming a tool call the agent made, with a short argument digest.

    Shape is measured, not assumed: a real `tool_call` block carries
    ``{"type", "id", "name", "input", "visibility"}`` -- the arguments live
    under ``input``, NOT ``arguments``.
    """
    name = block.get("name") or "<unnamed tool>"
    raw = block.get("input")
    if not isinstance(raw, dict) or not raw:
        return f"{name}()"
    parts: list[str] = []
    for key, value in list(raw.items())[:_PARTIAL_TOOL_ARGS_SHOWN]:
        # Drop the empties that tool schemas default in; they carry no signal
        # and crowd out the arguments that do.
        if value is None or value is False or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, str):
            rendered = value
            if len(rendered) > _PARTIAL_TOOL_ARG_MAX_CHARS:
                rendered = rendered[:_PARTIAL_TOOL_ARG_MAX_CHARS] + "..."
            rendered = repr(rendered)
        else:
            rendered = repr(value)
            if len(rendered) > _PARTIAL_TOOL_ARG_MAX_CHARS:
                rendered = rendered[:_PARTIAL_TOOL_ARG_MAX_CHARS] + "..."
        parts.append(f"{key}={rendered}")
    return f"{name}({', '.join(parts)})"


def _render_recovered_work(reasoning: list[str], tool_calls: list[str]) -> str:
    """Render the no-text channel as a self-labelling payload."""
    sections = [_RECOVERED_HEADER]
    if tool_calls:
        sections.append(
            f"\nTOOL CALLS THE AGENT MADE ({len(tool_calls)}), in order:\n"
            + "\n".join(f"  {i}. {call}" for i, call in enumerate(tool_calls, 1))
            + "\n"
        )
    if reasoning:
        sections.append(
            f"\nAGENT REASONING ({len(reasoning)} segment(s)) -- unreviewed, "
            "never addressed to a reader:\n\n" + "\n\n".join(reasoning) + "\n"
        )
    sections.append(_RECOVERED_FOOTER)
    return "".join(sections)


def _record_has_content(record: dict) -> bool:
    """True when anything at all was accumulated, in any channel."""
    return bool(
        record.get("chunks") or record.get("reasoning") or record.get("tool_calls")
    )


def get_partial_output(sub_session_id: str) -> dict | None:
    """``session.partial`` capability: what a sub-session produced before it died.

    Returns ``{"text", "segments", "source"}`` for a sub-session that was
    cancelled or timed out mid-flight, or ``None`` when nothing was preserved.

    Two channels, and the first that has anything wins:

    * assistant **text** -> exactly the pre-widening record
      (``source: "spawn-accumulator"``, ``segments`` counting text segments);
    * otherwise the agent's **reasoning and tool-call trace** -> a labelled
      payload under ``source: "spawn-accumulator:reasoning"``, with
      ``segments`` counting reasoning segments plus tool calls.

    Reads are destructive -- one delegate call consumes one record -- so the
    registry cannot grow without bound on a long-lived root session. A record
    with nothing in either channel reads as ``None``: "produced nothing" and
    "produced nothing recoverable" are the same answer to the consumer, and
    the widening must not manufacture a partial out of an empty accumulator.
    """
    record = _PARTIAL_OUTPUTS.pop(sub_session_id, None)
    if not record:
        return None
    chunks = list(record.get("chunks") or ())
    if chunks:
        return {
            "text": "".join(chunks),
            "segments": len(chunks),
            "source": "spawn-accumulator",
        }
    reasoning = list(record.get("reasoning") or ())
    tool_calls = list(record.get("tool_calls") or ())
    if not reasoning and not tool_calls:
        return None
    return {
        "text": _render_recovered_work(reasoning, tool_calls),
        "segments": len(reasoning) + len(tool_calls),
        "source": "spawn-accumulator:reasoning",
    }


def _publish_partial(sub_session_id: str, record: dict) -> None:
    """Enter an in-flight accumulator in the registry, evicting oldest-first."""
    while len(_PARTIAL_OUTPUTS) >= _PARTIAL_MAX_SESSIONS:
        oldest = next(iter(_PARTIAL_OUTPUTS))
        _PARTIAL_OUTPUTS.pop(oldest, None)
        logger.warning(
            "Partial-output registry is at its %d-session cap; evicted %s to "
            "make room for %s",
            _PARTIAL_MAX_SESSIONS,
            oldest,
            sub_session_id,
        )
    _PARTIAL_OUTPUTS[sub_session_id] = record


def _discard_partial(sub_session_id: str) -> None:
    """Drop a record for a sub-session that completed normally."""
    _PARTIAL_OUTPUTS.pop(sub_session_id, None)


def _seal_partial(sub_session_id: str, record: dict) -> None:
    """Confirm an accumulator is readable after the sub-session failed to finish.

    The record is normally already published (see the note above); this
    re-publishes it if it was evicted under the cap, and logs what survived.
    Synchronous by design: awaiting anything while unwinding a timeout risks
    blocking past the very deadline that caused the unwind.
    """
    if not _record_has_content(record):
        return
    if sub_session_id not in _PARTIAL_OUTPUTS:
        _publish_partial(sub_session_id, record)
    chunks = record.get("chunks") or []
    reasoning = record.get("reasoning") or []
    tool_calls = record.get("tool_calls") or []
    logger.warning(
        "Sub-session %s did not complete; preserved %d assistant text "
        "segment(s) (%d chars), %d reasoning segment(s) (%d chars), "
        "%d tool call(s)",
        sub_session_id,
        len(chunks),
        sum(len(c) for c in chunks),
        len(reasoning),
        sum(len(r) for r in reasoning),
        len(tool_calls),
    )


def _trim_reasoning(reasoning: list[str]) -> None:
    """Bound retained reasoning in place, dropping oldest segments first.

    Keeps the most recent thinking, which is both the closest to what the
    agent was doing when it died and consistent with the consumer's own
    tail-keeping truncation.
    """
    total = sum(len(segment) for segment in reasoning)
    while len(reasoning) > 1 and total > _PARTIAL_REASONING_MAX_CHARS:
        total -= len(reasoning.pop(0))


def _open_partial(sub_session_id: str, hooks):
    """Start accumulating the agent's in-flight work, published from the first moment.

    Returns ``(record, unregister)``. ``unregister`` is ``None`` when there is
    no hooks coordinator to register against -- in that case nothing can ever
    be accumulated, so nothing is published either and the consumer correctly
    degrades to ``partial_available: false``.

    Three channels are collected, and only one is ever returned (see
    ``get_partial_output``): assistant ``text``, the agent's ``thinking``, and
    its ``tool_call`` trace. Collecting the last two is what makes the feature
    reachable on a real leg at all -- see the module note above.

    The hook is registered at low priority so it observes blocks after the UI
    has rendered them and never influences rendering.
    """
    record: dict = {"chunks": [], "reasoning": [], "tool_calls": []}
    if not hooks:
        return record, None

    from amplifier_core.events import CONTENT_BLOCK_END
    from amplifier_core.hooks import HookResult

    async def _accumulate_partial(event: str, data: dict) -> HookResult:
        block = data.get("block")
        if not isinstance(block, dict):
            return HookResult()
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text") or ""
            if text:
                record["chunks"].append(text)
        elif block_type == "thinking":
            # Measured shape: a thinking block carries its reasoning under
            # `text`, the same field name a text block uses.
            text = block.get("text") or ""
            if text:
                record["reasoning"].append(text)
                _trim_reasoning(record["reasoning"])
        elif block_type == "tool_call":
            record["tool_calls"].append(_describe_tool_call(block))
        return HookResult()

    unregister = hooks.register(
        CONTENT_BLOCK_END,
        _accumulate_partial,
        priority=999,
        name="_spawn_partial",
    )
    _publish_partial(sub_session_id, record)
    return record, unregister


# Capture default sys.path entries at import time.
# Used to filter out bundle-added paths when forwarding sys_paths to subprocess children.
_DEFAULT_SYS_PATHS: frozenset[str] = frozenset(sys.path)


def _extract_bundle_context(session: "AmplifierSession") -> dict | None:
    """Extract serializable bundle context from session.

    Extracts both module resolution paths and mention mappings needed to
    reconstruct bundle context on resume.

    Args:
        session: The session to extract bundle context from.

    Returns:
        Dict with module_paths and mention_mappings, or None if not bundle mode.
    """
    # Get module resolver
    resolver = session.coordinator.get("module-source-resolver")
    if resolver is None:
        return None

    # Extract module paths from resolver
    # Handle both AppModuleResolver (wraps _bundle) and BundleModuleResolver directly
    module_paths: dict[str, str] = {}

    if hasattr(resolver, "_bundle") and hasattr(resolver._bundle, "_paths"):
        # AppModuleResolver wrapping BundleModuleResolver
        module_paths = {k: str(v) for k, v in resolver._bundle._paths.items()}
    elif hasattr(resolver, "_paths"):
        # Direct BundleModuleResolver
        module_paths = {k: str(v) for k, v in resolver._paths.items()}

    if not module_paths:
        # Not bundle mode - no paths to preserve
        return None

    # Extract mention mappings from mention resolver (for @namespace:path resolution)
    mention_mappings: dict[str, str] = {}
    mention_resolver = session.coordinator.get_capability("mention_resolver")
    if mention_resolver and hasattr(mention_resolver, "_bundle_mappings"):
        mention_mappings = {
            k: str(v) for k, v in mention_resolver._bundle_mappings.items()
        }

    return {
        "module_paths": module_paths,
        "mention_mappings": mention_mappings,
    }


def _filter_tools(
    config: dict,
    tool_inheritance: dict[str, list[str]],
    agent_explicit_tools: list[str] | None = None,
) -> dict:
    """Filter tools in config based on tool inheritance policy.

    Args:
        config: Session config containing "tools" list
        tool_inheritance: Policy dict with either:
            - "exclude_tools": list of tool module names to exclude
            - "inherit_tools": list of tool module names to include (allowlist)
        agent_explicit_tools: Optional list of tool module names explicitly declared
            by the agent. These are preserved even if they would be excluded.
            Formula: final_tools = (inherited - excluded) + explicit

    Returns:
        New config dict with filtered tools list
    """
    tools = config.get("tools", [])
    if not tools:
        return config

    exclude_tools = tool_inheritance.get("exclude_tools", [])
    inherit_tools = tool_inheritance.get("inherit_tools")

    # Get explicit tool module names (these are always preserved)
    explicit_modules = set(agent_explicit_tools or [])

    if inherit_tools is not None:
        # Allowlist mode: only include specified tools OR explicit
        filtered_tools = [
            t
            for t in tools
            if t.get("module") in inherit_tools or t.get("module") in explicit_modules
        ]
    elif exclude_tools:
        # Blocklist mode: exclude specified tools UNLESS explicit
        filtered_tools = [
            t
            for t in tools
            if t.get("module") not in exclude_tools
            or t.get("module") in explicit_modules
        ]
    else:
        # No filtering
        return config

    # Return new config with filtered tools
    new_config = dict(config)
    new_config["tools"] = filtered_tools

    logger.debug(
        "Filtered tools: %d -> %d (exclude=%s, inherit=%s)",
        len(tools),
        len(filtered_tools),
        exclude_tools,
        inherit_tools,
    )

    return new_config


def _filter_hooks(
    config: dict,
    hook_inheritance: dict[str, list[str]],
    agent_explicit_hooks: list[str] | None = None,
) -> dict:
    """Filter hooks in config based on hook inheritance policy.

    Args:
        config: Session config containing "hooks" list
        hook_inheritance: Policy dict with either:
            - "exclude_hooks": list of hook module names to exclude
            - "inherit_hooks": list of hook module names to include (allowlist)
        agent_explicit_hooks: Optional list of hook module names explicitly declared
            by the agent. These are preserved even if they would be excluded.
            Formula: final_hooks = (inherited - excluded) + explicit

    Returns:
        New config dict with filtered hooks list
    """
    hooks = config.get("hooks", [])
    if not hooks:
        return config

    exclude_hooks = hook_inheritance.get("exclude_hooks", [])
    inherit_hooks = hook_inheritance.get("inherit_hooks")

    # Get explicit hook module names (these are always preserved)
    explicit_modules = set(agent_explicit_hooks or [])

    if inherit_hooks is not None:
        # Allowlist mode: only include specified hooks OR explicit
        filtered_hooks = [
            h
            for h in hooks
            if h.get("module") in inherit_hooks or h.get("module") in explicit_modules
        ]
    elif exclude_hooks:
        # Blocklist mode: exclude specified hooks UNLESS explicit
        filtered_hooks = [
            h
            for h in hooks
            if h.get("module") not in exclude_hooks
            or h.get("module") in explicit_modules
        ]
    else:
        # No filtering
        return config

    # Return new config with filtered hooks
    new_config = dict(config)
    new_config["hooks"] = filtered_hooks

    logger.debug(
        "Filtered hooks: %d -> %d (exclude=%s, inherit=%s)",
        len(hooks),
        len(filtered_hooks),
        exclude_hooks,
        inherit_hooks,
    )

    return new_config


_REDACTION_SENTINEL = "[REDACTED]"


def _find_redacted_values(value: object, path: str = "") -> list[str]:
    """Recursively collect dotted/bracketed paths still holding the redaction sentinel.

    Used at resume time (see resume_sub_session's credential refresh) to detect
    secret-bearing config fields that were NOT successfully re-hydrated from
    live settings. redact_secrets() (amplifier_core.utils.truncate) replaces
    sensitive values with the literal string "[REDACTED]" before persisting
    session metadata to disk; this is the inverse-direction check that flags
    any such literal still present after the refresh pass.

    Args:
        value: Any nested dict/list/scalar structure (e.g. merged_config["hooks"]).
        path: Internal accumulator for the current traversal path.

    Returns:
        List of paths (e.g. "[2].config.destinations[0].api_key") where the
        sentinel value was found. Empty list if nothing is redacted.
    """
    found: list[str] = []
    if isinstance(value, dict):
        for key, sub_value in value.items():
            found.extend(_find_redacted_values(sub_value, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_redacted_values(item, f"{path}[{index}]"))
    elif value == _REDACTION_SENTINEL:
        found.append(path or "<root>")
    return found


# ---------------------------------------------------------------------------
# Mid-run transcript checkpointing
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS
#
# Both spawn_sub_session and resume_sub_session used to call store.save() only
# AFTER a successful `await child_session.execute(...)`. A wall-clock timeout in
# tool-delegate CANCELS that await, so the save never ran, the child session was
# cleaned up in the outer finally, and nothing about the timed-out sub-session
# ever reached SessionStore. The session_id the caller was handed back therefore
# could not be resumed -- `delegate(session_id=...)` failed with "Session not
# found. May have expired or never existed."
#
# THE CONSTRAINT THAT SHAPES THE FIX
#
# Persisting from the CANCELLATION path would require awaiting
# context.get_messages() while the task is already unwinding a deadline. Any
# await there can block past the very deadline that caused the unwind,
# re-creating the hang the timeout exists to bound -- and store.save() is
# synchronous I/O, so an `asyncio.wait_for` around it could not interrupt the
# part most likely to be slow anyway. So the cancellation path is left ENTIRELY
# UNTOUCHED: it gains no await, no write, and no new code at all. Instead the
# transcript is checkpointed DURING normal execution, where blocking is already
# accepted (the post-run save has always done exactly this work).
#
# WHY THE CHECKPOINT FIRES ON provider:request, NOT provider:response
#
# provider:request is the only point in the orchestrator loop where the message
# list is guaranteed TOOL-PAIR-BALANCED: the previous round's tool results have
# all been appended, and the next assistant message (which may open new
# tool_calls) has not been produced yet. Checkpointing after a response would
# persist an assistant message whose tool_calls have no matching results, and
# resuming that transcript reproduces the "No tool call found for function call
# output" class of provider error. Balanced-by-construction is the point.

_CHECKPOINT_INTERVAL_ENV = "AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S"

# CHOSEN, NOT MEASURED. No data exists on sub-session checkpoint sizes because
# no mid-run checkpoint has ever been written. 30 s bounds the transcript lost
# to a timeout to at most one window, while capping write amplification on
# fast-iterating sub-sessions (which would otherwise rewrite the whole
# transcript once per provider call). Override with the env var above; a
# negative value disables mid-run checkpointing entirely.
_DEFAULT_CHECKPOINT_INTERVAL_S = 30.0


def _checkpoint_interval_s() -> float:
    """Resolve the minimum interval between mid-run transcript checkpoints."""
    raw = os.environ.get(_CHECKPOINT_INTERVAL_ENV)
    if raw is None or raw.strip() == "":
        return _DEFAULT_CHECKPOINT_INTERVAL_S
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            f"Invalid {_CHECKPOINT_INTERVAL_ENV}={raw!r}; "
            f"using default {_DEFAULT_CHECKPOINT_INTERVAL_S}s"
        )
        return _DEFAULT_CHECKPOINT_INTERVAL_S


async def _write_checkpoint(
    session: "AmplifierSession",
    store,
    session_id: str,
    metadata: dict,
) -> bool:
    """Snapshot the live transcript to SessionStore. Best-effort, never raises.

    MUST only ever be called from the NORMAL execution path (pre-execute, or
    from a provider:request hook). Calling it while unwinding a cancellation
    would reintroduce the unbounded await this whole design exists to avoid.

    Returns True if a checkpoint was written, False if it was skipped or failed.
    """
    from datetime import UTC
    from datetime import datetime

    try:
        context = session.coordinator.get("context")
        transcript = await context.get_messages() if context else []
        snapshot = dict(metadata)
        snapshot["status"] = "in_progress"
        snapshot["turn_count"] = len(transcript)
        snapshot["last_updated"] = datetime.now(UTC).isoformat()
        store.save(session_id, transcript, snapshot)
        logger.debug(
            f"Sub-session {session_id} checkpointed ({len(transcript)} messages)"
        )
        return True
    except Exception as e:
        # A failed checkpoint must never take down the run it is protecting.
        # CancelledError is a BaseException and is deliberately NOT caught here:
        # if the hook itself is cancelled, that cancellation must propagate.
        logger.debug(f"Transcript checkpoint for {session_id} failed: {e}")
        return False


async def _install_transcript_checkpoint(
    session: "AmplifierSession",
    store,
    session_id: str,
    metadata: dict,
    write_now: bool = True,
):
    """Register a throttled provider:request checkpoint on ``session``.

    When ``write_now`` is True (the spawn path), one checkpoint is written
    immediately so the session_id is resolvable in SessionStore from before the
    first provider call onward -- a timeout that fires during the very first
    LLM request still leaves a resumable (if short) session rather than
    nothing at all. That write also seeds the throttle.

    When False (the resume path), the store already holds this session and
    nothing has been added to the transcript yet, so no immediate write is
    needed and the first provider:request checkpoints straight away.

    Returns a zero-arg callable that unregisters the hook (a no-op if no hook
    was registered).
    """

    def _noop() -> None:
        return None

    interval = _checkpoint_interval_s()
    if interval < 0:
        # Explicitly opted out: no pre-registration, no mid-run checkpoints.
        return _noop

    last_written: list[float | None] = [None]

    if write_now:
        await _write_checkpoint(session, store, session_id, metadata)
        last_written[0] = time.monotonic()

    hooks = session.coordinator.get("hooks")
    if hooks is None or not hasattr(hooks, "register"):
        # No hook registry: the pre-registration write above still stands, but
        # no mid-run events will fire, so there is nothing further to install.
        return _noop

    try:
        from amplifier_core.events import PROVIDER_REQUEST
        from amplifier_core.hooks import HookResult
    except ImportError as e:  # pragma: no cover - kernel always provides these
        logger.debug(f"Transcript checkpointing unavailable: {e}")
        return _noop

    async def _on_provider_request(event: str, data: dict) -> HookResult:
        now = time.monotonic()
        previous = last_written[0]
        if previous is not None and (now - previous) < interval:
            return HookResult()
        last_written[0] = now
        await _write_checkpoint(session, store, session_id, metadata)
        return HookResult()

    try:
        unregister = hooks.register(
            PROVIDER_REQUEST,
            _on_provider_request,
            priority=999,
            name="_spawn_transcript_checkpoint",
        )
    except Exception as e:
        logger.debug(f"Could not register transcript checkpoint hook: {e}")
        return _noop

    return unregister if callable(unregister) else _noop


async def spawn_sub_session(
    agent_name: str,
    instruction: str,
    parent_session: AmplifierSession,
    agent_configs: dict[str, dict],
    sub_session_id: str | None = None,
    tool_inheritance: dict[str, list[str]] | None = None,
    hook_inheritance: dict[str, list[str]] | None = None,
    orchestrator_config: dict | None = None,
    parent_messages: list[dict] | None = None,
    provider_preferences: list | None = None,
    self_delegation_depth: int = 0,
    session_metadata: dict | None = None,
    use_subprocess: bool = False,
) -> dict:
    """
    Spawn sub-session with agent configuration overlay.

    Precedence policy (this app's choice, not a kernel contract): see
    ``docs/SPAWN_PRECEDENCE.md``. Other apps that register the
    ``session.spawn`` capability may use different precedence.

    Args:
        agent_name: Name of agent from configuration
        instruction: Task for agent to execute
        parent_session: Parent session for inheritance
        agent_configs: Dict of agent configurations
        sub_session_id: Optional explicit ID (generates if None)
        tool_inheritance: Optional tool filtering policy:
            - {"exclude_tools": ["tool-task"]} - inherit all EXCEPT these
            - {"inherit_tools": ["tool-filesystem"]} - inherit ONLY these
        hook_inheritance: Optional hook filtering policy:
            - {"exclude_hooks": ["hooks-logging"]} - inherit all EXCEPT these
            - {"inherit_hooks": ["hooks-approval"]} - inherit ONLY these
        orchestrator_config: Optional orchestrator config to merge into session
            (e.g., {"min_delay_between_calls_ms": 500} for rate limiting)
        parent_messages: Optional list of messages from parent session to inject
            into child's context. Enables context inheritance where child can
            reference parent's conversation history.
        provider_preferences: Optional ordered list of ProviderPreference objects.
            Each preference has provider and model. System tries each in order
            until finding an available provider. Model names support glob patterns.
        self_delegation_depth: Current depth in the self-delegation chain (default: 0).
            Incremented for self-delegation, reset to 0 for named agents.
            Used to prevent infinite recursion.
        use_subprocess: If True, run the agent in a subprocess via
            run_session_in_subprocess instead of in-process. Also
            triggered when spawn_mode: "subprocess" is set in
            merged config. Returns early with output dict.

    Returns:
        Dict with "output" (response) and "session_id" (for multi-turn)

    Raises:
        ValueError: If agent not found or config invalid
    """
    # Get agent configuration
    # Special handling for "self" - spawn with parent's config (no agent overlay)
    if agent_name == "self":
        agent_config = {}  # Empty overlay = inherit parent config as-is
        logger.debug("Self-delegation: using parent config without agent overlay")
    elif agent_name not in agent_configs:
        raise ValueError(f"Agent '{agent_name}' not found in configuration")
    else:
        agent_config = agent_configs[agent_name]

    # Merge parent config with agent overlay
    merged_config = merge_configs(parent_session.config, agent_config)

    # === Issue #233 fix: propagate live agent registry to child ===
    #
    # parent_session.config is the STATIC snapshot captured at session-init.
    # Runtime additions (mode contributions via RuntimeOverlay) live in
    # parent_session.coordinator.config["agents"] and are NOT in the static
    # snapshot. Without this propagation, mode-contributed agents cannot
    # delegate to same-mode siblings.
    #
    # Design: read coordinator.config directly (source of truth), not from
    # any caller-supplied parameter. This ensures the fix works regardless
    # of which code path invoked spawn (tool-delegate, recipe orchestrator,
    # programmatic spawn, etc.). Local (agent_config) declarations win over
    # inherited live registry — never overwrite.
    #
    # Snapshot semantics: child gets a deep-copy at spawn time. Subsequent
    # mode changes in the parent do NOT propagate to an already-running child.
    parent_coord = getattr(parent_session, "coordinator", None)
    if parent_coord is not None:
        try:
            live_agents = (parent_coord.config or {}).get("agents") or {}
        except AttributeError:
            live_agents = {}

        # Reconcile this propagation with the overlay's OWN access-control
        # declaration (agent_config["agents"] -- a Smart Single Value:
        # "none" | list-of-names | "all"/absent). merge_configs() already
        # applied this same declaration once, but only against the STATIC
        # parent snapshot it can see -- a mode-contributed live agent named
        # in an explicit allowlist isn't in that snapshot, so merge_configs
        # resolves it to an empty dict there (this was PR #178/#233's own
        # documented under-delivery: "even an explicit agents: [sibling_b]
        # declaration wouldn't reach sibling_b -- the source dict is the
        # wrong one"). Left unhandled, the block below would then blindly
        # union in the FULL live registry regardless of that declaration,
        # silently re-opening delegation for an agent that said "none" or
        # handing it agents outside its stated allowlist -- defeating the
        # sub-agent access-control contract merge_configs() exists to
        # enforce (commit d609bb2; documented in AGENT_AUTHORING.md).
        #
        # So: apply the declaration a second time here, against the live
        # registry, before it gets merged in. This reconciles both intents
        # in one place -- #233's "mode siblings must be reachable" and the
        # original "agents: declares exactly who I can delegate to."
        #
        # Gate on the OVERLAY (agent_config), NOT on merged_config["agents"].
        # An unrestricted agent (no `agents:` key) whose parent simply has
        # no STATIC agents also produces an empty merged_config["agents"] --
        # gating on emptiness there would wrongly suppress propagation for
        # that agent too. The overlay's own declared value is the only
        # signal that distinguishes "restricted to nothing/some" from
        # "unrestricted, parent just has nothing (yet) in the snapshot."
        agent_filter = agent_config.get("agents")
        if agent_filter == "none":
            live_agents = {}
        elif isinstance(agent_filter, list):
            live_agents = {
                name: cfg for name, cfg in live_agents.items() if name in agent_filter
            }
        # else: "all", None, or absent -- inherit the live registry unchanged
        # (current/original #233 behavior).

        if live_agents:
            # Build a FRESH dict and rebind it; never mutate the dict
            # merged_config already holds. merge_configs() deep-copies the
            # agents dict only when it is non-empty, and merge_agent_dicts()
            # starts from a shallow parent.copy() -- so an EMPTY parent
            # "agents" dict arrives here as the parent session's own object.
            # setdefault()-then-mutate would then write the live registry
            # straight into the parent's live config and hand the child the
            # very same dict (cross-session state leak).
            child_agents = dict(merged_config.get("agents") or {})
            for name, cfg in live_agents.items():
                if name not in child_agents:
                    child_agents[name] = copy.deepcopy(cfg)
            merged_config["agents"] = child_agents
    # === end issue #233 fix (agents) ===

    # Apply tool inheritance filtering if specified
    if tool_inheritance and "tools" in merged_config:
        # Get agent's explicit tool modules to preserve them
        agent_tool_modules = [t.get("module") for t in agent_config.get("tools", [])]
        merged_config = _filter_tools(
            merged_config, tool_inheritance, agent_tool_modules
        )

    # Apply hook inheritance filtering if specified
    if hook_inheritance and "hooks" in merged_config:
        # Get agent's explicit hook modules to preserve them
        agent_hook_modules = [h.get("module") for h in agent_config.get("hooks", [])]
        merged_config = _filter_hooks(
            merged_config, hook_inheritance, agent_hook_modules
        )

    # Defense-in-depth: read routing-resolved provider_preferences from agent config
    # when no explicit preferences were passed by the caller.
    # The routing hook (hooks-routing) writes provider_preferences into agent configs
    # at session:start when resolving model_role declarations in agent frontmatter.
    # Tool-delegate normally reads these and passes them as a function argument, but
    # this fallback ensures spawn_sub_session works without that middleman — any
    # direct caller benefits from frontmatter routing too.
    if not provider_preferences:
        agent_prefs_raw = agent_config.get("provider_preferences")
        if agent_prefs_raw:
            from amplifier_foundation.spawn_utils import ProviderPreference

            provider_preferences = [
                ProviderPreference.from_dict(p) if isinstance(p, dict) else p
                for p in agent_prefs_raw
            ]
            logger.debug(
                "Using routing-resolved provider_preferences from agent config "
                "for agent '%s' (%d preference(s))",
                agent_name,
                len(provider_preferences),
            )

    # Apply provider preferences if specified (ordered fallback chain)
    if provider_preferences:
        from amplifier_foundation import apply_provider_preferences_with_resolution

        merged_config = await apply_provider_preferences_with_resolution(
            merged_config, provider_preferences, parent_session.coordinator
        )

    # Apply orchestrator config override if specified (recipe-level rate limiting)
    # Session reads orchestrator config from: config["session"]["orchestrator"]["config"]
    if orchestrator_config:
        if "session" not in merged_config:
            merged_config["session"] = {}
        if "orchestrator" not in merged_config["session"]:
            merged_config["session"]["orchestrator"] = {}
        if "config" not in merged_config["session"]["orchestrator"]:
            merged_config["session"]["orchestrator"]["config"] = {}
        # Merge orchestrator config (caller's config takes precedence)
        merged_config["session"]["orchestrator"]["config"].update(orchestrator_config)
        logger.debug(
            "Applied orchestrator config override to session.orchestrator.config: %s",
            orchestrator_config,
        )

    # Inject session metadata if provided (enables kernel CP-SM passthrough on session:start/fork)
    # Metadata is surfaced on session:start and session:fork events for observability consumers.
    if session_metadata:
        if "session" not in merged_config:
            merged_config["session"] = {}
        merged_config["session"]["metadata"] = session_metadata
        logger.debug(
            "Injected session_metadata into child session config: %s",
            session_metadata,
        )

    # Generate child session ID using W3C Trace Context span_id pattern
    # Use 16 hex chars (8 bytes) for fixed-length, filesystem-safe IDs
    if not sub_session_id:
        sub_session_id = generate_sub_session_id(
            agent_name=agent_name,
            parent_session_id=parent_session.session_id,
            parent_trace_id=getattr(parent_session, "trace_id", None),
        )
    assert sub_session_id is not None  # Always generated above if not provided

    # Route to subprocess runner if requested via parameter or config
    spawn_mode = merged_config.get("spawn_mode")
    if use_subprocess or spawn_mode == "subprocess":
        from amplifier_foundation.subprocess_runner import run_session_in_subprocess

        project_path = str(
            parent_session.coordinator.get_capability("session.working_dir")
            or Path.cwd()
        )
        child_config = {k: v for k, v in merged_config.items() if k != "spawn_mode"}

        # Extract bundle context to propagate to subprocess child.
        # Without this, bundle-loaded modules and packages are not importable in the child.
        bundle_ctx = _extract_bundle_context(parent_session)
        bundle_pkg_paths = parent_session.coordinator.get_capability(
            "bundle_package_paths"
        )

        result = await run_session_in_subprocess(
            config=child_config,
            prompt=instruction,
            parent_id=parent_session.session_id,
            project_path=project_path,
            session_id=sub_session_id,
            module_paths=bundle_ctx.get("module_paths") if bundle_ctx else None,
            bundle_package_paths=(
                bundle_pkg_paths() if callable(bundle_pkg_paths) else bundle_pkg_paths
            ),
            sys_paths=[p for p in sys.path if p not in _DEFAULT_SYS_PATHS],
            mention_mappings=bundle_ctx.get("mention_mappings") if bundle_ctx else None,
        )

        # Emit session:fork event from parent hooks (finding #14)
        parent_hooks = parent_session.coordinator.get("hooks")
        if parent_hooks:
            await parent_hooks.emit(
                "session:fork",
                {
                    "child_session_id": sub_session_id,
                    "parent_session_id": parent_session.session_id,
                    "agent_name": agent_name,
                    "spawn_mode": "subprocess",
                },
            )

        import json as _json

        try:
            parsed = _json.loads(result)
            if isinstance(parsed, dict) and "output" in parsed:
                return {
                    "output": parsed["output"],
                    "session_id": parsed.get("session_id", sub_session_id),
                    "status": parsed.get("status", "success"),
                    "turn_count": parsed.get("turn_count", 1),
                    "metadata": parsed.get("metadata", {}),
                }
        except (ValueError, TypeError):
            pass
        return {
            "output": result,
            "session_id": sub_session_id,
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }

    # Create child session with parent_id and inherited UX systems (kernel mechanism)
    # NOTE: We intentionally do NOT share parent's loader here.
    # The loader caches modules with their config, so sharing would cause child sessions
    # to get the parent's cached orchestrator config instead of their own.
    # Each session needs its own loader to respect session-specific config (e.g., rate limiting).
    display_system = parent_session.coordinator.display_system
    child_session = AmplifierSession(
        config=merged_config,
        loader=None,  # Let child create its own loader to respect its config
        session_id=sub_session_id,
        parent_id=parent_session.session_id,  # Links to parent
        approval_system=parent_session.coordinator.approval_system,  # Inherit from parent
        display_system=display_system,  # Inherit from parent
    )

    # Notify display system we're entering a nested session (for indentation)
    if hasattr(display_system, "push_nesting"):
        display_system.push_nesting()

    # NOTE: Parent message injection moved to AFTER initialize() because
    # the context module is only mounted during initialize().

    # Register app-layer capabilities for child session BEFORE initialization
    # These must be mounted before initialize() because module loading needs the resolver
    from amplifier_foundation.mentions import ContentDeduplicator

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver

    # Module source resolver - inherit from parent to preserve BundleModuleResolver in bundle mode
    # CRITICAL: Must be mounted BEFORE initialize() so modules with source: directives can be resolved
    parent_resolver = parent_session.coordinator.get("module-source-resolver")
    if parent_resolver:
        await child_session.coordinator.mount("module-source-resolver", parent_resolver)
    else:
        # Fallback to fresh resolver if parent doesn't have one
        resolver = create_foundation_resolver()
        await child_session.coordinator.mount("module-source-resolver", resolver)

    # Share sys.path additions from parent BEFORE initialize()
    # This ensures bundle packages (like amplifier_bundle_python_dev) are importable
    # when child session loads modules that depend on them.
    #
    # Two sources of paths need to be shared:
    # 1. loader._added_paths - individual module paths added during loading
    # 2. bundle_package_paths capability - bundle src/ directories (e.g., python-dev)
    paths_to_share: list[str] = []

    # Source 1: Module paths from parent loader
    if hasattr(parent_session, "loader") and parent_session.loader is not None:
        parent_added_paths = getattr(parent_session.loader, "_added_paths", [])
        paths_to_share.extend(parent_added_paths)

    # Source 2: Bundle package paths (src/ directories from bundles like python-dev)
    # These are registered as a capability during bundle preparation
    bundle_package_paths = parent_session.coordinator.get_capability(
        "bundle_package_paths"
    )
    if bundle_package_paths:
        paths_to_share.extend(bundle_package_paths)

    # Add all paths to sys.path
    if paths_to_share:
        for path in paths_to_share:
            if path not in sys.path:
                sys.path.insert(0, path)
        logger.debug(
            f"Shared {len(paths_to_share)} sys.path entries from parent to child session"
        )

    # Working directory - register BEFORE initialize(). Any capability a module
    # consumes while mounting or in on_session_ready must be registered before
    # initialize(), because module mounting and on_session_ready both run during
    # initialize(); a capability registered afterwards is invisible to them (the
    # module sees it as absent). This affects ANY module, not just hooks.
    # Fall back to cwd so the value is never empty even when the parent
    # session was created without an explicit working_dir capability.
    _child_working_dir = parent_session.coordinator.get_capability(
        "session.working_dir"
    ) or str(Path.cwd().resolve())
    child_session.coordinator.register_capability(
        "session.working_dir", _child_working_dir
    )

    # Initialize child session (mounts modules per merged config)
    # Now the resolver is available for loading modules with source: directives
    await child_session.initialize()

    # === Issue #233 fix: propagate runtime_skill_overlay capability ===
    #
    # Mode-contributed skills are registered as a coordinator capability
    # (RUNTIME_SKILL_OVERLAY_CAPABILITY) rather than in static config.
    # tool-skills in a sub-session reads its OWN coordinator's capability,
    # which is empty unless we propagate from parent here.
    #
    # Note: RUNTIME_CONTEXT_OVERLAY_CAPABILITY is intentionally NOT propagated.
    # Mode-contributed context belongs to "the mode is active here" — that state
    # is root-session only (hooks-mode's provider:request handler lives there).
    # Skills are different: they're discoverable resources, not mode state.
    child_coord = getattr(child_session, "coordinator", None)
    if parent_coord is not None and child_coord is not None:
        try:
            overlay_skills = parent_coord.get_capability(
                RUNTIME_SKILL_OVERLAY_CAPABILITY
            )
        except (AttributeError, KeyError):
            overlay_skills = None
        if overlay_skills:
            try:
                child_coord.register_capability(
                    RUNTIME_SKILL_OVERLAY_CAPABILITY,
                    list(overlay_skills),  # snapshot copy
                )
            except AttributeError:
                pass  # child coordinator without capability support; safe to skip
    # === end issue #233 fix (skill capability) ===

    # Note: Parent context inheritance is now handled by tool-task formatting
    # the parent messages directly into the instruction text. This ensures the
    # child agent sees the context regardless of session/orchestrator behavior.
    # The parent_messages parameter is kept for potential future use.

    # Wire up cancellation propagation: parent cancellation should propagate to child
    # This enables graceful Ctrl+C handling for nested agent sessions
    parent_cancellation = parent_session.coordinator.cancellation
    child_cancellation = child_session.coordinator.cancellation
    parent_cancellation.register_child(child_cancellation)
    logger.debug(
        f"Registered child cancellation token for sub-session {sub_session_id}"
    )

    # Mention resolver - inherit from parent to preserve bundle_override context
    parent_mention_resolver = parent_session.coordinator.get_capability(
        "mention_resolver"
    )
    if parent_mention_resolver:
        child_session.coordinator.register_capability(
            "mention_resolver", parent_mention_resolver
        )
    else:
        # Fallback to fresh resolver if parent doesn't have one
        child_session.coordinator.register_capability(
            "mention_resolver", AppMentionResolver()
        )

    # Mention deduplicator - inherit from parent to preserve session-wide deduplication state
    parent_deduplicator = parent_session.coordinator.get_capability(
        "mention_deduplicator"
    )
    if parent_deduplicator:
        child_session.coordinator.register_capability(
            "mention_deduplicator", parent_deduplicator
        )
    else:
        # Fallback to fresh deduplicator if parent doesn't have one
        child_session.coordinator.register_capability(
            "mention_deduplicator", ContentDeduplicator()
        )

    # Routing capability — inherit so child's hooks-routing can compose runtime overrides.
    # When the parent has a session.routing capability (registered by the routing-matrix
    # bundle), the child's hooks-routing reads it to apply capability_overrides to the
    # effective matrix. Without inheritance the child gets no overrides and may resolve
    # model_role against a different effective matrix than the parent intended.
    parent_routing = parent_session.coordinator.get_capability("session.routing")
    if parent_routing:
        child_session.coordinator.register_capability("session.routing", parent_routing)

    # Self-delegation depth tracking (for recursion limits)
    # This is a simple value capability, not a function
    child_session.coordinator.register_capability(
        "self_delegation_depth", self_delegation_depth
    )

    # Register session spawning capabilities on child session
    # This enables nested agent delegation (child can spawn grandchildren)
    # The capabilities are closures that reference the spawn/resume functions
    async def child_spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: AmplifierSession,
        agent_configs: dict[str, dict],
        sub_session_id: str | None = None,
        tool_inheritance: dict[str, list[str]] | None = None,
        hook_inheritance: dict[str, list[str]] | None = None,
        orchestrator_config: dict | None = None,
        parent_messages: list[dict] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        session_metadata: dict | None = None,
        use_subprocess: bool = False,
    ) -> dict:
        return await spawn_sub_session(
            agent_name=agent_name,
            instruction=instruction,
            parent_session=parent_session,
            agent_configs=agent_configs,
            sub_session_id=sub_session_id,
            tool_inheritance=tool_inheritance,
            hook_inheritance=hook_inheritance,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
            session_metadata=session_metadata,
            use_subprocess=use_subprocess,
        )

    async def child_resume_capability(
        sub_session_id: str,
        instruction: str,
        provider_preferences: list | None = None,
        model_role: str | list[str] | None = None,
    ) -> dict:
        # Kept in step with child_spawn_capability above: a caller that can
        # pin a provider at spawn must be able to pin the same one on every
        # subsequent leg. Both extras are optional so an older caller that
        # still invokes (sub_session_id, instruction) keeps working unchanged.
        return await resume_sub_session(
            sub_session_id=sub_session_id,
            instruction=instruction,
            parent_session=parent_session,
            provider_preferences=provider_preferences,
            model_role=model_role,
        )

    child_session.coordinator.register_capability(
        "session.spawn", child_spawn_capability
    )
    child_session.coordinator.register_capability(
        "session.resume", child_resume_capability
    )
    # Partial-output recovery for grandchildren that time out under this child.
    child_session.coordinator.register_capability("session.partial", get_partial_output)

    # Approval provider (for hooks-approval module, if active)
    register_provider_fn = child_session.coordinator.get_capability(
        "approval.register_provider"
    )
    if register_provider_fn:
        from rich.console import Console

        from amplifier_app_cli.approval_provider import CLIApprovalProvider

        console = Console()
        approval_provider = CLIApprovalProvider(console)
        register_provider_fn(approval_provider)
        logger.debug(f"Registered approval provider for child session {sub_session_id}")

    # Inject agent's system instruction
    # Check top-level instruction first (from agent .md file body), then nested system.instruction
    system_instruction = agent_config.get("instruction") or agent_config.get(
        "system", {}
    ).get("instruction")
    if system_instruction:
        context = child_session.coordinator.get("context")
        # Expand @-mentions in the agent body before injecting as system message.
        # Content lands inline as <context_file> XML blocks prepended to the instruction.
        _resolver = child_session.coordinator.get_capability("mention_resolver")
        if _resolver is not None:
            from amplifier_foundation.mentions import expand_mentions_in_instruction

            _deduplicator = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            _wd = child_session.coordinator.get_capability("session.working_dir")
            _rel_to = Path(_wd) if _wd else Path.cwd()
            system_instruction = await expand_mentions_in_instruction(
                system_instruction,
                resolver=_resolver,
                deduplicator=_deduplicator,
                relative_to=_rel_to,
            )
        if context and hasattr(context, "set_system_prompt_factory"):
            # Register a factory rather than a static system message so
            # hooks that compose onto the system prompt (e.g. the skills
            # visibility hook's "prefix" placement) have a surface to wrap.
            # Without this, those hooks fall back to re-injecting their
            # content on every provider:request, outside the cached prefix.
            # Mirrors amplifier-foundation _prepared.py spawn path.
            _resolved_system_instruction = system_instruction

            async def _system_prompt_factory() -> str:
                return _resolved_system_instruction

            await context.set_system_prompt_factory(_system_prompt_factory)
        elif context and hasattr(context, "add_message"):
            await context.add_message({"role": "system", "content": system_instruction})

    # Register temporary hook to capture orchestrator:complete data
    # This gives us status, turn_count, and metadata from the orchestrator
    completion_data: dict = {}
    hooks = child_session.coordinator.get("hooks")
    unregister_hook = None
    if hooks:
        from amplifier_core.hooks import HookResult

        async def _capture_completion(event: str, data: dict) -> HookResult:
            completion_data.update(data)
            return HookResult()

        unregister_hook = hooks.register(
            "orchestrator:complete",
            _capture_completion,
            priority=999,
            name="_spawn_capture",
        )

    # Accumulate assistant text as it is produced, so a delegate killed by
    # tool-delegate's wall-clock timeout still has recoverable output.
    partial_record, unregister_partial = _open_partial(sub_session_id, hooks)

    # Expand @-mentions in delegation instruction before executing.
    # Content lands inline as <context_file> XML blocks prepended to the instruction.
    if instruction:
        _instr_resolver = child_session.coordinator.get_capability("mention_resolver")
        if _instr_resolver is not None:
            from amplifier_foundation.mentions import expand_mentions_in_instruction

            _instr_dedup = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            _instr_wd = child_session.coordinator.get_capability("session.working_dir")
            _instr_rel = Path(_instr_wd) if _instr_wd else Path.cwd()
            instruction = await expand_mentions_in_instruction(
                instruction,
                resolver=_instr_resolver,
                deduplicator=_instr_dedup,
                relative_to=_instr_rel,
            )

    # ---------------------------------------------------------------------
    # Build persistence state BEFORE execute()
    #
    # Everything the metadata needs is already known at this point, and
    # resume_sub_session reconstructs a session purely from metadata["config"]
    # + metadata["agent_overlay"] + the transcript. Building it here is what
    # lets the transcript be checkpointed DURING the run (see
    # _install_transcript_checkpoint above) instead of only after a successful
    # execute() -- which is what left timed-out sub-sessions unresumable.
    # ---------------------------------------------------------------------
    from datetime import UTC
    from datetime import datetime

    from .session_store import SessionStore

    # Extract or generate trace_id for W3C Trace Context pattern
    # Root session ID is the trace_id, propagate it to all children
    parent_trace_id = getattr(parent_session, "trace_id", parent_session.session_id)

    # Extract child_span from sub_session_id for short_id resolution
    # Format: {parent_id}-{child_span}_{agent_name}
    child_span: str | None = None
    if sub_session_id and "_" in sub_session_id and "-" in sub_session_id:
        base = sub_session_id.rsplit("_", 1)[0]  # Remove agent name
        child_span = base.rsplit("-", 1)[-1]  # Get child_span (16 hex chars)

    metadata = {
        "session_id": sub_session_id,
        "parent_id": parent_session.session_id,
        "trace_id": parent_trace_id,  # W3C Trace Context: trace entire conversation
        "agent_name": agent_name,
        "child_span": child_span,  # For short_id resolution (first 8 chars = short_id)
        "created": datetime.now(UTC).isoformat(),
        "config": merged_config,
        "agent_overlay": agent_config,
        "turn_count": 1,
        "bundle_context": _extract_bundle_context(parent_session),
        "self_delegation_depth": self_delegation_depth,  # For recursion limit tracking
        # Store working_dir for session sync between CLI and web
        "working_dir": str(Path.cwd().resolve()),
    }

    store = SessionStore()
    unregister_checkpoint = await _install_transcript_checkpoint(
        child_session, store, sub_session_id, metadata, write_now=True
    )

    # Execute instruction in child session; cleanup MUST run even on CancelledError
    #
    # NOTE: the `except BaseException` below is SYNCHRONOUS ONLY and must stay
    # that way. A timeout cancels execute(), and the cancellation path must
    # remain free of any await -- the transcript has already been checkpointed
    # above and by the provider:request hook, so nothing needs to be *fetched*
    # while unwinding. The handler only confirms and logs an already-published
    # accumulator; it is NOT what makes the partial readable (see the note at
    # the top of this module -- the consumer often reads before this runs).
    try:
        try:
            response = await child_session.execute(instruction)
        except BaseException:
            # Timed out or cancelled: the post-run block below never runs, so
            # the agent's own partial text stays published for the delegate
            # tool to read. Synchronous only -- see _seal_partial.
            _seal_partial(sub_session_id, partial_record)
            raise
        else:
            # Completed normally: there is no partial to offer, and the
            # registry must not carry one.
            _discard_partial(sub_session_id)
        finally:
            if unregister_hook:
                unregister_hook()
            if unregister_partial:
                unregister_partial()
            unregister_checkpoint()

        # Persist final state for multi-turn resumption
        context = child_session.coordinator.get("context")
        transcript = await context.get_messages() if context else []

        metadata["status"] = "complete"
        metadata["last_updated"] = datetime.now(UTC).isoformat()

        store.save(sub_session_id, transcript, metadata)
        logger.debug(f"Sub-session {sub_session_id} state persisted")

        # Bridge child session costs to parent coordinator (bridge_child_cost never raises)
        await bridge_child_cost(
            child_coordinator=child_session.coordinator,
            parent_coordinator=parent_session.coordinator,
            child_session_id=sub_session_id,
        )

    finally:
        # Unregister child cancellation token before cleanup
        # MUST run even if execution was cancelled (CancelledError) or failed
        parent_cancellation.unregister_child(child_cancellation)
        logger.debug(
            f"Unregistered child cancellation token for sub-session {sub_session_id}"
        )

        # Notify display system we're exiting the nested session (for indentation)
        if hasattr(display_system, "pop_nesting"):
            display_system.pop_nesting()

        # Cleanup child session
        await child_session.cleanup()

    # Return response and session ID for potential multi-turn
    # Include enriched fields from orchestrator:complete hook
    return {
        "output": response,
        "session_id": sub_session_id,
        "status": completion_data.get("status", "success"),
        "turn_count": completion_data.get("turn_count", 1),
        "metadata": completion_data.get("metadata", {}),
    }


# ---------------------------------------------------------------------------
# Provider promotion across the resume boundary
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS (model_performance-rc0 / -n1i)
#
# A delegate spawned with model_role/provider_preferences gets its preferred
# provider promoted to priority 0 by apply_provider_preferences_with_resolution
# (see spawn_sub_session). That symbol used to appear EXACTLY ONCE in this
# file -- inside spawn_sub_session -- so the resume path could not rebuild the
# promotion after anything disturbed it. Combined with the credential refresh
# re-imposing settings `priority` (fixed separately, see
# narrow_overrides_to_secrets), a resumed leg silently re-resolved to the
# settings priority-0 provider: 39 of 66 delegate resumes changed model in a
# 2,078-session archive, 37 of them cheap -> expensive.
#
# The helpers below let resume REBUILD the promotion rather than merely
# preserve it, which additionally re-resolves the preference against the
# CURRENT provider set and gives the honest "could not honour it" signal.


def _normalize_model_role(model_role: str | list[str] | None) -> list[str]:
    """Coerce a model_role declaration to the list form config stores."""
    if not model_role:
        return []
    if isinstance(model_role, str):
        return [model_role]
    return [role for role in model_role if isinstance(role, str)]


def _coerce_provider_preferences(raw: Any) -> list:
    """Coerce persisted/passed preferences to ProviderPreference objects.

    Accepts the dict form (how preferences are persisted in session metadata)
    and already-constructed ProviderPreference objects (how a caller passes
    them). Malformed entries are dropped with a warning rather than taking
    down a resume -- a broken preference must not make a session unresumable.
    """
    if not raw:
        return []

    from amplifier_foundation.spawn_utils import ProviderPreference

    coerced: list = []
    for entry in raw:
        if isinstance(entry, ProviderPreference):
            coerced.append(entry)
            continue
        if isinstance(entry, dict):
            try:
                coerced.append(ProviderPreference.from_dict(entry))
            except ValueError as e:
                logger.warning(
                    "Skipping malformed provider preference %r: %s", entry, e
                )
            continue
        logger.warning("Skipping unusable provider preference %r", entry)
    return coerced


def _provider_entry_keys(entry: dict) -> set[str]:
    """Every name a preference may use to refer to this provider entry.

    Mirrors foundation's _build_provider_lookup: module id, the id-less short
    name ("provider-anthropic" -> "anthropic"), and the instance id.
    """
    module = entry.get("module") or ""
    keys = {module, module.replace("provider-", "")}
    instance_id = entry.get("id")
    if instance_id:
        keys.add(instance_id)
    return {k for k in keys if k}


def _find_promoted_provider(providers: list, preferences: list) -> dict | None:
    """Return the provider entry the preferences actually promoted, if any.

    Checks the OUTCOME (a preferred provider sitting at priority 0) rather
    than trusting the return value of the apply call, so this stays honest
    across foundation versions.
    """
    wanted = {pref.provider for pref in preferences}
    for entry in providers or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("config") or {}).get("priority") != 0:
            continue
        if _provider_entry_keys(entry) & wanted:
            return entry
    return None


def _effective_provider(providers: list) -> dict | None:
    """The entry the session will actually resolve: lowest priority number.

    Used to name what a leg LANDED on when a promotion could not be honoured.
    Ties resolve to the first entry, matching mount-plan ordering.
    """
    best: dict | None = None
    best_priority: float | None = None
    for entry in providers or []:
        if not isinstance(entry, dict):
            continue
        priority = (entry.get("config") or {}).get("priority")
        if not isinstance(priority, (int, float)) or isinstance(priority, bool):
            continue
        if best_priority is None or priority < best_priority:
            best, best_priority = entry, priority
    if best is None and providers:
        first = providers[0]
        return first if isinstance(first, dict) else None
    return best


async def resume_sub_session(
    sub_session_id: str,
    instruction: str,
    parent_session: AmplifierSession | None = None,
    provider_preferences: list | None = None,
    model_role: str | list[str] | None = None,
) -> dict:
    """Resume existing sub-session for multi-turn engagement.

    Loads previously saved sub-session state, recreates the session with
    full context, executes new instruction, and saves updated state.

    Args:
        sub_session_id: ID of existing sub-session to resume
        instruction: Follow-up instruction to execute
        parent_session: Optional parent session (supplies the coordinator used
            to resolve glob model patterns, and a working_dir fallback)
        provider_preferences: Optional ordered list of ProviderPreference
            objects (or their dict form), mirroring spawn_sub_session. When
            omitted, preferences are recovered from the persisted session --
            first the agent overlay, then the mount plan -- so a caller that
            has not yet been taught to thread them still keeps its promotion.
        model_role: Optional model_role declaration to carry onto the resumed
            leg, so its routing hook resolves the SAME role the spawn leg was
            given rather than falling back to settings priority.

    Returns:
        Dict with "output" (response) and "session_id" (same ID)

    Raises:
        FileNotFoundError: If session not found in storage
        RuntimeError: If session metadata corrupted or incomplete
        ValueError: If session_id is invalid
    """
    from datetime import UTC
    from datetime import datetime

    from .session_store import SessionStore

    # Load session state from storage
    store = SessionStore()

    if not store.exists(sub_session_id):
        # NOT RESUMABLE -- say so, and say what to do instead.
        #
        # Mid-run checkpointing (see _install_transcript_checkpoint) means a
        # normally-spawned sub-session is present in SessionStore from before
        # its first provider call, so reaching here means one of: the
        # subprocess spawn path (which returns before any checkpointing),
        # checkpointing explicitly disabled via
        # AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S, an expired/pruned session, or
        # an id that never existed. In every one of those cases the correct
        # caller move is the same and it is NOT "retry the resume" -- so the
        # message names it rather than leaving the caller to infer it.
        raise FileNotFoundError(
            f"Sub-session '{sub_session_id}' not found. Session may have expired or was never created. "
            f"This session is NOT resumable -- re-delegate to start a fresh session instead of retrying "
            f"the resume."
        )

    try:
        transcript, metadata = store.load(sub_session_id)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load sub-session '{sub_session_id}': {str(e)}"
        ) from e

    # Extract reconstruction data
    merged_config = metadata.get("config")
    if not merged_config:
        raise RuntimeError(
            f"Corrupted session metadata for '{sub_session_id}'. Cannot reconstruct session without config."
        )

    # --- Credential refresh ---------------------------------------------------
    # On-disk metadata has secrets (provider api_keys, and hook/destination
    # secrets like the context-intelligence hook's private destination
    # api_key) redacted to "[REDACTED]" (security fix in
    # SessionStore._save_metadata -> redact_secrets()).
    #
    # redact_secrets() builds a NEW dict and never mutates its input, so it
    # only ever touches the PERSISTED snapshot -- the live parent session
    # config held in memory is never poisoned. That's why a FRESH spawn
    # (spawn_sub_session, which merges from parent_session.config above) is
    # unaffected: it always carries real credentials.
    #
    # RESUME is different: `merged_config` here was loaded straight from the
    # redacted on-disk snapshot (metadata["config"]), so EVERY section that
    # can carry a secret must be re-derived from live settings + environment
    # before session creation -- the same pipeline that assembles the ROOT
    # session config in runtime/config.py:resolve_bundle_config() (provider
    # overrides, then hook overrides, then env-var expansion) -- just applied
    # to the loaded snapshot instead of a freshly prepared bundle.
    # --------------------------------------------------------------------------
    if merged_config.get("providers") or merged_config.get("hooks"):
        from amplifier_app_cli.lib.settings import AppSettings
        from amplifier_app_cli.runtime.config import (
            _apply_hook_overrides,
            _apply_provider_overrides,
            _map_id_to_instance_id,
            deep_merge,
            expand_env_vars,
            narrow_overrides_to_secrets,
        )

        _live_settings = AppSettings()

        if merged_config.get("providers"):
            # SECRETS ONLY -- see narrow_overrides_to_secrets() for the full
            # rationale (model_performance-rc0 / -n1i).
            #
            # The unnarrowed merge re-imposed EVERY settings key on the
            # child's own persisted mount plan. `config.priority` is the
            # load-bearing casualty: a sub-session spawned with a
            # model_role/provider_preferences promotion carries priority: 0
            # on the promoted provider, and the settings priority overwrote
            # it -- so the resumed leg silently re-resolved to the settings
            # priority-0 provider (measured: 39/66 delegate resumes changed
            # model, 37 of them cheap -> expensive, basis="priority" on both
            # sides). `reasoning_effort` and every other per-candidate config
            # key were structurally exposed to the same wipe.
            #
            # Only the keys that redact_secrets() actually redacted need
            # restoring here, so only those are allowed through.
            _live_provider_overrides = narrow_overrides_to_secrets(
                _live_settings.get_provider_overrides()
            )
            if _live_provider_overrides:
                _refreshed_providers = _apply_provider_overrides(
                    merged_config["providers"], _live_provider_overrides
                )
                _refreshed_providers = _map_id_to_instance_id(_refreshed_providers)
                merged_config = {**merged_config, "providers": _refreshed_providers}
                logger.debug(
                    "Refreshed credentials for %d provider(s) at resume time",
                    len(_refreshed_providers),
                )

        if merged_config.get("hooks"):
            # Generalization of the provider refresh above. Re-derive hook
            # config from the SAME two live sources resolve_bundle_config()
            # uses to build a fresh session's hooks section:
            #   1. "overrides.<module>.config" in settings.yaml -- applies to
            #      ANY module id, hooks included (AppSettings.get_config_overrides()).
            #   2. Dedicated notification hook overrides
            #      (AppSettings.get_notification_hook_overrides()).
            # This is the piece that was previously MISSING: only providers
            # were refreshed, so a resumed sub-session kept sending
            # `Bearer [REDACTED]` for any hook/destination api_key.
            #
            # DELIBERATE ASYMMETRY with the provider refresh above, which is
            # narrowed to secrets. Hooks are NOT narrowed, for two reasons:
            #   1. Nothing in a hook entry carries per-session RESOLUTION
            #      state. The provider wipe mattered because `config.priority`
            #      decides which model a leg runs on; a hook has no analogue.
            #   2. get_notification_hook_overrides() legitimately APPENDS
            #      hooks that are absent from the persisted plan (see
            #      _apply_hook_overrides). Narrowing to secrets would append
            #      those hooks stripped of `enabled`/`topic`/etc, breaking
            #      notifications on resumed sub-sessions to fix a defect not
            #      observed here.
            # The same over-reach IS structurally possible for a hook whose
            # config an agent overlay customised (settings would re-impose its
            # own value at resume). No instance has been measured; narrowing
            # this path needs its own evidence, not a speculative change.
            _config_overrides = _live_settings.get_config_overrides()
            _refreshed_hooks = merged_config["hooks"]
            if _config_overrides:
                _refreshed_hooks = [
                    {
                        **hook,
                        "config": deep_merge(
                            hook.get("config", {}) or {},
                            _config_overrides[hook["module"]],
                        ),
                    }
                    if isinstance(hook, dict)
                    and hook.get("module") in _config_overrides
                    else hook
                    for hook in _refreshed_hooks
                ]
            _notification_overrides = _live_settings.get_notification_hook_overrides()
            if _notification_overrides:
                _refreshed_hooks = _apply_hook_overrides(
                    _refreshed_hooks, _notification_overrides
                )
            merged_config = {**merged_config, "hooks": _refreshed_hooks}
            logger.debug(
                "Refreshed credentials for %d hook(s) at resume time",
                len(_refreshed_hooks),
            )

        # Expand any ${VAR} references now that live overrides have been
        # spliced in -- covers both providers and hooks in one pass.
        merged_config = expand_env_vars(merged_config)

        # Fail-loud guard: if a secret-bearing field STILL reads the
        # redaction sentinel after the refresh above, no live override
        # existed to restore it (e.g. the secret was baked into the bundle
        # definition itself rather than sourced from settings.yaml).
        # Do NOT silently mount "[REDACTED]" as if it were a usable value --
        # that is exactly how a resumed sub-session ends up sending
        # `Bearer [REDACTED]` and getting a genuine-looking 401 that masks
        # the real cause. Leave the sentinel in place (a downstream guard at
        # header-assembly time is expected to reject/disable it rather than
        # send it) and log loudly so the gap is visible, not swallowed.
        #
        # Scan the ENTIRE merged config, not just hooks. The same silent-
        # sentinel failure mode exists wherever a secret can live: a provider
        # entry with no matching live override keeps its redacted key, tools
        # are not re-hydrated on resume, and any of these can also appear
        # agent-scoped under agents[*]. _find_redacted_values already recurses
        # arbitrary structures, so pointing it at the whole config closes the
        # gap at no extra cost.
        _redacted_paths = _find_redacted_values(merged_config)
        if _redacted_paths:
            logger.warning(
                "Sub-session %s: %d config field(s) still hold the "
                "redaction sentinel '%s' after credential refresh (no live "
                "override found to restore them): %s. These fields are "
                "mounted as-is; the destination/consumer is expected to "
                "reject them rather than receive a fake credential.",
                sub_session_id,
                len(_redacted_paths),
                _REDACTION_SENTINEL,
                _redacted_paths,
            )

    parent_id = metadata.get("parent_id")
    agent_name = metadata.get("agent_name", "unknown")
    trace_id = metadata.get("trace_id")

    # --- Rebuild the provider promotion --------------------------------------
    # The spawn path applies model_role/provider_preferences here (see
    # spawn_sub_session's "Apply provider preferences" block). Resume now does
    # the same, so every leg of a delegate resolves the same way instead of
    # inheriting whatever survived persistence. See the module-level comment
    # above _normalize_model_role for the measured defect this closes.
    _resume_agent_overlay = metadata.get("agent_overlay") or {}

    if model_role:
        # Carry the caller's role onto the resumed leg so its routing hook
        # resolves the SAME role the spawn leg was given.
        merged_config = {
            **merged_config,
            "model_role": _normalize_model_role(model_role),
        }

    # Precedence: what the caller threaded > the agent overlay as persisted >
    # the persisted mount plan's own copy. The last two are recovery sources:
    # they let a caller that still resumes with (session_id, instruction) keep
    # its promotion, which is what makes this fix reach existing sessions.
    _resume_preferences = _coerce_provider_preferences(provider_preferences)
    _preferences_source = "caller"
    if not _resume_preferences:
        _resume_preferences = _coerce_provider_preferences(
            _resume_agent_overlay.get("provider_preferences")
        )
        _preferences_source = "agent_overlay"
    if not _resume_preferences:
        _resume_preferences = _coerce_provider_preferences(
            merged_config.get("provider_preferences")
        )
        _preferences_source = "persisted_config"

    _promotion_fallback: dict | None = None
    if _resume_preferences:
        from amplifier_foundation import apply_provider_preferences_with_resolution

        # parent_session may be absent (the root-registered resume capability
        # passes none). apply_provider_preferences_with_resolution only needs a
        # coordinator to expand GLOB model patterns and already degrades to
        # "use the pattern as-is" when it cannot query one, so passing None is
        # safe rather than fatal.
        _resume_coordinator = (
            parent_session.coordinator if parent_session is not None else None
        )
        merged_config = await apply_provider_preferences_with_resolution(
            merged_config, _resume_preferences, _resume_coordinator
        )

        _promoted = _find_promoted_provider(
            merged_config.get("providers") or [], _resume_preferences
        )
        if _promoted is not None:
            logger.debug(
                "Sub-session %s: re-applied provider promotion on resume "
                "(provider=%s, model=%s, preferences from %s)",
                sub_session_id,
                _promoted.get("module"),
                (_promoted.get("config") or {}).get("default_model"),
                _preferences_source,
            )
        else:
            # FAIL LOUD, DO NOT SILENTLY RE-RESOLVE. Silent re-resolution by
            # settings priority is exactly the defect this fix exists to end;
            # if the pin genuinely cannot be honoured, say so and name what
            # the leg actually landed on.
            _landed = _effective_provider(merged_config.get("providers") or [])
            _promotion_fallback = {
                "session_id": sub_session_id,
                "agent_name": agent_name,
                "reason": "preferred_provider_not_mounted",
                "requested": [pref.to_dict() for pref in _resume_preferences],
                "preferences_source": _preferences_source,
                "provider": (_landed or {}).get("module"),
                "model": (_landed or {}).get("config", {}).get("default_model"),
            }
            logger.warning(
                "Sub-session %s: cannot honour provider preference(s) %s on "
                "resume -- none is mounted in this session's plan. Falling "
                "back to provider=%s model=%s.",
                sub_session_id,
                [pref.provider for pref in _resume_preferences],
                _promotion_fallback["provider"],
                _promotion_fallback["model"],
            )

    # Sub-session resume creates fresh UX systems. Parent UX context (approval history,
    # display state) is not preserved across resume. This is acceptable because:
    # 1. Sub-sessions are typically short-lived agent delegations
    # 2. Serializing full UX state would add significant complexity
    # 3. The parent session may no longer be running when sub-session resumes
    # 4. Approval decisions are contextual to the current execution state
    from amplifier_app_cli.ui import CLIApprovalSystem
    from amplifier_app_cli.ui import CLIDisplaySystem

    logger.debug(
        "Resuming sub-session %s (agent=%s, parent=%s, trace=%s). "
        "UX context (approval history, display state) not preserved - using fresh UX systems.",
        sub_session_id,
        agent_name,
        parent_id,
        trace_id,
    )

    approval_system = CLIApprovalSystem()
    display_system = CLIDisplaySystem()

    child_session = AmplifierSession(
        config=merged_config,
        loader=None,  # Use default loader
        session_id=sub_session_id,  # REUSE same ID
        parent_id=parent_id,
        approval_system=approval_system,
        display_system=display_system,
    )

    # Register app-layer capabilities for resumed child session BEFORE initialization
    # Must be mounted before initialize() so modules with source: directives can be resolved
    from pathlib import Path

    from amplifier_foundation.mentions import ContentDeduplicator

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver

    # Extract bundle context from metadata (saved during spawn_sub_session)
    bundle_context = metadata.get("bundle_context")

    # Module source resolver - restore from bundle context if available
    # CRITICAL: Must be mounted BEFORE initialize() so modules with source: directives can be resolved
    if bundle_context and bundle_context.get("module_paths"):
        # Restore BundleModuleResolver with saved module paths
        from amplifier_foundation.bundle import BundleModuleResolver

        from amplifier_app_cli.lib.bundle_loader import AppModuleResolver

        module_paths = {k: Path(v) for k, v in bundle_context["module_paths"].items()}
        bundle_resolver = BundleModuleResolver(module_paths=module_paths)
        logger.debug(
            f"Restored BundleModuleResolver with {len(module_paths)} module paths"
        )

        # Wrap with AppModuleResolver to provide fallback to settings resolver
        # This is critical for modules (like providers) that may not be in the saved
        # module_paths but are available via user settings/installed providers.
        # Mirrors the wrapping done in session_runner.py and tool.py
        fallback_resolver = create_foundation_resolver()
        resolver = AppModuleResolver(
            bundle_resolver=bundle_resolver,
            settings_resolver=fallback_resolver,
        )
        logger.debug("Wrapped with AppModuleResolver for settings fallback")
    else:
        # Fallback to FoundationSettingsResolver
        resolver = create_foundation_resolver()
    await child_session.coordinator.mount("module-source-resolver", resolver)

    # Working directory - register BEFORE initialize() so any module reading it
    # while mounting or in on_session_ready (both run during initialize()) sees
    # the capability. This affects ANY module, not just hooks.
    # Prefer the value saved in metadata at original spawn time, then fall back
    # to the parent's working_dir if a parent session was supplied, and finally
    # to cwd — so the capability is never absent/empty.
    _child_resume_working_dir = (
        metadata.get("working_dir")
        or (
            parent_session.coordinator.get_capability("session.working_dir")
            if parent_session is not None
            else None
        )
        or str(Path.cwd().resolve())
    )
    child_session.coordinator.register_capability(
        "session.working_dir", _child_resume_working_dir
    )

    # Initialize session (mounts modules per config)
    # Now the resolver is available for loading modules with source: directives
    await child_session.initialize()

    # Mention resolver - restore bundle mappings if available
    if bundle_context and bundle_context.get("mention_mappings"):
        # Restore AppMentionResolver with saved bundle mappings for @namespace:path resolution
        mention_mappings = {
            k: Path(v) for k, v in bundle_context["mention_mappings"].items()
        }
        child_session.coordinator.register_capability(
            "mention_resolver",
            AppMentionResolver(bundle_mappings=mention_mappings),
        )
        logger.debug(
            f"Restored AppMentionResolver with {len(mention_mappings)} bundle mappings"
        )
    else:
        # Fallback to fresh resolver without bundle mappings
        child_session.coordinator.register_capability(
            "mention_resolver", AppMentionResolver()
        )

    # Mention deduplicator - create fresh (deduplication state doesn't persist across resumes)
    child_session.coordinator.register_capability(
        "mention_deduplicator", ContentDeduplicator()
    )

    # Self-delegation depth - restore from metadata for recursion limit tracking
    self_delegation_depth = metadata.get("self_delegation_depth", 0)
    child_session.coordinator.register_capability(
        "self_delegation_depth", self_delegation_depth
    )

    # Register session spawning capabilities on resumed child session
    # This enables nested agent delegation (child can spawn grandchildren)
    # The capabilities are closures that reference the spawn/resume functions
    async def child_spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: "AmplifierSession",
        agent_configs: dict[str, dict],
        sub_session_id: str | None = None,
        tool_inheritance: dict[str, list[str]] | None = None,
        hook_inheritance: dict[str, list[str]] | None = None,
        orchestrator_config: dict | None = None,
        parent_messages: list[dict] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        session_metadata: dict | None = None,
        use_subprocess: bool = False,
    ) -> dict:
        return await spawn_sub_session(
            agent_name=agent_name,
            instruction=instruction,
            parent_session=parent_session,
            agent_configs=agent_configs,
            sub_session_id=sub_session_id,
            tool_inheritance=tool_inheritance,
            hook_inheritance=hook_inheritance,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
            session_metadata=session_metadata,
            use_subprocess=use_subprocess,
        )

    async def child_resume_capability(
        sub_session_id: str,
        instruction: str,
        provider_preferences: list | None = None,
        model_role: str | list[str] | None = None,
    ) -> dict:
        # Kept in step with child_spawn_capability above: a caller that can
        # pin a provider at spawn must be able to pin the same one on every
        # subsequent leg. Both extras are optional so an older caller that
        # still invokes (sub_session_id, instruction) keeps working unchanged.
        return await resume_sub_session(
            sub_session_id=sub_session_id,
            instruction=instruction,
            parent_session=child_session,
            provider_preferences=provider_preferences,
            model_role=model_role,
        )

    child_session.coordinator.register_capability(
        "session.spawn", child_spawn_capability
    )
    child_session.coordinator.register_capability(
        "session.resume", child_resume_capability
    )
    # Partial-output recovery for grandchildren that time out under this child.
    child_session.coordinator.register_capability("session.partial", get_partial_output)

    # Approval provider (for hooks-approval module, if active)
    register_provider_fn = child_session.coordinator.get_capability(
        "approval.register_provider"
    )
    if register_provider_fn:
        from rich.console import Console

        from amplifier_app_cli.approval_provider import CLIApprovalProvider

        console = Console()
        approval_provider = CLIApprovalProvider(console)
        register_provider_fn(approval_provider)
        logger.debug(
            f"Registered approval provider for resumed child session {sub_session_id}"
        )

    # Emit session:resume event for observability
    hooks = child_session.coordinator.get("hooks")
    if hooks:
        await hooks.emit(
            "session:resume",
            {
                "session_id": sub_session_id,
                "parent_id": parent_id,
                "agent_name": agent_name,
                "turn_count": len(transcript) + 1,
            },
        )

        # A promotion that could not be honoured is REPORTED, never silent.
        # Emitted here rather than at merge time because the hook registry
        # only exists once the session is initialized. The payload names the
        # cause AND the provider/model the leg actually landed on, so an
        # observer can tell "the pin was refused" from "the pin was wiped" --
        # the distinction the rc0 archive had no way to make.
        if _promotion_fallback:
            await hooks.emit("provider:fallback", _promotion_fallback)

    # Re-register the agent's system prompt on resume.
    #
    # Mirrors the spawn path (see the "Inject agent's system instruction"
    # block above, ~line 764). That block registers the system instruction
    # via context.set_system_prompt_factory() rather than a persisted
    # message: context-simple builds the system message into a per-request
    # COPY and never writes it into self.messages, so it is never present in
    # the saved transcript. SessionStore._save_transcript also explicitly
    # skips system/developer role messages when persisting, so this holds
    # even for a context module using the add_message() fallback below.
    #
    # Restoring the transcript alone (next block) therefore restores ZERO
    # system-role messages -- every subsequent request on a resumed
    # sub-session ran with no system prompt at all, and omitting it on a
    # chained request CLEARS the provider's server-held prompt rather than
    # preserving it. Recover the same instruction the original spawn used
    # and re-register it through the same mechanism.
    context = child_session.coordinator.get("context")
    agent_overlay = metadata.get("agent_overlay") or {}
    resume_system_instruction = agent_overlay.get("instruction") or agent_overlay.get(
        "system", {}
    ).get("instruction")
    if not resume_system_instruction:
        # Fallback for metadata saved before agent_overlay existed, or an
        # empty inherit-as-is overlay: recover the declaration from the
        # merged config's own agents map, keyed by agent_name.
        _resume_agents_cfg = merged_config.get("agents") or {}
        _resume_agent_decl = _resume_agents_cfg.get(agent_name) or {}
        resume_system_instruction = _resume_agent_decl.get(
            "instruction"
        ) or _resume_agent_decl.get("system", {}).get("instruction")

    if resume_system_instruction:
        # Expand @-mentions exactly like the spawn path does, using the
        # just-restored resolver/deduplicator/working_dir capabilities.
        _resume_sys_resolver = child_session.coordinator.get_capability(
            "mention_resolver"
        )
        if _resume_sys_resolver is not None:
            from amplifier_foundation.mentions import expand_mentions_in_instruction

            _resume_sys_dedup = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            _resume_sys_wd = child_session.coordinator.get_capability(
                "session.working_dir"
            )
            _resume_sys_rel = Path(_resume_sys_wd) if _resume_sys_wd else Path.cwd()
            resume_system_instruction = await expand_mentions_in_instruction(
                resume_system_instruction,
                resolver=_resume_sys_resolver,
                deduplicator=_resume_sys_dedup,
                relative_to=_resume_sys_rel,
            )
        if context and hasattr(context, "set_system_prompt_factory"):
            _resolved_resume_system_instruction = resume_system_instruction

            async def _resume_system_prompt_factory() -> str:
                return _resolved_resume_system_instruction

            await context.set_system_prompt_factory(_resume_system_prompt_factory)
        elif context and hasattr(context, "add_message"):
            await context.add_message(
                {"role": "system", "content": resume_system_instruction}
            )
    else:
        logger.warning(
            "Sub-session %s (agent=%s): no system instruction recoverable from "
            "persisted metadata (agent_overlay / config.agents) on resume. "
            "This resumed session will run WITHOUT a system prompt for all "
            "subsequent requests -- proceeding with resume anyway.",
            sub_session_id,
            agent_name,
        )

    # Restore transcript to context
    if context and hasattr(context, "add_message"):
        for message in transcript:
            await context.add_message(message)
    else:
        logger.warning(
            f"Context module does not support add_message() - transcript not restored for session {sub_session_id}"
        )

    # Register temporary hook to capture orchestrator:complete data
    # This gives us status, turn_count, and metadata from the orchestrator
    completion_data: dict = {}
    hooks = child_session.coordinator.get("hooks")
    unregister_hook = None
    if hooks:
        from amplifier_core.hooks import HookResult

        async def _capture_completion(event: str, data: dict) -> HookResult:
            completion_data.update(data)
            return HookResult()

        unregister_hook = hooks.register(
            "orchestrator:complete",
            _capture_completion,
            priority=999,
            name="_spawn_capture",
        )

    # Accumulate assistant text as it is produced, so a delegate killed by
    # tool-delegate's wall-clock timeout still has recoverable output.
    partial_record, unregister_partial = _open_partial(sub_session_id, hooks)

    # Wire up cancellation propagation if parent session provided
    # Enables graceful Ctrl+C to stop the child after its current tool call
    if parent_session is not None:
        resume_parent_cancellation = parent_session.coordinator.cancellation
        resume_child_cancellation = child_session.coordinator.cancellation
        resume_parent_cancellation.register_child(resume_child_cancellation)
        logger.debug(
            f"Registered child cancellation token for resumed sub-session {sub_session_id}"
        )
    else:
        resume_parent_cancellation = None
        resume_child_cancellation = None

    # Expand @-mentions in the resumed instruction (consistent with spawn path).
    # Content lands inline as <context_file> XML blocks prepended to the instruction.
    if instruction:
        _resume_resolver = child_session.coordinator.get_capability("mention_resolver")
        if _resume_resolver is not None:
            from amplifier_foundation.mentions import expand_mentions_in_instruction

            _resume_dedup = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            _resume_wd = child_session.coordinator.get_capability("session.working_dir")
            _resume_rel = Path(_resume_wd) if _resume_wd else Path.cwd()
            instruction = await expand_mentions_in_instruction(
                instruction,
                resolver=_resume_resolver,
                deduplicator=_resume_dedup,
                relative_to=_resume_rel,
            )

    # Checkpoint the transcript DURING the run so a wall-clock timeout on this
    # resume does not discard the turn (same defect, same fix, as the spawn
    # path above). Installed AFTER the transcript restore so the first
    # checkpoint carries the full history, not an empty list. No immediate
    # write: the store already holds this session, and the resume path adds
    # nothing to the transcript until the first provider call.
    unregister_checkpoint = await _install_transcript_checkpoint(
        child_session, store, sub_session_id, metadata, write_now=False
    )

    # Execute new instruction with full context; cleanup MUST run even on CancelledError
    #
    # NOTE: the `except BaseException` below is synchronous only -- see the
    # spawn path's note. The cancellation path must stay free of any await.
    try:
        try:
            response = await child_session.execute(instruction)
        except BaseException:
            # Timed out or cancelled: the agent's own partial text stays
            # published for the delegate tool to read.
            # Synchronous only -- see _seal_partial.
            _seal_partial(sub_session_id, partial_record)
            raise
        else:
            _discard_partial(sub_session_id)
        finally:
            if unregister_hook:
                unregister_hook()
            if unregister_partial:
                unregister_partial()
            unregister_checkpoint()

        # Update state for next resumption
        metadata["status"] = "complete"
        updated_transcript = await context.get_messages() if context else []
        metadata["turn_count"] = len(updated_transcript)
        metadata["last_updated"] = datetime.now(UTC).isoformat()

        store.save(sub_session_id, updated_transcript, metadata)
        logger.debug(
            f"Sub-session {sub_session_id} state updated (turn {metadata['turn_count']})"
        )

        # Bridge child session costs to parent coordinator (bridge_child_cost never raises)
        if parent_session is not None:
            await bridge_child_cost(
                child_coordinator=child_session.coordinator,
                parent_coordinator=parent_session.coordinator,
                child_session_id=sub_session_id,
            )

    finally:
        # Unregister child cancellation token before cleanup
        # MUST run even if execution was cancelled (CancelledError) or failed
        if (
            resume_parent_cancellation is not None
            and resume_child_cancellation is not None
        ):
            resume_parent_cancellation.unregister_child(resume_child_cancellation)
            logger.debug(
                f"Unregistered child cancellation token for resumed sub-session {sub_session_id}"
            )

        # Cleanup child session
        await child_session.cleanup()

    # Return response and same session ID
    # Include enriched fields from orchestrator:complete hook
    return {
        "output": response,
        "session_id": sub_session_id,
        "status": completion_data.get("status", "success"),
        "turn_count": completion_data.get("turn_count", 1),
        "metadata": completion_data.get("metadata", {}),
    }
