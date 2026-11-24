"""Explicit full-source snapshots of CLOSED, enrolled prospective sessions.

This is not conversation fork/turn slicing, enrollment of legacy history, or an
offline import grant. Foundation owns every transcript write, adoption and ACK.
The source is fully preflighted/checkpointed, including ALL original assets.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import os
import sys

from . import session_storage_owner as owners
from .session_binding_custody import claim_binding


def _supported():
    if sys.platform != "linux":
        owners.held("path")  # Before path selection, stat, mkdir or a lease.


def _cancelled(callback):
    value = callback()
    if type(value) is not bool:
        owners.held("operation")
    if value:
        raise InterruptedError("Snapshot interrupted; preserve target and operation")
    return False


def _policy_data(projection):
    return {"enabled": projection.known, **asdict(projection.policy)}


def _within_limit(ref, projection):
    from datetime import UTC, datetime, timedelta

    policy = projection.policy
    if not projection.known or policy.retention != "durable":
        owners.held("policy")
    if ((policy.max_bytes is not None and ref["bytes"] > policy.max_bytes)
            or (policy.max_pixels is not None and ref["width"] * ref["height"] > policy.max_pixels)):
        owners.held("source")
    if policy.ttl_seconds is not None:
        created = datetime.fromisoformat(ref["provenance"]["created_at"])
        if created + timedelta(seconds=policy.ttl_seconds) <= datetime.now(UTC):
            owners.held("source")


async def _target_config(source, target_id):
    from amplifier_foundation.session.metadata import SessionMetadataStore
    from .lib.bundle_loader.discovery import DEFAULT_BUNDLE
    from .lib.settings import AppSettings
    from .project_utils import get_project_slug
    from .session_media_policy import meet, resolve_declared_config_async
    from .session_store import extract_session_mode

    metadata = SessionMetadataStore(source.directory).read()
    bundle, _ = extract_session_mode(metadata)
    config = await resolve_declared_config_async(
        bundle or DEFAULT_BUNDLE, AppSettings().with_session(target_id, get_project_slug()),
        session_id=target_id, project_slug=get_project_slug(),
    )
    # Inherited restrictions are a floor. Target declarations/settings can only
    # narrow them; no saved config or current parent's guessed policy is used.
    config["session_media"] = meet(config.get("session_media", {}), _policy_data(source.projection))
    projection = owners.project_policy(target_id, resolved_config=config)
    return config, projection, {key: metadata[key] for key in ("bundle", "model") if key in metadata}


class SnapshotTarget(owners.SessionStorageOwner):
    """Same host cleanup registry, separate target-aware recovery selection."""

    def __init__(self, directory, *, session_id, operation, operation_id):
        super().__init__(directory, session_id=session_id, purpose="resume",
                         operation=operation, operation_id=operation_id)
        self.policy_prefix = None

    def _policy_projection(self):
        if self.resolved_config is not None or self.policy_prefix is None:
            return super()._policy_projection()
        from amplifier_foundation.session.media import MediaPolicy
        from .session_media_policy import closed_policy_config

        try:
            config = closed_policy_config(self.directory, self.session_id, self.policy_prefix)
            return owners.project_policy(self.session_id, resolved_config=config)
        except Exception:
            return owners.PolicyProjection(MediaPolicy(retention="drop"), False)

    def create(self, source, config, projection, metadata, cancelled):
        from amplifier_foundation.session.media import SessionMediaStore
        from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
        from amplifier_foundation.session.versioned_checkpoint import verified_versioned_checkpoint_refs
        from .session_store import SessionStore

        def admit_initial():
            source.check()
            _cancelled(cancelled)

        identity = hashlib.sha256(self.operation_id.encode("ascii")).hexdigest()
        self.binding = VersionedSessionBinding.create_new(
            self.directory, session_id=self.session_id,
            operation_id="snapshot-init-" + identity, admit=admit_initial,
        )
        try:
            claim_binding(self.binding, self)
        except BaseException:
            self.binding.close()
            self.binding = None
            raise
        self.current = self.binding.expected
        self.resolved_config = config  # Actual just-resolved host input, not plan DATA.
        self.projection = projection
        self.media = SessionMediaStore(
            self.directory / "media", self.session_id, policy=projection.policy,
            checkpoint_verifier=verified_versioned_checkpoint_refs, policy_verifier=self.source_policy,
        )
        self.native = SessionStore(self.directory.parent, canonical_binding=self.binding)
        self.native.bind_media_store(self.session_id, self.media)
        self.epoch = self.media.epoch
        # The existing native publication owner captures ACTUAL first-bound
        # restrictions while the issued target is still empty, before adoption.
        receipt = self.native.save_versioned(
            self.session_id, [], metadata, expected=self.current,
            operation_id="snapshot-empty-" + identity, admit=admit_initial,
        )
        receipt.verify()
        self.current = self.policy_prefix = self.binding.expected
        self.resolved_config = None
        self.check()  # Re-resolve current target declarations before any pixels.

    @contextmanager
    def reader(self, ref):
        self.source_policy(ref, purpose="snapshot")
        # Adoption is published but not canonically reachable until target ACK.
        with self.media.resolve_ref(ref, committed=False) as stream:
            self.source_policy(ref, purpose="snapshot")
            yield stream
            self.source_policy(ref, purpose="snapshot")
        self.check()

    def saved_target(self):
        from amplifier_foundation.session.media_conversion import load_candidate_plan
        from amplifier_foundation.session.transcript_v2 import CommittedPrefix

        plan = load_candidate_plan(self.operation)  # Verified selector DATA, never custody.
        if (plan.owner != "amplifier-cli" or plan.target_prefix is None
                or plan.target_session_dir != str(self.directory) or plan.portable_directory is not None
                or plan.selected_records != plan.records or plan.session_id == self.session_id
                or plan.committed_session_dir != str(owners.default_session_directory(plan.session_id))):
            owners.held("operation")
        original = CommittedPrefix(**plan.target_prefix)
        if original.session_id != self.session_id or original.message_count != 0:
            owners.held("operation")
        return plan, original

    def select_policy_prefix(self, original):
        from amplifier_foundation.session.metadata import SessionMetadataStore
        from .session_media_policy import RESTRICTIONS_KEY, restriction_record

        record = SessionMetadataStore(self.directory).read().get(RESTRICTIONS_KEY)
        # A lost post-ACK metadata reply may leave the genuine first-bound
        # projection at the exact saved EMPTY predecessor. Reuse only that
        # restrictive record or the actual current one, under admitted custody
        # and the public original-operation join, never arbitrary prefix data.
        for prefix in (self.current, original):
            try:
                restriction_record(record, session_id=self.session_id, expected=prefix)
            except (ValueError, TypeError):
                continue
            self.policy_prefix = prefix
            return
        owners.held("policy")

    def finish(self, result, plan):
        from amplifier_foundation.session.metadata import SessionMetadataStore
        from .session_media_policy import RESTRICTIONS_KEY, meet, restriction_record

        report = self._report(result)
        report.update(snapshot="full-source-closed-t2", conversation_fork=False,
                      cost_boundary="incomplete-not-computed", source_session_id=plan.session_id)
        if result.state != "acknowledged" or result.checkpoint is None:
            return report  # Historical/superseded is never a current receipt.
        try:
            self.check()
            store = SessionMetadataStore(self.directory)
            restrictions = restriction_record(
                store.read().get(RESTRICTIONS_KEY), session_id=self.session_id,
                expected=self.policy_prefix,
            )
            restrictions = meet(
                restrictions, {"enabled": True, **asdict(self.media.policy)},
            )  # Capture any stricter actual store used for this recovery ACK.
            self.check()
            # The helper owns the non-reentrant publication context through
            # the actual auxiliary write and witness rebind after typed ACK.
            self.publish_restriction_metadata(result.checkpoint, {
                RESTRICTIONS_KEY: {"version": 1, "session_id": self.session_id,
                                   "prefix": asdict(result.commit), "restrictions": restrictions},
                "storage_snapshot": {
                    "kind": "full-source-closed-t2", "source_session_id": plan.session_id,
                    "source_prefix": plan.committed_prefix, "operation_id": self.operation_id,
                    "conversation_fork": False, "cost_boundary": "incomplete-not-computed",
                },
            }, expected_prefix=self.policy_prefix)
            self.current = self.policy_prefix = result.commit
            self.check()
            self.check()
        except BaseException as error:
            self.native.versioned_publication_failure = error
            raise
        return report


def _target_selection(target_id, operation, operation_id):
    _supported()
    owners._identity(target_id)
    owners._identity(operation_id)
    directory = owners._lexical(owners.default_session_directory(target_id))
    operation = owners._lexical(operation)
    owners._private_directory(directory.parent)
    owners._private_directory(operation.parent)
    if (directory.name != target_id or directory == operation
            or directory in operation.parents or operation in directory.parents):
        owners.held("path")
    return directory, operation


async def fork_snapshot(source_id, target_id, operation, operation_id, *, cancelled=lambda: False):
    """Create once. Full-source asset preflight, not selected-prefix read-only."""
    from amplifier_foundation.session import media_activation, media_conversion, versioned_checkpoint
    from amplifier_foundation.session.transcript_source import open_committed_source

    directory, operation = _target_selection(target_id, operation, operation_id)
    if source_id == target_id or os.path.lexists(directory) or os.path.lexists(operation):
        owners.held("operation")  # Damaged/dangling/partial names are also occupied.
    source = owners.default_owner_factory(source_id, purpose="prepare", operation=operation)
    if source is None:
        owners.held("path")
    if source.directory == directory or source.directory in directory.parents or directory in source.directory.parents:
        owners.held("path")
    with source:
        _cancelled(cancelled)
        plan = media_conversion.preview_committed(
            source.directory, expected=source.current, owner="amplifier-cli", fence=source.fence,
        )
        if plan.inline_images:
            owners.held("inline")  # No historical origin/consent producer.
        source.prepare_media()  # Honest ALL-source physical checkpoint effect.
        config, projection, metadata = await _target_config(source, target_id)
        count = 0
        with open_committed_source(source.directory, expected=source.current) as selected:
            for ref in versioned_checkpoint.scan_serialized_refs(
                owners.CommittedRangeView(selected), prefix=source.current, check=source.check,
            ):
                source.source_policy(ref, purpose="snapshot")
                _within_limit(ref, projection)
                count += 1
                if projection.policy.max_images is not None and count > projection.policy.max_images:
                    owners.held("source")
        _cancelled(cancelled)
        # Recheck occupied names after callback-bearing source/policy work.
        if os.path.lexists(directory) or os.path.lexists(operation):
            owners.held("operation")
        target = SnapshotTarget(directory, session_id=target_id, operation=operation,
                                operation_id=operation_id)
        try:
            target.create(source, config, projection, metadata, cancelled)
            original = target.current  # Issued target predecessor AFTER empty native publication.

            @contextmanager
            def both(owner):
                with source.fence(owner), target.fence(owner):
                    yield

            await media_conversion.transfer_committed_prefix(
                plan, operation, fence=both, source_store=source.media,
                target_binding=target.binding, target_store=target.media, expected_target=original,
                asset_reader=target.reader, cancelled=lambda: _cancelled(cancelled),
            )
            with target.binding.publication():
                result = media_activation.apply_committed_candidate(
                    operation, binding=target.binding, expected=original, operation_id=operation_id,
                    owner="amplifier-cli", media_store=target.media, admit=target.admit,
                    cancelled=lambda: _cancelled(cancelled),
                )
            saved_plan, _ = target.saved_target()
            report = target.finish(result, saved_plan)
            source.check()
        finally:
            target.close()  # Failed media/lease settlement retains exact custody.
    return report  # Only after both actual cleanup owners have settled.


def recover_snapshot(target_id, operation, operation_id, *, resume=False, cancelled=lambda: False):
    """Reconcile the original target only. Never construct source or transfer."""
    from amplifier_foundation.session import media_activation

    directory, operation = _target_selection(target_id, operation, operation_id)
    with SnapshotTarget(directory, session_id=target_id, operation=operation,
                        operation_id=operation_id) as target:
        plan, original = target.saved_target()
        kwargs = dict(binding=target.binding, expected=original, operation_id=operation_id,
                      owner="amplifier-cli")
        observed = media_activation.reconcile_committed_candidate(operation, **kwargs)
        if not resume or observed.state in {"not-started", "intent-unconfirmed-no-replay"}:
            report = target._report(observed) | {
                "effect": "read-only", "checkpoint_acknowledged": False,
                "snapshot": "full-source-closed-t2", "conversation_fork": False,
                "cost_boundary": "incomplete-not-computed",
            }
        else:
            _cancelled(cancelled)
            target.select_policy_prefix(original)
            target.prepare_media()
            with target.binding.publication():
                result = media_activation.resume_committed_candidate(
                    operation, **kwargs, media_store=target.media, admit=target.admit,
                    cancelled=lambda: _cancelled(cancelled),
                )
            report = target.finish(result, plan)
    return report