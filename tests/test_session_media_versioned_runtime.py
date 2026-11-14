"""Public CLI root T2 integration with installed Foundation ownership.

The controlled module loader is a deterministic test fixture, not an EP/provider
campaign. Core, PreparedBundle, transcript lifetime/writer/reader, media store,
checkpoint ACK and CLI SessionStore are real; no neighboring checkout imports or
serialized ownership substitutes are used. All namespaces are fresh and private.
"""

import asyncio
import base64
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
from importlib import import_module
from io import BytesIO, StringIO
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from rich.console import Console

from amplifier_core import AmplifierSession
from amplifier_core.message_models import ChatRequest, ImageBlock, Message
from amplifier_foundation.bundle import Bundle, PreparedBundle
from amplifier_foundation.session.media import MediaRefusal
# Freeze the bridge's real Core type before the controlled factory patch.
from amplifier_foundation.session.media_hook_bridge import MediaHookBridge
from amplifier_foundation.session.transcript_lifetime import (
    VersionedSessionBinding,
    VersionedSessionConflict,
)
from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_foundation.session.transcript_v2 import UnsupportedTranscript
from amplifier_foundation.session.versioned_checkpoint import (
    VersionedCheckpointReceipt,
    VersionedCheckpointRefused,
)

from amplifier_app_cli.session_hooks import seal_session_hook_graph
from amplifier_app_cli.session_runner import SessionConfig, create_initialized_session
from amplifier_app_cli.session_store import SessionStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "linux", reason="Prospective Foundation custody is Linux-only"),
]


class Context:
    def __init__(self):
        self.rows = []

    async def add_message(self, row):
        self.rows.append(deepcopy(row))

    async def get_messages(self):
        return deepcopy(self.rows)

    async def set_messages(self, rows):
        self.rows[:] = deepcopy(rows)

    async def set_system_prompt_factory(self, factory):
        self.factory = factory


class Provider:
    name = "fixture-versioned-provider"
    default_model = "fixture-vision"

    def __init__(self):
        self.observed = []

    def get_media_request_support(self, *, model, request_options):
        return {"v": 1, "positions": ["user", "tool"]}

    def observe(self, request):
        images = [
            base64.b64decode(block.source["data"])
            for message in request.messages if isinstance(message.content, list)
            for block in message.content if isinstance(block, ImageBlock)
        ]
        self.observed.append((request, images))

    async def request_budget(self, request, *, request_options=None):
        self.observe(request)
        return {
            "estimated_input_tokens": 10, "input_limit_tokens": 100,
            "context_token_budget": 100, "measurement": {
                "kind": "provider_count", "source": "fixture", "input_tokens": 10,
            },
        }

    async def complete(self, request):
        self.observe(request)
        return "fixture result"


class Loader:
    """Only discovery/vendor boundaries are synthetic, never ownership or I/O."""

    def __init__(self, execute=None):
        self.queue = []
        self.execute = execute

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            if module_id == "fixture-versioned-loop":
                async def execute(*args, **kwargs):
                    return "fixture done"
                await current.mount("orchestrator", SimpleNamespace(execute=self.execute or execute))
            elif module_id == "fixture-versioned-context":
                await current.mount("context", Context())
            elif module_id == "fixture-versioned-provider":
                await current.mount("providers", Provider(), name=module_id)
            else:
                raise AssertionError(f"Unexpected controlled module: {module_id}")
        return mount

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


def committed(directory):
    with open_committed_source(directory) as source:
        return source.commit


def files(directory):
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*") if path.is_file() and not path.is_symlink()
    }


def assert_held(binding):
    binding.check()
    with pytest.raises(VersionedSessionConflict, match="retains the source lease"):
        VersionedSessionBinding.resume(
            binding.directory, session_id=binding.session_id, expected=binding.expected,
        )


