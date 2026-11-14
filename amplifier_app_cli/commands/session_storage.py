"""Thin native-v1 commands over Foundation's public conversion/activation APIs.

Candidate integrity is not activation, import authority or durable media ACK.
No transcript reader that performs automatic recovery is used for preview.
Mutation is HELD unless an embedding host supplies live, private admission.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import BinaryIO, Callable, ContextManager, Protocol

import click

from ..shared_root_state import SharedRootSession, _shared_root_platform_supported, shared_root_id_supported


_FAILURES = {
    "prepare": "STORAGE_PREPARE_FAILED: Preparation not confirmed; preserve operation files and inspect status.",
    "apply": "STORAGE_APPLY_FAILED: Activation not confirmed; preserve operation files and inspect status.",
    "resume": "STORAGE_RESUME_FAILED: Resume not confirmed; preserve operation files and inspect status.",
    "export": "STORAGE_EXPORT_FAILED: Portable export not verified; preserve operation files and inspect status.",
}
_REFUSALS = {
    "STORAGE_OWNER_UNSUPPORTED": "Storage requires supported exact session writer ownership",
    "STORAGE_T2_HELD": "HELD: native-v1 converter cannot prepare a versioned binding/header; T2 API pending",
    "STORAGE_LAYOUT_HELD": "Pending or separately gated storage layout; preserve and inspect",
    "STORAGE_ADMISSION_HELD": "HELD: private host maintenance/source admission required",
    "STORAGE_ADMISSION_INVALID": "HELD: actual private StorageAdmission required, not JSON or boolean",
    "STORAGE_MAINTENANCE_HELD": "HELD: SharedRootSession alone cannot quiesce old native descriptors",
    "STORAGE_SOURCE_MISMATCH": "Admission differs from selected native/session/source",
    "STORAGE_MEDIA_MISMATCH": "Admission requires the actual writable native-bound media store",
    "STORAGE_CUSTODY_INVALID": "Maintenance must check actual custody, not return consent",
    "STORAGE_PERMISSION_INVALID": "Permission check must raise on refusal, not return consent",
    "STORAGE_OWNER_MISMATCH": "Storage owner differs from this host",
    "STORAGE_ORIGIN_HELD": "HELD: attested original five-field provenance/retention required",
    "STORAGE_PLAN_MISMATCH": "Fresh typed plan differs from expected plan facts; preview again",
    "STORAGE_OPERATION_EXISTS": "Existing/unknown operation: status and inspect; never rebuild",
    "STORAGE_SOURCE_CONFLICT": "Selected source conflicts with this exact operation",
    "STORAGE_PREPARE_NEEDED": "Prepare needed: no portable closure; use a NEW operation with --export-assets",
}


class _StorageRefusal(ValueError):
    """Adapter-owned finite reason; arbitrary exception text is never a reason."""

    def __init__(self, code):
        self.code = code
        super().__init__(_REFUSALS[code])


def _operation_failure(command, error):
    # Never str/repr/format/log an external exception, its class, chain or notes.
    # Keep the original object privately via `raise ... from error` at the seam.
    if type(error) is _StorageRefusal:
        code = getattr(error, "code", None)
        if type(code) is str and code in _REFUSALS:
            return click.ClickException(code + ": " + _REFUSALS[code])
    return click.ClickException(_FAILURES[command])


class _MaintenanceOwner(Protocol):
    """Host-owned exclusion, established BEFORE opening any participating writer.

    The host must quiesce actual native writers/old descriptors, retain custody
    for the entire admission context, and reject successor factories. A shared
    root lock or a fixed caller promise alone does not implement this contract.
    """

    directory: Path
    session_id: str

    def check(self) -> None: ...


@dataclass(frozen=True)
class _StorageAdmission:
    """Private in-process adapter; never deserialize this from plans/settings.

    admission_factory(native, *, session_id, source, purpose) returns a context
    manager yielding this object under actual maintenance exclusion. Permission
    checks raise on refusal/revocation and return None, not a consent boolean.
    origin_for attests the ORIGINAL five provenance fields AND durable retention;
    unknown history stays held (no invented migration ID, clock or TTL).
    asset_reader independently enforces selected-source/purpose and durable
    retained publication, including refusal of request-only observations.
    No default admission or legacy-session enrollment is supplied by this module.
    """

    native: object
    session_id: str
    source: Path
    maintenance: _MaintenanceOwner
    media_store: object
    check_permission: Callable[[str], None]
    origin_for: Callable[[int, int], dict]
    asset_reader: Callable[[dict], ContextManager[BinaryIO]]


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _expected_plan(path):
    # Facts only. Reject ambiguous JSON, including Python's True == 1 equality.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate expected-plan member")
            result[key] = value
        return result

    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Expected plan exceeds its read bound")
    value = json.loads(raw, object_pairs_hook=pairs)
    if isinstance(value, dict) and set(value) == {"state", "plan"}:
        value = value["plan"]
    return _json(value)


def _selected(native, session_id):
    if not _shared_root_platform_supported() or not shared_root_id_supported(session_id):
        raise _StorageRefusal("STORAGE_OWNER_UNSUPPORTED")
    if native._canonical_binding is not None:
        raise _StorageRefusal("STORAGE_T2_HELD")
    directory = native.base_dir.absolute() / session_id
    for marker in ("transcript.jsonl.append-pending", "transcript.commit.json"):
        if (directory / marker).exists() or (directory / marker).is_symlink():
            raise _StorageRefusal("STORAGE_LAYOUT_HELD")
    return directory / "transcript.jsonl"


@contextmanager
def _admitted(factory, native, session_id, source, purpose):
    from amplifier_foundation.session.media import SessionMediaStore

    if factory is None:
        raise _StorageRefusal("STORAGE_ADMISSION_HELD")
    with factory(native, session_id=session_id, source=source, purpose=purpose) as admission:
        if type(admission) is not _StorageAdmission:
            raise _StorageRefusal("STORAGE_ADMISSION_INVALID")
        if isinstance(admission.maintenance, SharedRootSession):
            raise _StorageRefusal("STORAGE_MAINTENANCE_HELD")

        def check():
            if (admission.native is not native or admission.session_id != session_id
                    or admission.source != source
                    or getattr(admission.maintenance, "directory", None) != source.parent
                    or getattr(admission.maintenance, "session_id", None) != session_id):
                raise _StorageRefusal("STORAGE_SOURCE_MISMATCH")
            if (type(admission.media_store) is not SessionMediaStore
                    or admission.media_store.session_id != session_id
                    or admission.media_store.read_only
                    or native._media_stores.get(session_id) is not admission.media_store):
                raise _StorageRefusal("STORAGE_MEDIA_MISMATCH")
            if admission.maintenance.check() is not None:
                raise _StorageRefusal("STORAGE_CUSTODY_INVALID")
            if admission.check_permission(purpose) is not None:
                raise _StorageRefusal("STORAGE_PERMISSION_INVALID")
            _selected(native, session_id)

        @contextmanager
        def fence(owner):
            if owner != "amplifier-cli":
                raise _StorageRefusal("STORAGE_OWNER_MISMATCH")
            check()
            yield
            check()

        @contextmanager
        def reader(ref):
            check()
            admission.media_store.validate_published_ref(ref)
            with admission.asset_reader(ref) as stream:
                check()
                yield stream
                check()

        def origin(record, block):
            check()
            value = admission.origin_for(record, block)
            if (not isinstance(value, dict) or set(value) != {
                    "kind", "session_id", "id", "created_at", "expires_at"}):
                raise _StorageRefusal("STORAGE_ORIGIN_HELD")
            return value.copy()

        check()
        yield admission, check, fence, reader, origin
        check()


def _fresh_plan(source, session_id, fence, expected):
    from amplifier_foundation.session.media_conversion import preview

    plan = preview(source, owner="amplifier-cli", session_id=session_id, fence=fence)
    if _json(asdict(plan)) != _expected_plan(expected):
        raise _StorageRefusal("STORAGE_PLAN_MISMATCH")
    return plan


def _activation_report(result):
    # Do not serialize the receipt's seal, private paths or callable authority.
    return {
        "state": result.state, "sha256": result.sha256,
        "bytes": result.bytes, "records": result.records,
        "checkpoint": result.checkpoint.verify() if result.checkpoint is not None else None,
    }


def register_session_storage_commands(session, *, store_factory, admission_factory=None):
    @session.group("storage")
    def storage():
        """Inspect storage; changes require private host maintenance admission."""

    @storage.command("preview")
    @click.argument("session_id")
    @click.option("--backup", multiple=True, type=click.Path(path_type=Path),
                  help="Explicit original backup to inventory (never selected for recovery).")
    @click.option("--records", type=click.IntRange(min=0), default=None,
                  help="Selected prefix count; no suffix assets are inventoried.")
    def preview(session_id, backup, records):
        """Read the exact native-v1 primary under supported writer ownership."""
        from amplifier_foundation.session.media_conversion import preview as owner_preview
        native = store_factory()

        @contextmanager
        def fence(owner):
            if owner != "amplifier-cli":
                raise ValueError("Storage owner differs from this host")
            root = SharedRootSession.acquire(session_id)
            try:
                root.held.check()
                yield
                root.held.check()
            finally:
                root.release()

        try:
            source = _selected(native, session_id)
            kwargs = dict(owner="amplifier-cli", session_id=session_id,
                          backups=tuple(path.absolute() for path in backup), selected_records=records)
            if admission_factory is None:
                # Legacy read control only: does NOT establish mutation admission
                # or prove absence of already-open legacy descriptors.
                plan = owner_preview(source, fence=fence, **kwargs)
            else:
                with _admitted(admission_factory, native, session_id, source, "preview") as admitted:
                    plan = owner_preview(source, fence=admitted[2], **kwargs)
        except (OSError, ValueError, RuntimeError, TypeError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(json.dumps({"state": "preview-only-not-applied", "plan": asdict(plan)}, sort_keys=True))

    @storage.command("reconcile")
    @click.argument("operation", type=click.Path(path_type=Path))
    @click.option("--installed", type=click.Path(path_type=Path), default=None)
    def reconcile(operation, installed):
        """Read retained conversion facts; never replay replacement or claim ACK."""
        from amplifier_foundation.session.media_conversion import reconcile as owner_reconcile
        try:
            state = owner_reconcile(operation.absolute(), installed=installed.absolute() if installed else None)
        except (OSError, ValueError, RuntimeError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(json.dumps({"state": state, "effect": "read-only"}, sort_keys=True))

    @storage.command("verify-export")
    @click.argument("portable", type=click.Path(path_type=Path))
    def verify_export(portable):
        """Verify only the portable closure directory, not its private parent operation."""
        from amplifier_foundation.session.media_conversion import verify_export as owner_verify
        try:
            owner_verify(portable.absolute())
        except (OSError, ValueError, RuntimeError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(json.dumps({"state": "portable-integrity-verified", "import_authority": False,
                               "checkpoint_acknowledged": False}, sort_keys=True))

    @storage.command("prepare")
    @click.argument("session_id")
    @click.argument("operation", type=click.Path(path_type=Path))
    @click.option("--expected-plan", required=True, type=click.Path(path_type=Path))
    @click.option("--export-assets", is_flag=True,
                  help="Build a separate portable closure now (not the recovery operation).")
    def prepare(session_id, operation, expected_plan, export_assets):
        """Build once; never overwrite/rebuild an existing or unknown operation."""
        try:
            from amplifier_foundation.session.media_conversion import build_candidate

            native = store_factory()
            source = _selected(native, session_id)
            operation = operation.absolute()
            with _admitted(admission_factory, native, session_id, source, "prepare") as admitted:
                admission, check, fence, reader, origin = admitted
                if operation.exists() or operation.is_symlink():
                    raise _StorageRefusal("STORAGE_OPERATION_EXISTS")
                plan = _fresh_plan(source, session_id, fence, expected_plan)
                candidate = asyncio.run(build_candidate(
                    plan, operation, fence=fence, capability=admission.media_store,
                    origin_for=origin, asset_reader=reader, export_assets=export_assets,
                ))
                check()
                report = asdict(candidate)
                report["optimization"] = (
                    "inline-images-externalized" if plan.inline_images else
                    "already-externalized-no-inline-images" if plan.existing_refs else "no-inline-images"
                )
                report["checkpoint_acknowledged"] = False
            output = _json(report)
        except Exception as error:
            raise _operation_failure("prepare", error) from error
        click.echo(output)

    def activate(session_id, operation, expected_plan, *, resume):
        try:
            from amplifier_foundation.session.media_activation import apply_candidate, resume_candidate

            native = store_factory()
            source = _selected(native, session_id)
            with _admitted(admission_factory, native, session_id, source,
                           "resume" if resume else "apply") as admitted:
                admission, check, fence, reader, _ = admitted
                _fresh_plan(source, session_id, fence, expected_plan)

                def admit():
                    check()
                    return True  # Derived from the live checks, never input authority.

                result = (resume_candidate if resume else apply_candidate)(
                    operation.absolute(), source.parent, session_id=session_id,
                    owner="amplifier-cli", fence=fence, admit=admit,
                    asset_reader=reader, commit_media=admission.media_store.acknowledge_checkpoint,
                )
                report = _activation_report(result)
            output = _json(report)
        except Exception as error:
            raise _operation_failure("resume" if resume else "apply", error) from error
        click.echo(output)

    @storage.command("apply")
    @click.argument("session_id")
    @click.argument("operation", type=click.Path(path_type=Path))
    @click.option("--expected-plan", required=True, type=click.Path(path_type=Path))
    def apply(session_id, operation, expected_plan):
        """Apply once under live host admission; a plan is not authority."""
        activate(session_id, operation, expected_plan, resume=False)

    @storage.command("resume")
    @click.argument("session_id")
    @click.argument("operation", type=click.Path(path_type=Path))
    @click.option("--expected-plan", required=True, type=click.Path(path_type=Path))
    def resume(session_id, operation, expected_plan):
        """Reacquire admission and ACK actual bytes; never replay replacement."""
        activate(session_id, operation, expected_plan, resume=True)

    @storage.command("status")
    @click.argument("session_id")
    @click.argument("operation", type=click.Path(path_type=Path))
    def status(session_id, operation):
        """Read conversion AND activation facts; on-disk ACK is not live ACK."""
        from amplifier_foundation.session.media_activation import reconcile_activation
        from amplifier_foundation.session.media_conversion import reconcile

        try:
            native = store_factory()
            source = _selected(native, session_id)
            conversion = reconcile(operation.absolute(), installed=source)
            # Unknown/incomplete operations remain readable without inventing a
            # journal, receipt or activation binding.
            activation = None
            if conversion != "interrupted-preserve-and-inspect-journal":
                activation = _activation_report(reconcile_activation(
                    operation.absolute(), source.parent, session_id=session_id, owner="amplifier-cli",
                ))
        except (OSError, ValueError, RuntimeError, TypeError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(_json({"conversion": conversion, "activation": activation, "effect": "read-only"}))

    @storage.command("export")
    @click.argument("session_id")
    @click.argument("operation", type=click.Path(path_type=Path))
    def export(session_id, operation):
        """Expose ONLY an already-built verified portable closure, never its parent."""
        try:
            from amplifier_foundation.session.media_activation import reconcile_activation
            from amplifier_foundation.session.media_conversion import (
                iter_verified_candidate_refs, reconcile, verify_export,
            )

            native = store_factory()
            source = _selected(native, session_id)
            operation = operation.absolute()
            with _admitted(admission_factory, native, session_id, source, "export") as admitted:
                reconcile(operation)
                activation = reconcile_activation(
                    operation, source.parent, session_id=session_id, owner="amplifier-cli",
                )
                if activation.state == "conflict":
                    raise _StorageRefusal("STORAGE_SOURCE_CONFLICT")
                # Immutable DATA captured while this exact operation is admitted.
                # Later on-disk facts cannot become replacement authority.
                admitted_candidate = (activation.sha256, activation.bytes, activation.records)
                portable = operation / "portable"
                if not portable.exists() and not portable.is_symlink():
                    raise _StorageRefusal("STORAGE_PREPARE_NEEDED")
                verify_export(portable)
                # Bind this independently verified portable closure to THIS
                # operation and recheck host source/retention permission per ref.
                for _ in iter_verified_candidate_refs(
                    operation, installed=portable / "candidate.jsonl",
                    session_id=session_id, asset_reader=admitted[3],
                ):
                    pass
            # Recheck bytes AND selection AFTER callback-bearing operation/ref
            # join, final admission checks AND host cleanup. Public read-only
            # checks below invoke no host callbacks and grant no new admission.
            # A valid substituted closure (or operation+closure) is insufficient:
            # compare against the facts captured above, not new disk authority.
            verify_export(portable)
            if reconcile(operation, installed=portable / "candidate.jsonl") != (
                    "installed-bytes-match-checkpoint-unconfirmed"):
                raise ValueError("Portable export no longer matches the admitted operation")
            final_activation = reconcile_activation(
                operation, source.parent, session_id=session_id, owner="amplifier-cli",
            )
            if (final_activation.sha256, final_activation.bytes, final_activation.records) != admitted_candidate:
                raise ValueError("Portable export differs from admitted candidate facts")
            if final_activation.state == "conflict":
                raise _StorageRefusal("STORAGE_SOURCE_CONFLICT")
            # Finite reentrant windows only, not arbitrary subsequent concurrent
            # writes or a universal TOCTOU guarantee.
            output = _json({"state": "portable-integrity-verified", "portable": str(portable),
                            "import_authority": False, "checkpoint_acknowledged": False})
        except Exception as error:
            raise _operation_failure("export", error) from error
        click.echo(output)
