"""Standalone maintenance of CLOSED, enrolled prospective CLI sessions.

Foundation owns the lease, format, writer, scanner, candidate and ACK. This host
owns command purpose, current restrictions, media source routing and cleanup.
No legacy enrollment, history repair, saved consent or reconstructed origin.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import re
import stat

from .session_binding_custody import (
    binding_custody, claim_binding, close_binding, retain_binding,
)


class StorageOwnerHeld(ValueError):
    """Finite public reason selected by code, never external exception text."""


_HELD = {
    "api": "STORAGE_T2_API_HELD: Selected Foundation lacks the required public committed maintenance API.",
    "policy": "STORAGE_T2_POLICY_HELD: Effective current source policy is unknown or disallows durable maintenance.",
    "source": "STORAGE_T2_SOURCE_HELD: Original restricted-source validator is unavailable or current limits refuse.",
    "path": "STORAGE_T2_PATH_HELD: Select an exact private owned enrolled local session and operation path.",
    "inline": "STORAGE_T2_INLINE_HELD: Original inline observation policy is unavailable; source unchanged.",
    "operation": "STORAGE_T2_OPERATION_HELD: Select the original complete operation and stable operation ID.",
}


def held(reason):
    # The only public messages from this controller are this finite table.
    error = StorageOwnerHeld()
    error.reason = reason
    raise error


def public_held(error):
    if type(error) is StorageOwnerHeld and type(getattr(error, "reason", None)) is str:
        return _HELD.get(error.reason)
    return None


def _identity(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", value) is None:
        held("path")
    return value


def _lexical(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        held("path")
    return path


def _private_directory(path):
    """Reject aliases before format/native/media constructors; never chmod."""
    path = _lexical(path)
    for parent in reversed((path, *path.parents)):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            held("path")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        held("path")


def default_session_directory(session_id):
    from amplifier_core.utils.paths import get_amplifier_home
    from .project_utils import get_project_slug

    return get_amplifier_home().absolute() / "projects" / get_project_slug() / "sessions" / _identity(session_id)


def default_owner_factory(session_id, *, purpose, operation=None, operation_id=None):
    """Select only the normal CLI project path, without constructing SessionStore."""
    directory = default_session_directory(session_id)
    if not os.path.lexists(directory / "transcript.v2"):
        return None  # Legacy command keeps its existing private-admission boundary.
    return SessionStorageOwner(directory, session_id=session_id, purpose=purpose,
                               operation=operation, operation_id=operation_id)


@dataclass(frozen=True)
class PolicyProjection:
    """Known live resolved host config is distinct from settings restriction DATA."""

    policy: object
    known: bool


def project_policy(session_id, *, resolved_config=None):
    """Read settings only, never first-run/config preparation/provider discovery.

    Settings may narrow an actual resolved host configuration, but cannot prove
    the absent bundle/child restrictions of a closed session. config.md is NOT
    consulted: prospective saves do not establish it as a current policy owner.
    With no live resolved config, refs/pixels stay held. Text-only maintenance
    may use the restrictive drop policy: it admits no observations at all.
    """
    from amplifier_foundation.session.media import MediaPolicy
    from .lib.settings import AppSettings
    from .project_utils import get_project_slug
    from .session_media import media_configuration

    settings = AppSettings().with_session(session_id, get_project_slug()).get_merged_settings()
    # Runtime settings ordinarily live under config; accept the direct host
    # declaration as another restriction, never let one widen the other.
    config_settings = settings.get("config", {})
    if not isinstance(config_settings, dict):
        held("policy")
    declarations = [mapping["session_media"] for mapping in (settings, config_settings)
                    if "session_media" in mapping]
    policies = []
    for config in (resolved_config, *({"session_media": value} for value in declarations)):
        if config is None:
            continue
        value = media_configuration(config)
        policy = MediaPolicy(**{k: v for k, v in value.items() if k != "enabled"})
        policies.append((value.get("enabled", True), policy))
    known = resolved_config is not None
    if not known:
        return PolicyProjection(MediaPolicy(retention="drop"), False)
    rank = {"drop": 0, "request-only": 1, "durable": 2}
    kwargs = {}
    for name in ("max_bytes", "max_pixels", "max_images", "ttl_seconds"):
        values = [getattr(p, name) for _, p in policies if getattr(p, name) is not None]
        kwargs[name] = min(values) if values else None
    kwargs["retention"] = min((p.retention for _, p in policies), key=rank.__getitem__)
    if not all(enabled for enabled, _ in policies):
        kwargs["retention"] = "drop"
    return PolicyProjection(MediaPolicy(**kwargs), True)


class CommittedRangeView:
    """Seekable <=64KiB byte plumbing over the PUBLIC committed range reader."""

    def __init__(self, source):
        self.source = source
        self.position = 0

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        size = self.source.commit.committed_bytes
        if type(offset) is not int or whence not in (0, 1, 2):
            raise ValueError("Invalid range cursor")
        position = offset + (self.position if whence == 1 else size if whence == 2 else 0)
        if not 0 <= position <= size:
            raise ValueError("Range cursor outside committed prefix")
        self.position = position
        return position

    def read(self, size=-1):
        if type(size) is not int or size < 0 or size > 65536:
            raise ValueError("Bounded explicit range read required")
        stop = min(self.source.commit.committed_bytes, self.position + size)
        raw = self.source.read_range(self.position, stop)
        self.position = stop
        return raw


class SessionStorageOwner:
    """One standalone cleanup owner; never borrows a live root/child binding."""

    def __init__(self, directory, *, session_id, purpose, operation=None, operation_id=None,
                 resolved_config=None):
        self.directory = _lexical(directory)
        self.session_id = _identity(session_id)
        if self.directory.name != session_id or purpose not in {
            "preview", "prepare", "apply", "resume", "status", "export",
        }:
            held("path")
        self.purpose = purpose
        self.operation = _lexical(operation) if operation is not None else None
        self.operation_id = _identity(operation_id) if operation_id is not None else None
        self.resolved_config = resolved_config  # Host object only, never loaded from a plan/CLI JSON.
        self.binding = self.media = self.native = None
        self.projection = None
        self.epoch = None
        self.closed = False
        self.close_attempted = False

    def __enter__(self):
        from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
        from amplifier_foundation.session.transcript_source import open_committed_source

        _private_directory(self.directory.parent)
        _private_directory(self.directory)
        if self.operation is not None:
            if (self.operation == self.directory or self.operation in self.directory.parents
                    or self.directory in self.operation.parents):
                held("path")
            _private_directory(self.operation.parent)
            if os.path.lexists(self.operation):
                _private_directory(self.operation)
        with open_committed_source(self.directory) as source:
            current = source.commit  # Selection DATA; resume supplies enrolled custody.
        self.binding = VersionedSessionBinding.resume(
            self.directory, session_id=self.session_id, expected=current,
        )
        try:
            claim_binding(self.binding, self)
        except BaseException:
            self.binding.close()
            self.binding = None
            raise
        self.current = current
        try:
            self.check()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def check(self):
        if self.closed or self.close_attempted or self.binding is None:
            held("path")
        custody = binding_custody(self.binding)
        if custody is None or custody.owner is not self or custody.error is not None or custody.closing:
            held("path")
        self.binding.check()
        if self.media is not None:
            if (self.native._canonical_binding is not self.binding
                    or self.native._media_stores.get(self.session_id) is not self.media
                    or self.media.root != self.directory / "media"
                    or self.media.epoch != self.epoch):
                held("source")
            if project_policy(self.session_id, resolved_config=self.resolved_config) != self.projection:
                held("policy")

    def close(self):
        if self.closed:
            return
        if self.close_attempted:
            held("path")  # Unknown cleanup is never replayed.
        self.close_attempted = True
        if self.binding is None:
            self.closed = True
            return
        try:
            if self.media is not None:
                self.media.close()  # Must settle before lease/custody release.
                if self.native is not None:
                    self.native.unbind_media_store(self.session_id, self.media)
            close_binding(self.binding, self)
            self.closed = True
        except BaseException as error:
            retain_binding(self.binding, self, error)
            raise

    @contextmanager
    def fence(self, owner):
        if owner != "amplifier-cli":
            held("source")
        self.check()
        with self.binding.publication():
            self.check()
            yield
            self.check()
        self.check()

    def source_policy(self, ref, *, purpose):
        self.check()
        if ref.get("session_id") != self.session_id:
            held("source")
        if not self.projection.known or self.projection.policy.retention != "durable":
            held("policy")
        # Public expiry-neutral ownership/publication check, BEFORE classification.
        self.media.validate_published_ref(ref, check_expiry=False)
        policy = self.projection.policy
        if ((policy.max_bytes is not None and ref["bytes"] > policy.max_bytes)
                or (policy.max_pixels is not None and ref["width"] * ref["height"] > policy.max_pixels)):
            held("source")
        created = datetime.fromisoformat(ref["provenance"]["created_at"])
        if policy.ttl_seconds is not None and created + timedelta(seconds=policy.ttl_seconds) <= datetime.now(UTC):
            held("source")
        if self.media.get_origin_classification(ref) is not None:
            held("source")  # No standalone screenshot/foreign source validator.
        return True

    def prepare_media(self):
        """Preflight every selected ref BEFORE actual checkpoint physical reads."""
        from amplifier_foundation.session import versioned_checkpoint
        from amplifier_foundation.session.media import SessionMediaStore
        from amplifier_foundation.session.transcript_source import open_committed_source
        from .session_store import SessionStore

        scanner = getattr(versioned_checkpoint, "scan_serialized_refs", None)
        if not callable(scanner):
            held("api")  # No private parser/row materialization fallback on fd32.
        if self.media is not None:
            held("operation")  # A failed or completed preparation is not replayed.
        self.projection = project_policy(self.session_id, resolved_config=self.resolved_config)
        if not self.projection.known or self.projection.policy.retention != "durable":
            # Without a known pixel policy, prove the source is ref-free BEFORE
            # even reopening a media writer. Settings/drop defaults cannot
            # authorize a reference just because its identity parses.
            with open_committed_source(self.directory, expected=self.current) as source:
                for _ in scanner(CommittedRangeView(source), prefix=self.current, check=self.check):
                    held("policy")
        self.media = SessionMediaStore(
            self.directory / "media", self.session_id, policy=self.projection.policy,
            checkpoint_verifier=versioned_checkpoint.verified_versioned_checkpoint_refs,
            policy_verifier=self.source_policy,
        )
        self.native = SessionStore(self.directory.parent, canonical_binding=self.binding)
        self.native.bind_media_store(self.session_id, self.media)
        self.epoch = self.media.epoch
        count = 0
        with open_committed_source(self.directory, expected=self.current) as source:
            for ref in scanner(CommittedRangeView(source), prefix=self.current, check=self.check):
                self.source_policy(ref, purpose=self.purpose)
                count += 1
                limit = self.projection.policy.max_images
                if limit is not None and count > limit:
                    held("source")
        self.check()
        with self.binding.publication():
            receipt = self.binding.checkpoint(expected=self.current, media_store=self.media)
            receipt.verify()
            self.check()
        self.check()

    @contextmanager
    def reader(self, ref):
        self.source_policy(ref, purpose=self.purpose)
        with self.media.resolve_ref(ref) as stream:
            self.source_policy(ref, purpose=self.purpose)
            yield stream
            self.source_policy(ref, purpose=self.purpose)
        self.check()

    def eligible_record(self, record):
        self.check()
        return False  # No original historical inline policy producer exists here.

    def origin_for(self, record, block):
        held("inline")  # Never infer an origin or current clock from content.

    def admit(self):
        self.check()
        return True  # Result of current actual owner/policy checks, not input consent.

    def _saved(self, conversion):
        from amplifier_foundation.session.transcript_v2 import CommittedPrefix

        if self.operation is None or self.operation_id is None:
            held("operation")
        plan = conversion.load_candidate_plan(self.operation)
        # Transfers/imports require separate explicit source+target control.
        if (plan.owner != "amplifier-cli" or plan.session_id != self.session_id
                or plan.committed_session_dir != str(self.directory) or plan.target_prefix is not None
                or plan.portable_directory is not None or plan.selected_records != plan.records):
            held("operation")
        original = CommittedPrefix(**plan.committed_prefix)
        if original.session_id != self.session_id:
            held("operation")
        return plan, original

    async def run(self, *, expected_plan=None, expected_json=None, export_assets=False, records=None, backups=()):
        """Async host entry; Click owns its external-process asyncio.run boundary."""
        from amplifier_foundation.session import media_activation as activation, media_conversion as conversion

        self.check()
        if backups:
            held("operation")  # T2 has no implicit backup/source substitution.
        preview = getattr(conversion, "preview_committed", None)
        if self.purpose == "preview":
            if not callable(preview):
                return {"state": "committed-source-only", "commit": asdict(self.current),
                        "conversion": "public-committed-api-required", "checkpoint_acknowledged": False}
            plan = preview(self.directory, expected=self.current, owner="amplifier-cli",
                           fence=self.fence, selected_records=records)
            return {"state": "preview-only-not-applied", "plan": asdict(plan)}
        if not callable(preview) or not callable(getattr(conversion, "load_candidate_plan", None)):
            held("api")
        if self.purpose == "prepare":
            if self.operation is None or self.operation_id is None or os.path.lexists(self.operation):
                held("operation")
            plan = preview(self.directory, expected=self.current, owner="amplifier-cli",
                           fence=self.fence, selected_records=None)
            if expected_json(asdict(plan)) != expected_plan:
                held("operation")
            if plan.inline_images and not plan.existing_refs:
                return {"state": "held-original-policy", "checkpoint_acknowledged": False,
                        "source_unchanged": True, "operation_id": self.operation_id}
            self.prepare_media()
            candidate = await conversion.build_candidate(
                plan, self.operation, fence=self.fence, capability=self.media,
                origin_for=self.origin_for, asset_reader=self.reader,
                eligible_record=self.eligible_record, export_assets=export_assets,
            )
            self.check()
            return {**asdict(candidate), "operation_id": self.operation_id,
                    "checkpoint_acknowledged": False,
                    "optimization": "already-externalized-no-inline-removal" if not plan.inline_images else "partial-or-held"}
        plan, original = self._saved(conversion)
        if expected_plan is not None and expected_json(asdict(plan)) != expected_plan:
            held("operation")
        kwargs = dict(binding=self.binding, expected=original, operation_id=self.operation_id, owner="amplifier-cli")
        if self.purpose == "status":
            result = activation.reconcile_committed_candidate(self.operation, **kwargs)
            return self._report(result) | {"effect": "read-only", "checkpoint_acknowledged": False}
        # Validate original selection and stable intent BEFORE any bootstrap
        # checkpoint/pixel effect. Reconcile is read-only and never supplies ACK.
        state = activation.reconcile_committed_candidate(self.operation, **kwargs)
        if state.state in {"conflict", "superseded-original-operation"}:
            held("operation")
        self.prepare_media()
        if self.purpose in {"apply", "resume"}:
            function = activation.resume_committed_candidate if self.purpose == "resume" else activation.apply_committed_candidate
            result = function(self.operation, **kwargs, media_store=self.media, admit=self.admit)
            self.check()
            return self._report(result)
        if self.purpose == "export":
            portable = self.operation / "portable"
            if not portable.is_dir():
                held("operation")  # New prepare --export-assets required, never rebuild.
            facts = conversion.candidate_facts(self.operation)
            conversion.verify_export(portable)
            with conversion.open_verified_candidate(
                self.operation, session_id=self.session_id, asset_reader=self.reader, expected_source=original,
            ) as (_, _, check):
                check()
                self.check()
            self.export_facts = facts
            return {"state": "portable-integrity-verified", "portable": str(portable),
                    "checkpoint_acknowledged": False, "import_authority": False}
        held("operation")

    def _report(self, result):
        from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt

        receipt = result.checkpoint
        facts = None
        if receipt is not None:
            if type(receipt) is not VersionedCheckpointReceipt:
                held("source")
            facts = receipt.verify()
        return {"state": result.state, "commit": asdict(result.commit) if result.commit is not None else None,
                "checkpoint": facts, "checkpoint_acknowledged": facts is not None,
                "inline_removed_bytes": result.inline_removed_bytes, "held_records": result.held_records,
                "operation_id": self.operation_id,
                "optimization": "no-inline-removal" if result.inline_removed_bytes == 0 else "partial-or-converted"}

    def verify_export_after_close(self):
        """No owner callbacks after exit: check original facts, not new disk authority."""
        if self.purpose != "export":
            return
        from amplifier_foundation.session import media_conversion as conversion

        portable = self.operation / "portable"
        conversion.verify_export(portable)
        if (conversion.reconcile(self.operation, installed=portable / "candidate.jsonl")
                != "installed-bytes-match-checkpoint-unconfirmed"
                or conversion.candidate_facts(self.operation) != self.export_facts):
            held("operation")