class Runtime:
    def __init__(self, tmp_path):
        self.parent = tmp_path / "sessions"
        self.parent.mkdir(mode=0o700)
        self.allocation_admissions = []
        self.asset_admissions = []
        self.pending = []
        self.created = []
        self.native_created = []
        self.initialized = None
        self.host = None
        self.execute = None

        def allocate_admit():
            assert stat.S_IMODE(self.parent.stat().st_mode) == 0o700
            assert not (self.parent / "root").exists()
            assert not list(self.parent.rglob("media"))
            assert self.created == self.native_created == []
            self.allocation_admissions.append("private-new-root-no-assets")

        self.binding = VersionedSessionBinding.create_new(
            self.parent / "root", session_id="root", operation_id="allocate-root",
            admit=allocate_admit,
        )
        self.bindings = [self.binding]
        self.plan = {
            "session": {"orchestrator": "fixture-versioned-loop", "context": "fixture-versioned-context"},
            "providers": [{"module": "fixture-versioned-provider"}],
        }

    def admit(self):
        # This is the caller's dynamic closure over the actual NEW root media
        # owner. No origin/ref is guessed by CLI storage or read from receipts.
        self.binding.check()
        assert self.host is not None and self.host.store.session_id == "root"
        assert self.host.native._canonical_binding is self.binding
        for ref, origin in self.pending:
            result = self.host.store.publish_sync([ref], origin=origin, disposition="retain")
            assert result["publication"]["durability"] == "assets-only"
        self.asset_admissions.append(tuple(ref["sha256"] for ref, _ in self.pending))

    def configuration(self, *, rows=None):
        prepared = PreparedBundle(deepcopy(self.plan), MagicMock(), Bundle(name="fixture-versioned"))
        return SessionConfig(
            config=deepcopy(self.plan), search_paths=[], verbose=False,
            session_id="root", prepared_bundle=prepared, bundle_name="fixture-versioned",
            shared_root=True, invocation_mode="single", initial_transcript=rows,
            canonical_binding=self.binding, canonical_admit=self.admit,
        )

    async def open(self, *, rows=None, setup_error=None, cleanup_error=None, media_cleanup_error=None):
        config = self.configuration(rows=rows)
        prepared = config.prepared_bundle
        create = prepared.create_session

        async def create_then_fail(*args, **kwargs):
            actual = await create(*args, **kwargs)
            if media_cleanup_error is not None:
                host = actual.coordinator.get_capability("cli.session_media")
                self.host = host
                self.physical_close = host.store.close
                self.physical_close_attempts = []

                def fail_physical_close():
                    assert_held(self.binding)
                    self.physical_close_attempts.append("physical-close")
                    raise media_cleanup_error

                stack.enter_context(patch.object(host.store, "close", new=fail_physical_close))
            raise setup_error

        def factory(*args, **kwargs):
            assert_held(self.binding)
            actual = AmplifierSession(*args, **kwargs)
            actual.coordinator.loader = Loader(self.execute)
            self.created.append(actual)
            if cleanup_error is not None:
                original_cleanup = actual.cleanup
                self.actual_cleanup = original_cleanup
                self.cleanup_attempts = []

                async def fail_cleanup(candidate):
                    assert candidate is actual
                    self.cleanup_attempts.append("attempt")
                    assert_held(self.binding)
                    raise cleanup_error
                # RustSession instance methods are read-only. This fault affects
                # only cleanup dispatch, never the real owner or coordinator.
                self.cleanup_patch = patch.object(AmplifierSession, "cleanup", new=fail_cleanup)
                self.cleanup_patch.start()
            return actual

        def native_factory(*args, **kwargs):
            assert kwargs["canonical_binding"] is self.binding
            assert_held(self.binding)
            native = SessionStore(*args, **kwargs)
            self.native_created.append(native)
            return native

        settings = MagicMock()
        settings.with_session.return_value.get_merged_settings.return_value = {"configurator": {}}
        with ExitStack() as stack:
            stack.enter_context(patch("amplifier_core.AmplifierSession", side_effect=factory))
            stack.enter_context(patch("amplifier_app_cli.session_runner.SessionStore", side_effect=native_factory))
            stack.enter_context(patch("amplifier_app_cli.commands.init.check_first_run", return_value=False))
            stack.enter_context(patch("amplifier_app_cli.session_runner.AppSettings", return_value=settings))
            stack.enter_context(patch("amplifier_app_cli.paths.create_foundation_resolver"))
            stack.enter_context(patch("amplifier_app_cli.runtime.config.inject_user_providers"))
            acquire = stack.enter_context(patch("amplifier_app_cli.session_handoff.acquire_root",
                                               side_effect=AssertionError("legacy writer must not acquire")))
            if setup_error is not None:
                stack.enter_context(patch.object(prepared, "create_session", side_effect=create_then_fail))
            result = await create_initialized_session(config, Console(file=StringIO()))
            acquire.assert_not_called()
        self.initialized = result
        self.host = result.session.coordinator.get_capability("cli.session_media")
        assert result.store is self.native_created[-1]
        assert result.store._canonical_binding is self.binding
        assert result.config.canonical_binding is self.binding
        assert result.root_state is result.config.root_state is None
        assert result.config.shared_root is False
        seal_session_hook_graph(result.session)
        bridge = result.session.coordinator.get_capability("session.media.tool_post")
        assert isinstance(bridge, MediaHookBridge)
        assert bridge.validate_receipt(bridge.graph_receipt)
        return result

    async def stage(self, image_format, *, session=None, policy=None):
        session = session if session is not None else self.initialized.session
        stream = BytesIO()
        Image.new("RGB", (3, 2), (29, 61, 103)).save(stream, image_format)
        payload = stream.getvalue()
        origin = {"kind": "tool", "session_id": "root", "id": "fixture-capture"}
        ref = (await self.host.capability.ingest(
            payload, source_format="image-bytes", origin=origin, policy=policy,
        ))["value"]
        bridge = session.coordinator.get_capability("session.media.tool_post")
        outcome = await bridge({"tool_name": "fixture-capture", "tool_call_id": origin["id"],
                                "result": {"refs": [ref]}})
        assert outcome["complete"] and outcome["result"]["refs"] == [ref]
        context = session.coordinator.get("context")
        await context.add_message({"role": "user", "content": "inspect generated observation", "metadata": {
            "experimental.media.v1": {"refs": [ref]},
        }})
        self.pending.append((ref, origin))
        return payload, ref

    async def observe(self, payload, *, session=None):
        session = session if session is not None else self.initialized.session
        native = session.coordinator.get_capability("cli.session_store")
        context = session.coordinator.get("context")
        before = await context.get_messages()
        saved = native.load("root")[0]
        assert saved == before
        request = ChatRequest(messages=[Message(**row) for row in before])
        canonical = request.model_dump()
        provider = session.coordinator.get("providers")["fixture-versioned-provider"]
        raw = next(raw for raw, wrapped in self.host.wrappers.values() if wrapped is provider)
        n = len(raw.observed)
        await provider.request_budget(request)
        assert await provider.complete(request) == "fixture result"
        counted, sent = raw.observed[n:]
        assert counted[0] is sent[0] and counted[0] is not request
        assert counted[1] == sent[1] == [payload]
        assert request.model_dump() == canonical
        assert await context.get_messages() == before
        assert native.load("root")[0] == saved
        return before


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    # Make any incidental CLI settings/path resolution private too.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    current = Runtime(tmp_path)
    yield current
    if hasattr(current, "cleanup_patch"):
        current.cleanup_patch.stop()
    for binding in current.bindings:
        binding.close()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
