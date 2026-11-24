"""Explicit selected canonical history transfer, never prose or parent authority.

Only the trusted host's existing parent_messages channel is consumed. The
Foundation format owner validates and copies the selected durable binary closure.
"""

from copy import deepcopy


def snapshot_selected_messages(selected):
    """Freeze an explicit caller-selected carrier before asynchronous setup."""
    if selected is None:
        return None
    if not isinstance(selected, list) or any(not isinstance(row, dict) for row in selected):
        raise ValueError("parent_messages must be explicitly selected canonical rows")
    return deepcopy(selected)


def selected_messages_have_media(selected):
    from amplifier_foundation.session.media import MEDIA_NAMESPACE
    return any(MEDIA_NAMESPACE in (row.get("metadata") or {}) for row in selected or [])


async def inherit_selected_messages(child, parent, selected):
    """Append an independently owned, refs-only snapshot to a fresh child.

    Return whether the caller must obtain its child's durable checkpoint after
    reconstruction metadata is ready and before any provider request. No message
    selection or implicit scan of the parent's context happens here.
    """
    if selected is None:
        return False
    from amplifier_foundation.session.media import MEDIA_NAMESPACE, MediaRefusal
    from amplifier_foundation.session.media_conversion import adopt_selected_media
    from .session_media import CLISessionMedia

    rows = []
    has_refs = False
    for row in snapshot_selected_messages(selected):
        # A parent's prompt/reminders cannot replace the child's own prompt
        # factory. Conversation observations are data, not child authority.
        if row.get("role") in ("system", "developer"):
            continue
        if row.get("role") not in ("user", "assistant", "tool"):
            raise ValueError("Unsupported selected parent message role")
        if row.get("function_call") is not None:
            raise ValueError("Legacy function history requires an explicit canonical selection")
        calls = row.get("tool_calls")
        if calls is not None and (
            row["role"] != "assistant" or not isinstance(calls, list)
            or any(not isinstance(call, dict) for call in calls)
        ):
            raise ValueError("Malformed selected tool-call history")
        content = row.get("content")
        if not isinstance(content, (str, list)):
            raise ValueError("Selected parent messages require canonical content")
        if isinstance(content, list) and any(
            not isinstance(block, dict) or block.get("type") == "image" for block in content
        ):
            raise ValueError("Inherited history must contain refs, not image bytes")
        if isinstance(content, list) and any(
            (block.get("type") == "tool_call" and row["role"] != "assistant")
            or (block.get("type") == "tool_result" and row["role"] != "tool")
            for block in content
        ):
            raise ValueError("Malformed selected tool-call history")
        metadata = row.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Invalid selected parent message metadata")
        if isinstance(metadata, dict) and MEDIA_NAMESPACE in metadata:
            has_refs = True  # Foundation validates the strict namespace/ref shape.
        rows.append(row)
    # Reuse the format owner's pure diagnosis, but never repair a selection by
    # scanning excluded rows or inventing synthetic results. A complete result
    # batch may end the selection; its following answer is not needed for pairing.
    from amplifier_foundation.session.diagnosis import diagnose_transcript
    try:
        diagnosis = diagnose_transcript(rows)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Malformed selected tool-call history") from error
    if any(diagnosis.get(key) for key in (
        "orphaned_tool_ids", "misplaced_tool_ids", "unresolved_tool_ids",
    )):
        raise ValueError("Selected history must preserve complete tool-call/result groups")
    if has_refs:
        source = parent.coordinator.get_capability("cli.session_media")
        target = child.coordinator.get_capability("cli.session_media")
        if not (isinstance(source, CLISessionMedia) and source.durable
                and isinstance(target, CLISessionMedia) and target.durable
                and source.session_id == parent.session_id
                and target.session_id == child.session_id
                and source.store is not target.store):
            raise MediaRefusal("Selected history requires independent source and child owners")
        rows = adopt_selected_media(
            rows, source_store=source.store, target_store=target.store,
            target_session_id=child.session_id,
        )
    context = child.coordinator.get("context")
    if not callable(getattr(context, "add_message", None)):
        raise RuntimeError("Child context cannot accept selected canonical history")
    for row in rows:
        await context.add_message(row)
    return has_refs
