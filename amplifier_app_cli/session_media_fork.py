"""Native fork integration over Foundation's selected-media closure owner.

The caller holds the actual parent writer through this entire operation. No
parent events, capture data, mutable source paths or live capabilities travel.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import UTC, datetime

from amplifier_foundation.session import count_turns, fork_session_in_memory

from .cost_history import build_fork_cost_boundary


def fork_native_session(
    native, parent_id, messages, metadata, *, turn, new_session_id=None,
    source_media=None, fenced=False, bundle=None,
):
    """Fork the final filtered payload, publishing assets before native rows.

    A media-bearing partial target is retained on failure for inspection; it is
    never retried, silently deleted or reported as a successful conversation.
    Text-only forks retain exclusive save_new semantics and existing metadata.
    """
    from amplifier_foundation import sanitize_message
    from amplifier_foundation.session.checkpoint import acknowledge_legacy_checkpoint, verified_checkpoint_refs
    from amplifier_foundation.session.media import MEDIA_NAMESPACE, MediaPolicy, SessionMediaStore, validate_blob_ref
    from amplifier_foundation.session.media_conversion import adopt_selected_media

    target = None
    target_id = None
    with ExitStack() as resources:
        def adopt(selected, *, session_id, session_dir):
            nonlocal target, target_id, source_media
            if fenced is not True:
                raise RuntimeError("Media fork requires supported parent writer ownership")
            # Validate before creating a target. Limits cover only this exact
            # selected closure; they are not producer defaults or fresh consent.
            refs = [validate_blob_ref(ref) for row in selected
                    for ref in (row.get("metadata", {}).get(MEDIA_NAMESPACE, {}).get("refs", []))]
            policy = MediaPolicy(
                max_bytes=max(1, sum(ref["bytes"] for ref in refs)),
                max_pixels=max([1] + [ref["width"] * ref["height"] for ref in refs]),
                max_images=max(1, len(refs)),
            )
            if source_media is None:
                # Read the actual current primary under the caller's fence,
                # never recover or rewrite it, nor mutate its asset journal
                # solely to export a selected prefix.
                receipt = acknowledge_legacy_checkpoint(
                    native.base_dir / parent_id, session_id=parent_id,
                    expected_messages=(sanitize_message(row) for row in messages
                                       if row.get("role") not in ("system", "developer")),
                )

                def selected_source(ref, *, purpose):
                    found = False
                    # Exhaust even after matching: the iterator's final source
                    # checks are part of the proof, not an optional tail.
                    for accepted in verified_checkpoint_refs(receipt):
                        found = found or ref == accepted
                    return purpose == "transfer" and ref in refs and found

                source_media = SessionMediaStore(
                    native.base_dir.absolute() / parent_id / "media", parent_id,
                    policy=policy, checkpoint_verifier=verified_checkpoint_refs,
                    read_only=True, source_read_verifier=selected_source,
                )
                resources.callback(source_media.close)
            target_id = session_id
            directory = native.reserve_session(session_id)
            directory.chmod(0o700)
            target = SessionMediaStore(
                directory.absolute() / "media", session_id, policy=policy,
                checkpoint_verifier=verified_checkpoint_refs,
            )
            resources.callback(target.close)
            native.bind_media_store(session_id, target)
            resources.callback(native.unbind_media_store, session_id, target)
            return adopt_selected_media(
                selected, source_store=source_media, target_store=target,
                target_session_id=session_id,
            )

        result = fork_session_in_memory(
            messages, turn=turn, parent_id=parent_id, new_session_id=new_session_id,
            media_adopter=adopt,
        )
        child = result.messages or []
        boundary = build_fork_cost_boundary(
            parent_dir=native.base_dir / parent_id, parent_id=parent_id,
            parent_messages=messages, parent_metadata=metadata,
            fork_turn=result.forked_from_turn, child_messages=child,
        )
        now = datetime.now(UTC).isoformat()
        child_metadata = {
            "session_id": result.session_id, "parent_id": parent_id,
            "forked_from_turn": result.forked_from_turn,
            "fork_cost_boundary": boundary, "forked_at": now, "created": now,
            "turn_count": count_turns(child),
            "bundle": metadata.get("bundle", bundle), "model": metadata.get("model"),
        }
        if target is None:
            native.save_new(result.session_id, child, child_metadata)
        else:
            if target_id != result.session_id:
                raise RuntimeError("Fork media owner differs from canonical target")
            native.save(result.session_id, child, child_metadata)
        return result