async def test_public_root_typed_v2_ack_cold_resume_private_pixels_and_idle_lease(runtime, image_format):
    current = runtime
    assert current.allocation_admissions == ["private-new-root-no-assets"]
    for name in ("transcript.jsonl", "transcript.jsonl.backup"):
        fence = current.binding.directory / name
        assert fence.is_dir() and list(fence.iterdir())
        with pytest.raises(IsADirectoryError):
            fence.open("wb")
    initial = current.binding.expected
    opened = await current.open()
    payload, ref = await current.stage(image_format)
    try:
        with pytest.raises(MediaRefusal):
            current.host.store.verify_checkpoint(ref)
        with pytest.raises(ValueError, match="explicit versioned"):
            opened.store.save("root", [], {})
        receipt = await opened.session.coordinator.get_capability("session.durable_checkpoint")()
        assert type(receipt) is VersionedCheckpointReceipt
        assert receipt is current.binding.last_receipt
        assert receipt.verify()["operation_id"] == current.binding.expected.operation_id
        assert current.binding.expected != initial
        assert current.asset_admissions == [(hashlib.sha256(payload).hexdigest(),)]
        with current.host.store.resolve_ref(ref) as image:
            assert image.read() == payload
        rows = await current.observe(payload)
        selected = current.binding.expected
        assert selected == committed(current.binding.directory)
        assert_held(current.binding)  # Same-process competitor refused while idle.
        first_host, first_session, first_binding = current.host, opened.session, current.binding
        first_bridge = first_session.coordinator.get_capability("session.media.tool_post")
        first_graph = first_bridge.graph_receipt
        closed = []
        actual_close = first_host.store.close

        def checked_store_close():
            assert_held(first_binding)
            actual_close()
            closed.append("media-closed-before-owner-release")

        with patch.object(first_host.store, "close", side_effect=checked_store_close):
            await opened.cleanup()
        assert closed == ["media-closed-before-owner-release"]
        assert first_host.closed
        with pytest.raises(VersionedSessionConflict, match="closed"):
            first_binding.check()
        # Append garbage to the actual body, without repairing/truncating it.
        # The reader must select the descriptor prefix, never physical EOF.
        body = first_binding.directory / "transcript.v2" / selected.body
        with body.open("ab") as stream:
            stream.write(b'{"role":"user","content":"uncommitted-tail"}\ninvalid\x00')
        before_resume = files(first_binding.directory / "transcript.v2")
        resumed_binding = VersionedSessionBinding.resume(first_binding.directory, session_id="root", expected=selected)
        current.bindings.append(resumed_binding)
        assert resumed_binding is not first_binding
        assert resumed_binding.load_messages() == rows
        current.binding = resumed_binding
        current.pending.clear()  # No ingest, source capture or publication replay.
        restored = await current.open(rows=resumed_binding.load_messages())
        try:
            assert restored.session is not first_session
            assert current.host is not first_host
            assert current.host.store is not first_host.store
            assert restored.store is not opened.store
            assert resumed_binding.last_receipt is not receipt
            assert type(resumed_binding.last_receipt) is VersionedCheckpointReceipt
            assert resumed_binding.last_receipt.verify()
            bridge = restored.session.coordinator.get_capability("session.media.tool_post")
            assert bridge.graph_receipt is not first_graph
            assert bridge.validate_receipt(bridge.graph_receipt)
            with current.host.store.resolve_ref(ref) as image:
                assert image.read() == payload
            assert await current.observe(payload) == rows
            assert current.asset_admissions == [(ref["sha256"],)]
            assert files(first_binding.directory / "transcript.v2") == before_resume
            assert resumed_binding.expected == selected
        finally:
            await restored.cleanup()
    finally:
        if not current.host.closed:
            await current.initialized.cleanup()


@pytest.mark.parametrize("primary_kind", ["error", "cancel"])
@pytest.mark.parametrize("cleanup_kind", [None, "error", "cancel"])
async def test_setup_failure_preserves_exact_cause_and_retains_real_owner_on_uncertain_cleanup(
    runtime, primary_kind, cleanup_kind,
):
    primary = OSError("synthetic setup") if primary_kind == "error" else asyncio.CancelledError("synthetic setup cancel")
    cleanup = (OSError("synthetic cleanup") if cleanup_kind == "error" else
               asyncio.CancelledError("synthetic cleanup cancel") if cleanup_kind else None)
    initial = runtime.binding.expected
    try:
        with pytest.raises(type(primary)) as failed:
            await runtime.open(setup_error=primary, cleanup_error=cleanup)
        assert failed.value is primary
        assert len(runtime.created) == len(runtime.native_created) == 1
        assert runtime.binding.expected == initial == committed(runtime.binding.directory)
        assert runtime.asset_admissions == []
        if cleanup is not None:
            assert runtime.cleanup_attempts == ["attempt"]  # Inner/outer handlers never replay.
            assert_held(runtime.binding)
            assert any("writer retained" in note for note in primary.__notes__)
            host = runtime.created[0].coordinator.get_capability("cli.session_media")
            assert not host.closed
        else:
            assert runtime.created[0].coordinator.get_capability("cli.session_media").closed
            fresh = VersionedSessionBinding.resume(runtime.binding.directory, session_id="root", expected=initial)
            runtime.bindings.append(fresh)
            fresh.check()
    finally:
        if cleanup is not None and runtime.created:
            # Explicit test teardown: the injected function NEVER ran the real
            # cleanup. Settle that real operation once, not the failed callback.
            await runtime.actual_cleanup()
            assert runtime.cleanup_attempts == ["attempt"]


@pytest.mark.parametrize("failure_kind", ["error", "cancel"])
async def test_initialized_cleanup_failure_keeps_actual_source_lease(runtime, failure_kind):
    error = OSError("cleanup uncertain") if failure_kind == "error" else asyncio.CancelledError("cleanup cancelled")
    opened = await runtime.open(cleanup_error=error)
    try:
        with pytest.raises(type(error)) as failed:
            await opened.cleanup()
        assert failed.value is error
        assert runtime.cleanup_attempts == ["attempt"]
        assert_held(runtime.binding)
        assert not runtime.host.closed
    finally:
        await runtime.actual_cleanup()
        assert runtime.host.closed
        runtime.binding.close()
    fresh = VersionedSessionBinding.resume(runtime.binding.directory, session_id="root", expected=runtime.binding.expected)
    runtime.bindings.append(fresh)
    fresh.check()


async def test_late_ack_failure_stops_checkpoint_then_dispatch_and_never_rewrites_again(runtime, monkeypatch):
    async def settled_dispatch(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        # Controlled orchestrator, but actual public Core execute and the real
        # installed CLI callback determine whether count/send is reachable.
        await coordinator.get_capability("session.durable_checkpoint")()
        request = ChatRequest(messages=[Message(**row) for row in await context.get_messages()])
        selected_provider = providers["fixture-versioned-provider"]
        await selected_provider.request_budget(request)
        await selected_provider.complete(request)
        return "must not dispatch after failed checkpoint"

    runtime.execute = settled_dispatch
    opened = await runtime.open()
    payload, ref = await runtime.stage("PNG")
    binding, media = runtime.binding, runtime.host.store
    initial = binding.expected
    error = OSError("late ACK physical-fence failure")
    actual_ack, actual_check, actual_write = media.acknowledge_checkpoint, binding.check, binding._writer.rewrite
    receipts, writes = [], []
    armed = False

    def acknowledge_then_arm(receipt):
        nonlocal armed
        result = actual_ack(receipt)
        receipts.append(receipt)
        armed = True
        return result

    def late_check():
        actual_check()
        if armed:
            raise error

    def write_once(*args, **kwargs):
        writes.append(kwargs["operation_id"])
        return actual_write(*args, **kwargs)

    provider = opened.session.coordinator.get("providers")["fixture-versioned-provider"]
    raw = next(raw for raw, wrapped in runtime.host.wrappers.values() if wrapped is provider)

    monkeypatch.setattr(binding._writer, "rewrite", write_once)
    try:
        with monkeypatch.context() as faults:
            faults.setattr(media, "acknowledge_checkpoint", acknowledge_then_arm)
            faults.setattr(binding, "check", late_check)
            # Actual Rust Core execute reports the callback's refusal as its
            # RuntimeError envelope; do not claim callback-type propagation.
            with pytest.raises(RuntimeError, match="VersionedCheckpointRefused"):
                await opened.session.execute("inspect generated observation")
        assert len(receipts) == len(writes) == 1
        assert raw.observed == []
        with pytest.raises(VersionedCheckpointRefused):
            receipts[0].verify()
        actual = committed(binding.directory)
        assert actual != initial and actual.operation_id == writes[0]
        assert binding.expected == initial and binding.last_receipt is None
        before = files(binding.directory)
        with pytest.raises(RuntimeError, match="VersionedSessionConflict.*reconciliation"):
            await opened.session.execute("inspect generated observation")
        assert files(binding.directory) == before
        assert len(writes) == 1
        assert len(runtime.asset_admissions) == 1 and raw.observed == []
        assert ref["sha256"] == hashlib.sha256(payload).hexdigest()
    finally:
        await opened.cleanup()


@pytest.mark.parametrize("invalid", ["root-replaced", "initial-prefix-rows"])
async def test_invalid_source_refuses_public_root_before_any_provider_construction(runtime, invalid):
    binding = runtime.binding
    if invalid == "root-replaced":
        # Preserve the original and its physical handles, never delete/reset it.
        retained = binding.directory.with_name("retained-original-root")
        binding.directory.rename(retained)
        binding.directory.mkdir(mode=0o700)
        expected_error = VersionedSessionConflict
        rows = None
    else:
        expected_error = ValueError
        rows = [{"role": "user", "content": "not the selected committed prefix"}]
    before = files(runtime.parent)
    with pytest.raises(expected_error):
        await runtime.open(rows=rows)
    assert runtime.created == runtime.native_created == []
    assert runtime.asset_admissions == []
    assert files(runtime.parent) == before


async def test_resume_exact_prefix_selector_not_serialized_data_and_wrong_commit(runtime):
    binding = runtime.binding
    selected = binding.expected
    binding.close()
    before = files(binding.directory)
    for selector in (asdict(selected), True, replace(selected, sequence=selected.sequence + 1)):
        with pytest.raises((VersionedSessionConflict, UnsupportedTranscript)):
            VersionedSessionBinding.resume(binding.directory, session_id="root", expected=selector)
    assert runtime.created == runtime.native_created == []
    assert files(binding.directory) == before


@pytest.mark.parametrize("impostor", [True, {"enabled": True}, {"kind": "versioned-checkpoint"}])
async def test_private_sessionconfig_binding_requires_live_identity_not_json_or_flags(runtime, impostor):
    config = runtime.configuration()
    config.canonical_binding = impostor
    before = files(runtime.parent)
    with patch("amplifier_app_cli.commands.init.check_first_run", return_value=False):
        with pytest.raises(ValueError, match="real session-bound"):
            await create_initialized_session(config, Console(file=StringIO()))
    assert files(runtime.parent) == before
    assert_held(runtime.binding)


@contextmanager
def public_entry_environment(current, *, cleanup_error=None):
    """Control discovery/presentation only; both public callers create real roots.

    Observers wrap actual storage and cleanup. No create_initialized_session or
    InitializedSession double is installed, and no provider/TTY is contacted.
    """
    main = import_module("amplifier_app_cli.main")

    observations = SimpleNamespace(
        output=StringIO(), saves=[], cleanup=[], store_closes=[], tty_closes=[],
    )
    actual_cleanup = AmplifierSession.cleanup
    actual_save = SessionStore.save_versioned

    def factory(*args, **kwargs):
        assert_held(current.binding)
        actual = AmplifierSession(*args, **kwargs)
        actual.coordinator.loader = Loader(current.execute)
        current.created.append(actual)
        return actual

    def native_factory(*args, **kwargs):
        assert kwargs["canonical_binding"] is current.binding
        assert_held(current.binding)
        native = SessionStore(*args, **kwargs)
        current.native_created.append(native)
        return native

    def save(native, session_id, rows, metadata, **options):
        assert native is current.native_created[-1]
        assert native._canonical_binding is current.binding
        assert options["admit"] is current.admit_callback
        assert options["expected"] == current.binding.expected
        assert isinstance(options["operation_id"], str) and options["operation_id"]
        receipt = actual_save(native, session_id, rows, metadata, **options)
        assert type(receipt) is VersionedCheckpointReceipt
        assert receipt is current.binding.last_receipt
        assert receipt.verify()
        observations.saves.append((native, deepcopy(rows), deepcopy(metadata), receipt))
        return receipt

    async def cleanup(candidate):
        assert candidate is current.created[-1]
        assert_held(current.binding)
        observations.cleanup.append("attempt")
        if cleanup_error is not None:
            raise cleanup_error
        host = candidate.coordinator.get_capability("cli.session_media")
        close = host.store.close

        def store_close():
            assert_held(current.binding)
            close()
            observations.store_closes.append("media-closed-owner-still-held")

        with patch.object(host.store, "close", new=store_close):
            await actual_cleanup(candidate)
        assert host.closed
        assert_held(current.binding)
        observations.cleanup.append("settled-before-owner-release")

    # Retain the exact bound method object passed through both public wrappers,
    # not merely equivalent serialized config or another callback.
    current.admit_callback = current.admit
    settings = MagicMock()
    settings.with_session.return_value.get_merged_settings.return_value = {"configurator": {}}
    with ExitStack() as stack:
        stack.enter_context(patch("amplifier_core.AmplifierSession", side_effect=factory))
        stack.enter_context(patch.object(AmplifierSession, "cleanup", new=cleanup))
        stack.enter_context(patch("amplifier_app_cli.session_runner.SessionStore", side_effect=native_factory))
        stack.enter_context(patch("amplifier_app_cli.commands.init.check_first_run", return_value=False))
        stack.enter_context(patch("amplifier_app_cli.session_runner.AppSettings", return_value=settings))
        stack.enter_context(patch("amplifier_app_cli.paths.create_foundation_resolver"))
        stack.enter_context(patch("amplifier_app_cli.runtime.config.inject_user_providers"))
        stack.enter_context(patch.object(SessionStore, "save_versioned", new=save))
        legacy = stack.enter_context(patch.object(SessionStore, "save", side_effect=AssertionError("legacy save")))
        acquire = stack.enter_context(patch("amplifier_app_cli.session_handoff.acquire_root",
                                           side_effect=AssertionError("legacy acquisition")))
        fallback = stack.enter_context(patch.object(main, "SessionStore",
                                                    side_effect=AssertionError("unbound fallback store")))
        stack.enter_context(patch.object(main, "console", Console(file=observations.output)))
        prompt = stack.enter_context(patch.object(main, "_create_prompt_session",
                                                 side_effect=AssertionError("must fail before TTY")))
        stack.enter_context(patch.object(main, "close_dedicated_tty_input",
                                        side_effect=lambda: observations.tty_closes.append("closed")))
        try:
            yield observations
        finally:
            legacy.assert_not_called()
            acquire.assert_not_called()
            fallback.assert_not_called()
            prompt.assert_not_called()
            # A synthetic cleanup fault never entered this actual operation.
            observations.actual_cleanup = actual_cleanup


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
async def test_execute_single_public_finite_media_turn_final_persist_and_release(runtime, image_format):
    main = import_module("amplifier_app_cli.main")

    observed = {}

    async def execute(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        actual = runtime.created[-1]
        runtime.host = coordinator.get_capability("cli.session_media")
        assert runtime.host.native is runtime.native_created[-1]
        assert runtime.host.native._canonical_binding is runtime.binding
        bridge = coordinator.get_capability("session.media.tool_post")
        assert bridge.validate_receipt(bridge.graph_receipt)
        assert await context.get_messages() == []
        assert prompt == "inspect generated observation"
        payload, ref = await runtime.stage(image_format, session=actual)
        receipt = await coordinator.get_capability("session.durable_checkpoint")()
        assert type(receipt) is VersionedCheckpointReceipt
        assert receipt is runtime.binding.last_receipt
        with runtime.host.store.resolve_ref(ref) as image:
            assert image.read() == payload
        user_rows = await runtime.observe(payload, session=actual)
        response = "finite synthetic observation accepted"
        await context.add_message({"role": "assistant", "content": response})
        observed.update(payload=payload, ref=ref, checkpoint=receipt,
                        before_final=runtime.binding.expected, user_rows=user_rows,
                        canonical=await context.get_messages(), response=response)
        return response

    runtime.execute = execute
    prepared = runtime.configuration().prepared_bundle
    initial = runtime.binding.expected
    with public_entry_environment(runtime) as evidence:
        result = await main.execute_single(
            "inspect generated observation", deepcopy(runtime.plan), [], False,
            session_id="root", bundle_name="fixture-versioned", prepared_bundle=prepared,
            canonical_binding=runtime.binding, canonical_admit=runtime.admit_callback,
        )
    assert result is None  # Public function renders the actual finite response.
    assert observed["response"] in evidence.output.getvalue()
    assert len(runtime.created) == len(runtime.native_created) == 1
    assert len(evidence.saves) == 2  # Loop checkpoint + public final persistence.
    native, final_rows, metadata, final_receipt = evidence.saves[-1]
    assert final_rows == observed["canonical"]
    assert final_receipt is runtime.binding.last_receipt
    assert final_receipt is not observed["checkpoint"]
    selected = runtime.binding.expected
    assert selected != observed["before_final"] != initial
    assert selected == committed(runtime.binding.directory)
    assert selected.message_count == 2
    assert metadata["session_id"] == "root"
    assert metadata["turn_count"] == 1 and metadata["bundle"] == "fixture-versioned"
    assert metadata["working_dir"] == str(Path.cwd().resolve())
    assert evidence.cleanup == ["attempt", "settled-before-owner-release"]
    assert evidence.store_closes == ["media-closed-owner-still-held"]
    assert evidence.tty_closes == ["closed"]
    assert runtime.host.closed and native._media_stores == {}
    with pytest.raises(VersionedSessionConflict, match="closed"):
        runtime.binding.check()
    # Inspect only the real selected source; no alternate HOME session was used.
    reader = SessionStore(runtime.parent)
    saved, saved_metadata = reader.load("root")
    assert saved == final_rows
    assert saved_metadata["turn_count"] == 1
    assert json.loads((runtime.binding.directory / "metadata.json").read_text()) == saved_metadata
    transcript_files = files(runtime.binding.directory / "transcript.v2")
    metadata_bytes = (runtime.binding.directory / "metadata.json").read_bytes()
    encoded = base64.b64encode(observed["payload"])
    assert all(observed["payload"] not in data and encoded not in data
               for data in (*transcript_files.values(), metadata_bytes))
    assert all(b"canonical_binding" not in data and b"canonical_admit" not in data
               for data in (*transcript_files.values(), metadata_bytes))
    assert not list((Path.home() / ".amplifier").rglob("transcript.v2"))
    with open_committed_source(runtime.binding.directory, expected=selected) as source:
        assert source.commit == selected
    fresh = VersionedSessionBinding.resume(runtime.binding.directory, session_id="root", expected=selected)
    runtime.bindings.append(fresh)
    assert fresh.load_messages() == final_rows
    assert files(runtime.binding.directory / "transcript.v2") == transcript_files


@pytest.mark.parametrize("cleanup_fault", [False, True])
async def test_interactive_public_private_binding_seal_failure_before_prompt(runtime, cleanup_fault, monkeypatch):
    main = import_module("amplifier_app_cli.main")
    from amplifier_app_cli import session_hooks

    primary = ValueError("synthetic final hook-graph seal refusal")
    secondary = OSError("synthetic unsettled media cleanup") if cleanup_fault else None
    calls = []
    initial = runtime.binding.expected
    before = files(runtime.binding.directory / "transcript.v2")

    def refuse_seal(actual):
        assert actual is runtime.created[-1]
        host = actual.coordinator.get_capability("cli.session_media")
        runtime.host = host
        assert host.native is runtime.native_created[-1]
        assert host.native._canonical_binding is runtime.binding
        assert callable(actual.coordinator.get_capability("cli.canonical_checkpoint_save"))
        assert callable(actual.coordinator.get_capability("session.durable_checkpoint"))
        assert actual.coordinator.get("context") is not None
        assert_held(runtime.binding)
        calls.append("final-host-seal")
        raise primary

    monkeypatch.setattr(session_hooks, "seal_session_hook_graph", refuse_seal)
    prepared = runtime.configuration().prepared_bundle
    with public_entry_environment(runtime, cleanup_error=secondary) as evidence:
        with pytest.raises(ValueError) as failure:
            await main.interactive_chat(
                deepcopy(runtime.plan), [], False, session_id="root",
                bundle_name="fixture-versioned", prepared_bundle=prepared,
                canonical_binding=runtime.binding, canonical_admit=runtime.admit_callback,
            )
        assert failure.value is primary
        assert calls == ["final-host-seal"]
        assert len(runtime.created) == len(runtime.native_created) == 1
        assert evidence.saves == [] and runtime.asset_admissions == []
        assert evidence.tty_closes == ["closed"]
        assert runtime.binding.expected == initial == committed(runtime.binding.directory)
        assert files(runtime.binding.directory / "transcript.v2") == before
        if cleanup_fault:
            assert evidence.cleanup == ["attempt"]
            assert evidence.store_closes == [] and not runtime.host.closed
            assert_held(runtime.binding)
            assert any("writer retained" in note for note in primary.__notes__)
        else:
            assert evidence.cleanup == ["attempt", "settled-before-owner-release"]
            assert evidence.store_closes == ["media-closed-owner-still-held"]
            assert runtime.host.closed
            with pytest.raises(VersionedSessionConflict, match="closed"):
                runtime.binding.check()
    if cleanup_fault:
        # Explicit fixture recovery of the real operation, never replay the
        # synthetic failing callback or restart the interactive public caller.
        await evidence.actual_cleanup(runtime.created[-1])
        assert evidence.cleanup == ["attempt"]
        assert runtime.host.closed
        runtime.binding.close()
    fresh = VersionedSessionBinding.resume(runtime.binding.directory, session_id="root", expected=initial)
    runtime.bindings.append(fresh)
    assert fresh.load_messages() == []


def contains_exception(error, original):
    """Exception groups retain the actual ordinary store failure, not its text."""
    if error is original:
        return True
    return isinstance(error, BaseExceptionGroup) and any(
        contains_exception(item, original) for item in error.exceptions
    )


@pytest.mark.parametrize("kind", ["error", "cancel"])
async def test_actual_core_registered_callback_failure_control_returns_normally(runtime, kind, caplog):
    opened = await runtime.open()
    original = OSError("callback-control-fixed-sentinel") if kind == "error" else asyncio.CancelledError(
        "callback-control-fixed-sentinel"
    )
    attempts = []

    async def failing_registered_callback():
        attempts.append("callback")
        raise original

    # This control intentionally exercises Core's native callback policy. The
    # fixed sentinel contains no pixel/provider data; do not infer log privacy
    # or exception propagation from Core returning normally.
    opened.session.coordinator.register_cleanup(failing_registered_callback)
    try:
        assert await opened.session.cleanup() == ()  # Actual Rust Core unit result.
        assert attempts == ["callback"]
        assert "callback-control-fixed-sentinel" in caplog.text
        assert runtime.host.cleanup_settled is True
        assert runtime.host.cleanup_error is None
        assert_held(runtime.binding)  # Core alone cannot release the CLI owner.
    finally:
        runtime.binding.close()


@pytest.mark.parametrize("kind", ["error", "cancel"])
async def test_initialized_actual_media_callback_failure_retains_owner_and_never_replays(runtime, kind, monkeypatch, caplog):
    opened = await runtime.open()
    host = runtime.host
    physical_close = host.store.close
    original = OSError("media-close-fixed-sentinel") if kind == "error" else asyncio.CancelledError(
        "media-close-fixed-sentinel"
    )
    attempts = []

    def fail_before_physical_close():
        assert_held(runtime.binding)
        attempts.append("physical-close")
        raise original

    monkeypatch.setattr(host.store, "close", fail_before_physical_close)
    try:
        with pytest.raises(BaseException) as failed:
            await opened.cleanup()
        retained = failed.value
        assert contains_exception(retained, original)
        assert retained is host.cleanup_error is opened._cleanup_error
        assert host.cleanup_attempted is True and host.cleanup_settled is False
        assert host.closed is True  # Early latch is explicitly NOT settlement.
        assert attempts == ["physical-close"]
        assert_held(runtime.binding)
        # Actual Core reports normal completion despite the retained failure.
        assert await opened.session.cleanup() == ()
        with pytest.raises(BaseException) as second:
            await opened.cleanup()
        assert second.value is retained
        assert await host.close() is None
        assert attempts == ["physical-close"]  # No callback or physical replay.
        assert_held(runtime.binding)
        assert "media-close-fixed-sentinel" not in caplog.text
    finally:
        # Explicit fixture-only settlement of the never-entered physical close;
        # do not reset the host witness or call its failing operation again.
        physical_close()
        runtime.binding.close()


@pytest.mark.parametrize("kind", ["error", "cancel"])
async def test_setup_failure_with_actual_media_callback_fault_preserves_primary_and_owner(runtime, kind, caplog):
    primary = OSError("setup-primary-fixed-sentinel")
    original = OSError("media-close-fixed-sentinel") if kind == "error" else asyncio.CancelledError(
        "media-close-fixed-sentinel"
    )
    selected = runtime.binding.expected
    before = files(runtime.binding.directory / "transcript.v2")
    try:
        with pytest.raises(OSError) as failed:
            await runtime.open(setup_error=primary, media_cleanup_error=original)
        assert failed.value is primary
        assert any("writer retained" in note for note in primary.__notes__)
        host = runtime.host
        assert host.cleanup_attempted is True and host.cleanup_settled is False
        assert host.closed is True
        assert contains_exception(host.cleanup_error, original)
        assert runtime.physical_close_attempts == ["physical-close"]
        assert len(runtime.created) == len(runtime.native_created) == 1
        assert_held(runtime.binding)
        assert await runtime.created[0].cleanup() == ()  # Actual Core, no outer patch.
        assert await host.close() is None
        assert runtime.physical_close_attempts == ["physical-close"]
        assert runtime.binding.expected == selected == committed(runtime.binding.directory)
        assert files(runtime.binding.directory / "transcript.v2") == before
        assert_held(runtime.binding)
        # The CLI callback contains private exceptions instead of handing them
        # to Core's swallowing/logging sink. Preserve fixed custody diagnostics.
        assert "media-close-fixed-sentinel" not in caplog.text
        assert "writer retained" in caplog.text
    finally:
        if hasattr(runtime, "physical_close"):
            runtime.physical_close()
        runtime.binding.close()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
async def test_cold_resume_omitted_rows_restores_exact_committed_media_before_constructor(runtime, image_format):
    opened = await runtime.open()
    assert opened.config.initial_transcript is None and opened.config.is_resume is False
    payload, ref = await runtime.stage(image_format)
    first_receipt = await opened.session.coordinator.get_capability("session.durable_checkpoint")()
    rows = await runtime.observe(payload)
    selected = runtime.binding.expected
    first_host, first_session = runtime.host, opened.session
    await opened.cleanup()
    fresh = VersionedSessionBinding.resume(runtime.binding.directory, session_id="root", expected=selected)
    runtime.bindings.append(fresh)
    runtime.binding = fresh
    runtime.pending.clear()
    admissions_before = deepcopy(runtime.asset_admissions)
    source_before = files(fresh.directory / "transcript.v2")
    constructors = []
    actual_constructor = AmplifierSession

    def constructor_observer(*args, **kwargs):
        # Runtime.open's factory delegates here indirectly only via the saved
        # real constructor below; Foundation supplies is_resumed to actual Core.
        constructors.append(kwargs.get("is_resumed"))
        return actual_constructor(*args, **kwargs)

    # Observe public PreparedBundle's actual constructor kwargs via the existing
    # controlled factory, without replacing CLI initialization or ownership.
    with patch.object(sys.modules[__name__], "AmplifierSession", new=constructor_observer):
        restored = await runtime.open()  # No initial_transcript supplied.
    try:
        assert constructors == [True]
        assert restored.config.is_resume is True
        assert restored.config.initial_transcript == rows
        assert restored.session is not first_session
        assert runtime.host is not first_host and runtime.host.store is not first_host.store
        assert await restored.session.coordinator.get("context").get_messages() == rows
        assert restored.store.load("root")[0] == rows == fresh.load_messages()
        assert type(fresh.last_receipt) is VersionedCheckpointReceipt
        assert fresh.last_receipt is not first_receipt
        assert fresh.last_receipt.verify()
        assert fresh.expected == selected
        assert await runtime.observe(payload) == rows
        with runtime.host.store.resolve_ref(ref) as image:
            assert image.read() == payload
        assert runtime.asset_admissions == admissions_before
        assert runtime.pending == []
        assert files(fresh.directory / "transcript.v2") == source_before
        # A subsequent real ACK/save must preserve restored history, never [].
        next_receipt = await restored.session.coordinator.get_capability("session.durable_checkpoint")()
        assert type(next_receipt) is VersionedCheckpointReceipt
        assert restored.store.load("root")[0] == rows
        assert fresh.expected.message_count == selected.message_count == len(rows)
    finally:
        await restored.cleanup()


@pytest.mark.parametrize("ack_kind", ["error", "cancel"])
@pytest.mark.parametrize("close_kind", [None, "error", "cancel"])
async def test_cold_resume_ack_failure_preserves_cause_and_single_witnessed_cleanup(
    runtime, ack_kind, close_kind, monkeypatch, caplog,
):
    from amplifier_app_cli.session_media import cleanup_session_media_owner

    opened = await runtime.open()
    await runtime.stage("PNG")
    await opened.session.coordinator.get_capability("session.durable_checkpoint")()
    rows = opened.store.load("root")[0]
    selected = runtime.binding.expected
    await opened.cleanup()
    fresh = VersionedSessionBinding.resume(runtime.binding.directory, session_id="root", expected=selected)
    runtime.bindings.append(fresh)
    runtime.binding = fresh
    runtime.pending.clear()
    initial_source = files(fresh.directory / "transcript.v2")
    admissions_before = deepcopy(runtime.asset_admissions)
    primary = OSError("restored-ack-fixed-sentinel") if ack_kind == "error" else asyncio.CancelledError(
        "restored-ack-fixed-sentinel"
    )
    secondary = (OSError("restored-close-fixed-sentinel") if close_kind == "error" else
                 asyncio.CancelledError("restored-close-fixed-sentinel") if close_kind else None)
    attempts, ack_calls, physical = [], [], []
    hosts = []

    def fail_ack(native, session_id, *, expected):
        actual = runtime.created[-1]
        host = actual.coordinator.get_capability("cli.session_media")
        hosts.append(host)
        assert native is host.native is runtime.native_created[-1]
        assert native._canonical_binding is fresh
        assert session_id == "root" and expected == selected
        assert native.load("root")[0] == rows
        assert actual.coordinator.get("context") is None  # Before Core initialize.
        assert_held(fresh)
        actual_close = host.store.close
        physical.append(actual_close)

        def close_once():
            attempts.append("physical-close")
            assert_held(fresh)
            if secondary is not None:
                raise secondary
            actual_close()

        monkeypatch.setattr(host.store, "close", close_once)
        ack_calls.append("restored-ACK")
        raise primary

    monkeypatch.setattr(SessionStore, "acknowledge_versioned", fail_ack)
    try:
        with pytest.raises(type(primary)) as failed:
            await runtime.open(rows=rows)
        assert failed.value is primary
        assert ack_calls == ["restored-ACK"] and attempts == ["physical-close"]
        assert len(hosts) == 1
        host = hosts[0]
        assert host.cleanup_attempted is True and host.closed is True
        assert host.cleanup_settled is (secondary is None)
        assert fresh.expected == selected == committed(fresh.directory)
        assert files(fresh.directory / "transcript.v2") == initial_source
        assert runtime.asset_admissions == admissions_before and runtime.pending == []
        # Core's registered callback has already run; invoking actual Core
        # again must never replay the physical close or mask the original ACK.
        assert await runtime.created[-1].cleanup() == ()
        assert attempts == ["physical-close"]
        if secondary is not None:
            assert contains_exception(host.cleanup_error, secondary)
            assert any("writer retained" in note for note in primary.__notes__)
            assert_held(fresh)
            with pytest.raises(BaseException) as retained:
                await cleanup_session_media_owner(runtime.created[-1])
            assert retained.value is host.cleanup_error
            assert attempts == ["physical-close"]
            assert_held(fresh)
        else:
            assert host.cleanup_error is None
            with pytest.raises(VersionedSessionConflict, match="closed"):
                fresh.check()
            reacquired = VersionedSessionBinding.resume(fresh.directory, session_id="root", expected=selected)
            runtime.bindings.append(reacquired)
            assert reacquired.load_messages() == rows
        cli_records = [record for record in caplog.records if record.name.startswith("amplifier_app_cli")]
        assert all("restored-ack-fixed-sentinel" not in record.getMessage()
                   and "restored-close-fixed-sentinel" not in record.getMessage()
                   and record.exc_info is None for record in cli_records)
        assert "restored-close-fixed-sentinel" not in caplog.text
    finally:
        if secondary is not None and physical:
            # Explicit fixture settlement only: close never entered its actual
            # implementation, and neither callback/witness nor ACK is reset.
            physical[0]()
        fresh.close()


def assert_public_graph_and_owner(current):
    actual = current.initialized.session
    bridge = actual.coordinator.get_capability("session.media.tool_post")
    assert isinstance(bridge, MediaHookBridge)
    assert bridge.validate_receipt(bridge.graph_receipt)
    assert actual.coordinator.get_capability("cli.session_store") is current.initialized.store
    assert current.initialized.store._canonical_binding is current.binding
    assert current.host.native is current.initialized.store
    assert_held(current.binding)


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
async def test_actual_checkpoint_request_only_publication_refuses_before_commit(runtime, image_format, monkeypatch, caplog):
    async def checkpoint_then_dispatch(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        await coordinator.get_capability("session.durable_checkpoint")()
        request = ChatRequest(messages=[Message(**row) for row in await context.get_messages()])
        provider = providers["fixture-versioned-provider"]
        await provider.request_budget(request)
        await provider.complete(request)
        return "unreachable"

    runtime.execute = checkpoint_then_dispatch
    opened = await runtime.open()
    assert_public_graph_and_owner(runtime)
    payload, ref = await runtime.stage(image_format, policy={"retention": "request-only"})
    origin = runtime.pending[0][1]
    publication = runtime.host.store.publish_sync([ref], origin=origin, disposition="retain")
    assert publication["refs"] == [ref]
    assert publication["publication"]["durability"] == "assets-only"
    assert runtime.host.store.validate_published_ref(ref) == ref
    with runtime.host.store.resolve_ref(ref, committed=False) as image:
        assert image.read() == payload
    rows = await opened.session.coordinator.get("context").get_messages()
    selected = runtime.binding.expected
    descriptor = runtime.binding.directory / "transcript.v2" / "current.json"
    before = descriptor.read_bytes()
    acknowledgements = []
    actual_ack = runtime.host.store.acknowledge_checkpoint

    def observe_ack(receipt):
        acknowledgements.append(receipt)
        return actual_ack(receipt)

    monkeypatch.setattr(runtime.host.store, "acknowledge_checkpoint", observe_ack)
    try:
        with pytest.raises(RuntimeError, match="VersionedSessionConflict.*durable publication"):
            await opened.session.execute("request-only checkpoint must refuse")
        assert descriptor.read_bytes() == before
        assert committed(runtime.binding.directory) == runtime.binding.expected == selected
        assert runtime.binding.last_receipt is None
        assert acknowledgements == []  # Refusal is precommit, not failed late ACK.
        assert opened.store.versioned_publication_failure is None
        assert opened.store.load("root")[0] == []
        assert await opened.session.coordinator.get("context").get_messages() == rows
        assert runtime.asset_admissions == [(ref["sha256"],)]
        assert all(raw.observed == [] for raw, _ in runtime.host.wrappers.values())
        assert_public_graph_and_owner(runtime)
        assert base64.b64encode(payload).decode() not in caplog.text
        assert all(record.exc_info is None for record in caplog.records
                   if record.name.startswith("amplifier_app_cli"))
    finally:
        await opened.cleanup()


@pytest.mark.parametrize("metadata", [
    pytest.param(False, id="false-metadata"),
    pytest.param([], id="array-metadata"),
    pytest.param({"experimental.media.v1": None}, id="null-media-namespace"),
    pytest.param({"experimental.media.v1": {}}, id="missing-refs"),
    pytest.param(None, id="null-metadata-allowed"),
])
async def test_actual_checkpoint_strict_envelope_precommit_and_null_metadata_allowed(runtime, metadata, monkeypatch):
    receipts = []

    async def checkpoint(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        receipts.append(await coordinator.get_capability("session.durable_checkpoint")())
        return "checkpoint accepted"

    runtime.execute = checkpoint
    opened = await runtime.open()
    assert_public_graph_and_owner(runtime)
    row = {"role": "user", "content": "strict envelope observation", "metadata": deepcopy(metadata)}
    await opened.session.coordinator.get("context").set_messages([row])
    selected = runtime.binding.expected
    descriptor = runtime.binding.directory / "transcript.v2" / "current.json"
    before = descriptor.read_bytes()
    acknowledgements = []
    actual_ack = runtime.host.store.acknowledge_checkpoint

    def observe_ack(receipt):
        acknowledgements.append(receipt)
        return actual_ack(receipt)

    monkeypatch.setattr(runtime.host.store, "acknowledge_checkpoint", observe_ack)
    try:
        if metadata is None:
            assert await opened.session.execute("ordinary null metadata is allowed") == "checkpoint accepted"
            receipt, = receipts
            assert type(receipt) is VersionedCheckpointReceipt
            assert receipt is runtime.binding.last_receipt
            assert acknowledgements == [receipt] and receipt.verify()
            assert runtime.binding.expected != selected
            # Foundation's existing sanitize_message omits null optional fields
            # on disk. The caller's live canonical None must remain untouched.
            assert opened.store.load("root")[0] == [{"role": "user", "content": row["content"]}]
        else:
            refusal = ("ValueError: Invalid canonical media metadata carrier"
                       if isinstance(metadata, dict) else "VersionedSessionConflict")
            with pytest.raises(RuntimeError, match=refusal):
                await opened.session.execute("strict malformed envelope must refuse")
            assert descriptor.read_bytes() == before
            assert committed(runtime.binding.directory) == runtime.binding.expected == selected
            assert acknowledgements == [] and runtime.binding.last_receipt is None
            assert receipts == []
            assert opened.store.versioned_publication_failure is None
            assert opened.store.load("root")[0] == []
        assert await opened.session.coordinator.get("context").get_messages() == [row]
        assert all(raw.observed == [] for raw, _ in runtime.host.wrappers.values())
        assert_public_graph_and_owner(runtime)
    finally:
        await opened.cleanup()


async def test_public_binding_factory_refuses_existing_nonprivate_parent_before_staging_or_cli(tmp_path):
    parent = tmp_path / "existing-sessions"
    parent.mkdir(mode=0o755)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    untouched = parent.stat()
    admissions = []
    # Parent validation necessarily opens ancestor directory FDs. It must not
    # stage a session namespace or construct a native/shared-root/media owner.
    with (
        patch("amplifier_app_cli.session_runner.SessionStore", side_effect=AssertionError("native construction")) as native,
        patch("amplifier_app_cli.session_handoff.acquire_root", side_effect=AssertionError("shared acquisition")) as shared,
        patch("amplifier_app_cli.session_media.CLISessionMedia", side_effect=AssertionError("media construction")) as media,
        patch("amplifier_foundation.session.transcript_writer.TranscriptWriter.initialize",
              side_effect=AssertionError("writer initialization")) as writer,
    ):
        with pytest.raises(VersionedSessionConflict, match="parent must be private and owned"):
            VersionedSessionBinding.create_new(
                parent / "root", session_id="root", operation_id="private-parent-required",
                admit=lambda: admissions.append("must-not-admit"),
            )
        native.assert_not_called()
        shared.assert_not_called()
        media.assert_not_called()
        writer.assert_not_called()
    assert admissions == [] and list(parent.iterdir()) == []
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert parent.stat().st_ino == untouched.st_ino
    assert parent.stat().st_mtime_ns == untouched.st_mtime_ns
