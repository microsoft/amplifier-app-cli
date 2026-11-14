"""Private CLI child ownership against installed Core/Foundation, offline only.

The loader supplies controlled context/orchestrator/provider fixtures; Core,
participating graph, media admission, transcript lifetime, ACK and provider
preparation remain actual installed implementations. No HOME/network/modules
are discovered, and no neighboring repository is imported.
"""

import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from amplifier_core import AmplifierSession
from amplifier_core.message_models import ChatRequest, ImageBlock, Message
from amplifier_foundation.session.media import MEDIA_NAMESPACE
from amplifier_foundation.session.transcript_lifetime import (
    VersionedSessionBinding, VersionedSessionConflict,
)
from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt

from amplifier_app_cli import session_spawner as spawner
from amplifier_app_cli.session_checkpoint import install_session_checkpoint
from amplifier_app_cli.session_hooks import install_session_hook_graph
from amplifier_app_cli.session_media import CLISessionMedia, install_session_media
from amplifier_app_cli.session_store import SessionStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "linux", reason="Versioned lifetime custody is Linux-only"),
]
PLAN = {
    "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
    "providers": [{"module": "fixture-provider"}],
}


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
    name, default_model = "fixture-provider", "fixture-vision"

    def __init__(self):
        self.observed = []

    def get_media_request_support(self, *, model, request_options):
        return {"v": 1, "positions": ["user", "tool"]}

    def observe(self, request):
        pixels = [
            hashlib.sha256(base64.b64decode(block.source["data"])).hexdigest()
            for message in request.messages if isinstance(message.content, list)
            for block in message.content if isinstance(block, ImageBlock)
        ]
        self.observed.append((request, pixels))

    async def request_budget(self, request, *, request_options=None):
        self.observe(request)
        return {"estimated_input_tokens": 4, "input_limit_tokens": 100,
                "context_token_budget": 100, "measurement": {
                    "kind": "provider_count", "source": "fixture", "input_tokens": 4}}

    async def complete(self, request):
        self.observe(request)
        return "offline provider result"


class Loader:
    def __init__(self, execute):
        self.execute, self.queue = execute, []

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            if module_id == "fixture-loop":
                await current.mount("orchestrator", SimpleNamespace(execute=self.execute))
            elif module_id == "fixture-context":
                await current.mount("context", Context())
            elif module_id == "fixture-provider":
                await current.mount("providers", Provider(), name="fixture-provider")
            else:
                raise AssertionError(f"Unexpected offline module: {module_id}")
        return mount

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def selected_prefix(directory):
    with open_committed_source(directory) as source:
        return source.commit


def image_bytes(image_format):
    data = io.BytesIO()
    Image.new("RGB", (3, 2), (21, 48, 90)).save(data, image_format)
    return data.getvalue()


class Rig:
    def __init__(self, root, monkeypatch):
        self.root = root / "sessions"
        self.root.mkdir(mode=0o700)
        self.created, self.owners, self.actions = [], {}, {}
        self.cleanup_calls, self.admissions, self.groups = [], [], {}
        self.core_cleanup_returns = []
        self.native_calls = []
        self.cleanup_error = None
        self.setup_error = None
        # Rust Core's instance methods are read-only. Patch the real class seam,
        # never replace Core/coordinator with a fake positive implementation.
        self.actual_cleanup = AmplifierSession.cleanup
        actual_initialize = AmplifierSession.initialize

        async def owned_cleanup(actual):
            if actual.session_id == "parent":
                return await self.actual_cleanup(actual)
            self.cleanup_calls.append(actual.session_id)
            owner = self.owners.get(actual.session_id)
            if owner:
                owner.check()
            if self.cleanup_error is not None:
                raise self.cleanup_error
            await self.actual_cleanup(actual)
            self.core_cleanup_returns.append(actual.session_id)
            if owner:
                owner.check()

        async def initialize(actual):
            if actual.session_id != "parent" and actual.session_id in self.owners:
                native = actual.coordinator.get_capability("cli.session_store")
                assert native._canonical_binding is self.owners[actual.session_id]
                assert native.base_dir == self.root
                assert actual.coordinator.get_capability("cli.session_media").store.session_id == actual.session_id
                assert actual.coordinator.get_capability("cli.hooks.participating_graph") is not None
            if actual.session_id != "parent" and self.setup_error is not None:
                raise self.setup_error
            return await actual_initialize(actual)

        monkeypatch.setattr(AmplifierSession, "cleanup", owned_cleanup)
        monkeypatch.setattr(AmplifierSession, "initialize", initialize)
        def private_native(*args, **kwargs):
            self.native_calls.append((args, kwargs))
            return SessionStore(*args, **kwargs) if args or kwargs else SessionStore(self.root)
        monkeypatch.setattr("amplifier_app_cli.session_store.SessionStore", private_native)
        monkeypatch.setattr(spawner, "AmplifierSession", self.factory)
        monkeypatch.setattr(spawner, "_expand_system_instruction", AsyncMock(side_effect=lambda text, _: text))
        monkeypatch.setattr("amplifier_app_cli.paths.create_foundation_resolver", lambda: object())
        settings = MagicMock()
        settings.get_config_overrides.return_value = {}
        settings.get_provider_overrides.return_value = []
        settings.get_notification_hook_overrides.return_value = []
        monkeypatch.setattr("amplifier_app_cli.lib.settings.AppSettings", lambda: settings)
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "-1")

    def allocate(self, sid):
        owner = VersionedSessionBinding.create_new(
            self.root / sid, session_id=sid, operation_id="allocate-" + sid,
            admit=lambda: None,  # Allocation has an explicit empty transcript.
        )
        self.owners[sid] = owner
        return owner

    def admit(self, sid):
        def admit():
            owner = self.owners[sid]
            owner.check()
            store = next(s.coordinator.get_capability("cli.session_media").store
                         for s in reversed(self.created) if s.session_id == sid)
            for refs, origin in self.groups.get(sid, []):
                result = store.publish_sync(refs, origin=origin, disposition="retain")
                assert result["publication"]["durability"] == "assets-only"
            self.admissions.append(sid)
        return admit

    def factory(self, *args, **kwargs):
        actual = AmplifierSession(*args, **kwargs)
        self.created.append(actual)

        async def execute(*args, **kwargs):
            return await self.actions[actual.session_id](actual, *args, **kwargs)

        actual.coordinator.loader = Loader(execute)
        return actual

    async def parent(self, *, versioned=True, config=None):
        parent = AmplifierSession(deepcopy(PLAN if config is None else config), session_id="parent")
        parent.coordinator.loader = Loader(AsyncMock(return_value="parent"))
        if versioned:
            owner = self.allocate("parent")
            native = SessionStore(self.root, canonical_binding=owner)
        else:
            native = SessionStore(self.root)
        install_session_hook_graph(parent, owner="fixture.parent", host_participants=("host.media",))
        install_session_media(parent, native)
        self.created.append(parent)
        install_session_checkpoint(parent, native, session_id="parent",
                                   canonical_admit=self.admit("parent") if versioned else None)
        await parent.initialize()
        return parent, native

    async def spawn(self, parent, sid="child", **kwargs):
        return await spawner.spawn_sub_session(
            "named", "offline instruction", parent, {"named": {}}, sub_session_id=sid,
            canonical_binding=self.owners[sid], canonical_admit=self.admit(sid), **kwargs,
        )


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


async def assert_count_send(actual, raw_bytes):
    context = actual.coordinator.get("context")
    before = await context.get_messages()
    native = actual.coordinator.get_capability("cli.session_store")
    saved = native.load(actual.session_id)[0]
    assert saved == before
    bridge = actual.coordinator.get_capability("session.media.tool_post")
    assert bridge.validate_receipt(bridge.graph_receipt)
    host = actual.coordinator.get_capability("cli.session_media")
    assert type(native._canonical_binding.last_receipt) is VersionedCheckpointReceipt
    request = ChatRequest(messages=[Message(**row) for row in before])
    wrapped = actual.coordinator.get("providers")["fixture-provider"]
    raw = next(raw for raw, wrapper in host.wrappers.values() if wrapper is wrapped)
    await wrapped.request_budget(request)
    await wrapped.complete(request)
    counted, sent = raw.observed[-2:]
    assert counted[0] is sent[0] and counted[0] is not request
    assert counted[1] == sent[1] == [hashlib.sha256(raw_bytes).hexdigest()]
    assert await context.get_messages() == before and native.load(actual.session_id)[0] == saved
    assert actual.coordinator.get("tools") == {}  # No originating tool can be reexecuted.
    return bridge.graph_receipt, host.store, native._canonical_binding.last_receipt


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
@pytest.mark.parametrize("interval", ["-1", "0"])
async def test_actual_bound_raw_spawn_cold_resume_and_full_snapshot(rig, monkeypatch, image_format, interval):
    monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", interval)
    parent, parent_native = await rig.parent()
    parent_owner = rig.owners["parent"]
    child_owner = rig.allocate("child")
    payload = image_bytes(image_format)
    parent_host = parent.coordinator.get_capability("cli.session_media")
    origin = {"kind": "tool", "session_id": "parent", "id": "original-call"}
    ref = (await parent_host.capability.ingest(payload, source_format="image-bytes", origin=origin, policy=None))["value"]
    rig.groups["parent"] = [([ref], origin)]
    selected = [
        {"role": "assistant", "content": [{"type": "tool_call", "id": "original-call",
                                               "name": "original-tool", "input": {}}]},
        {"role": "tool", "tool_call_id": "original-call", "content": "original observation",
         "metadata": {MEDIA_NAMESPACE: {"refs": [ref]}}},
    ]
    await parent.coordinator.get("context").set_messages(selected)
    await parent.coordinator.get_capability("session.durable_checkpoint")()
    assert type(parent_owner.last_receipt) is VersionedCheckpointReceipt
    frozen_selected = deepcopy(selected)
    seen = []

    async def execute(actual, prompt, context, **kwargs):
        owner = rig.owners["child"]
        owner.check()
        native = actual.coordinator.get_capability("cli.session_store")
        assert native is not parent_native and native._canonical_binding is owner
        rows = await context.get_messages()
        child_ref = rows[1]["metadata"][MEDIA_NAMESPACE]["refs"][0]
        assert child_ref["session_id"] == "child" and child_ref["provenance"] == ref["provenance"]
        assert child_ref["session_id"] != ref["session_id"]
        seen.append(await assert_count_send(actual, payload))
        # A public durable checkpoint receives a full snapshot. Removing/replacing
        # prior rows must be an explicit rewrite, never count-based suffix append.
        tail = {"role": "user", "content": "temporary full snapshot tail"}
        await context.add_message(tail)
        await actual.coordinator.get_capability("session.durable_checkpoint")()
        assert owner.load_messages() == rows + [tail]
        await context.set_messages(rows)
        await actual.coordinator.get_capability("session.durable_checkpoint")()
        assert owner.load_messages() == rows
        return "child complete"

    rig.actions["child"] = execute
    try:
        result = await rig.spawn(parent, parent_messages=selected)
        assert result["output"] == "child complete"
        assert selected == frozen_selected
        final = selected_prefix(child_owner.directory)
        assert final == child_owner.expected and final.message_count == 2
        with pytest.raises(VersionedSessionConflict, match="closed"):
            child_owner.check()
        await parent.cleanup()
        parent_owner.close()
        assert parent_host.closed
        # Parent ownership is gone; a fresh resume can use only child assets.
        parent_snapshot = snapshot(parent_owner.directory)
        reopened = VersionedSessionBinding.resume(child_owner.directory, session_id="child", expected=final)
        rig.owners["child"] = reopened
        resumed = await spawner.resume_sub_session(
            "child", "offline followup", canonical_binding=reopened, canonical_admit=rig.admit("child"),
        )
        assert resumed["output"] == "child complete"
        assert snapshot(parent_owner.directory) == parent_snapshot
        assert len(seen) == 2
        assert all(seen[0][i] is not seen[1][i] for i in range(3))
        assert rig.cleanup_calls == ["child", "child"]
        assert len(rig.native_calls) == 2 and all(
            args == (rig.root,) and set(kwargs) == {"canonical_binding"}
            for args, kwargs in rig.native_calls
        )
        assert set(rig.admissions) == {"parent", "child"}
        persisted = json.dumps(SessionStore(rig.root).get_metadata("child"))
        assert "canonical_binding" not in persisted and "canonical_admit" not in persisted
        assert "VersionedSessionBinding" not in persisted
    finally:
        await parent.cleanup()
        for owner in rig.owners.values():
            owner.close()


@pytest.mark.parametrize("resume_child", [False, True])
async def test_nested_explicit_caller_gets_distinct_binding_without_inheritance(rig, resume_child):
    parent, _ = await rig.parent()
    child = rig.allocate("child")
    grand = rig.allocate("grand")
    observations = []

    async def grand_execute(actual, **kwargs):
        native = actual.coordinator.get_capability("cli.session_store")
        assert native._canonical_binding is grand and grand is not rig.owners["child"]
        rig.owners["child"].check()
        observations.append("grand")
        return "grand complete"

    async def child_execute(actual, **kwargs):
        nonlocal grand
        slot = spawner._SETUP_SESSION.get()
        assert slot[0] is actual and slot[2] is rig.owners["child"]
        if not resume_child or len(observations):
            nested = actual.coordinator.get_capability("session.spawn")
            result = await nested("named", "nested", actual, {"named": {}}, sub_session_id="grand",
                                  canonical_binding=grand, canonical_admit=rig.admit("grand"))
            assert result["output"] == "grand complete"
            assert spawner._SETUP_SESSION.get() is slot and slot[0] is actual
            grand = VersionedSessionBinding.resume(
                grand.directory, session_id="grand", expected=grand.expected,
            )
            rig.owners["grand"] = grand
            resume = actual.coordinator.get_capability("session.resume")
            with pytest.raises(RuntimeError, match="explicit live binding"):
                await resume("grand", "no implicit binding")
            grand.check()
            assert (await resume("grand", "explicit next leg", canonical_binding=grand,
                                 canonical_admit=rig.admit("grand")))["output"] == "grand complete"
            assert spawner._SETUP_SESSION.get() is slot and slot[0] is actual
        else:
            observations.append("first-leg")
        return "child complete"

    rig.actions.update(child=child_execute, grand=grand_execute)
    try:
        await rig.spawn(parent)
        if resume_child:
            reopened = VersionedSessionBinding.resume(child.directory, session_id="child", expected=child.expected)
            rig.owners["child"] = reopened
            await spawner.resume_sub_session("child", "followup", canonical_binding=reopened,
                                             canonical_admit=rig.admit("child"))
        assert "grand" in observations
        assert spawner._SETUP_SESSION.get() is None
        with pytest.raises(VersionedSessionConflict, match="closed"):
            grand.check()
        assert rig.cleanup_calls.count("grand") == 2
    finally:
        await parent.cleanup()
        for owner in rig.owners.values():
            owner.close()


@pytest.mark.parametrize("entry", ["spawn", "resume"])
@pytest.mark.parametrize("invalid", ["dict", "bool", "subclass", "wrong-id", "missing-id", "no-admit", "closed", "admit-only"])
async def test_private_arguments_validated_before_session_paths_or_dispatch(rig, entry, invalid):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    binding, admit, sid = owner, rig.admit("child"), "child"
    expected = (TypeError, ValueError, VersionedSessionConflict)
    if invalid == "dict":
        binding = {"session_id": "child", "directory": str(owner.directory)}
    elif invalid == "bool":
        binding = True
    elif invalid == "subclass":
        class NotExact(VersionedSessionBinding):
            pass
        binding = NotExact()
    elif invalid == "wrong-id":
        sid = "other"
    elif invalid == "missing-id":
        sid = None
    elif invalid == "no-admit":
        admit = None
    elif invalid == "closed":
        owner.close()
    elif invalid == "admit-only":
        binding = None
    before = snapshot(rig.root)
    created = list(rig.created)
    try:
        with pytest.raises(expected):
            if entry == "spawn":
                await spawner.spawn_sub_session("named", "offline", parent, {"named": {}}, sub_session_id=sid,
                                                canonical_binding=binding, canonical_admit=admit)
            else:
                await spawner.resume_sub_session(sid, "offline", canonical_binding=binding, canonical_admit=admit)
        assert snapshot(rig.root) == before and rig.created == created
        assert spawner._SETUP_SESSION.get() is None
        assert not rig.cleanup_calls
    finally:
        await parent.cleanup()
        owner.close()
        rig.owners["parent"].close()


@pytest.mark.parametrize("config_route", [False, True])
async def test_subprocess_binding_refused_before_payload_or_reservation(rig, config_route, monkeypatch):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    if config_route:
        parent.config["spawn_mode"] = "subprocess"
    runner = AsyncMock()
    monkeypatch.setattr("amplifier_foundation.subprocess_runner.run_session_in_subprocess", runner)
    before = snapshot(rig.root)
    try:
        with pytest.raises(RuntimeError, match="cannot transfer"):
            await rig.spawn(parent, use_subprocess=not config_route)
        runner.assert_not_called()
        assert snapshot(rig.root) == before and len(rig.created) == 1
        owner.check()  # Rejected before custody transfer; caller still owns it.
    finally:
        await parent.cleanup()
        for owner in rig.owners.values():
            owner.close()


@pytest.mark.parametrize("phase", ["setup", "execute"])
@pytest.mark.parametrize("primary_kind", ["error", "cancel"])
@pytest.mark.parametrize("cleanup_kind", ["success", "error", "cancel"])
async def test_continuous_cleanup_preserves_primary_and_retains_failed_owner(
    rig, caplog, monkeypatch, phase, primary_kind, cleanup_kind,
):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    primary = ValueError("PRIVATE_PRIMARY_SENTINEL") if primary_kind == "error" else asyncio.CancelledError("PRIVATE_PRIMARY_SENTINEL")
    failure = RuntimeError("PRIVATE_CLEANUP_SENTINEL") if cleanup_kind == "error" else asyncio.CancelledError("PRIVATE_CLEANUP_SENTINEL")
    if cleanup_kind != "success":
        rig.cleanup_error = failure
    if phase == "setup":
        rig.setup_error = primary
    else:
        # Fault at the host's session.execute await boundary. Rust Core wraps an
        # orchestrator-raised CancelledError; test that behavior separately.
        async def fail_dispatch(actual, prompt):
            raise primary
        monkeypatch.setattr(AmplifierSession, "execute", fail_dispatch)

    async def execute(actual, **kwargs):
        raise primary
    rig.actions["child"] = execute
    try:
        with caplog.at_level(logging.WARNING, logger=spawner.__name__):
            with pytest.raises(type(primary)) as caught:
                await rig.spawn(parent)
        assert caught.value is primary
        assert rig.cleanup_calls == ["child"]
        assert "PRIVATE_PRIMARY_SENTINEL" not in caplog.text
        assert "PRIVATE_CLEANUP_SENTINEL" not in caplog.text
        assert all(record.exc_info is None for record in caplog.records if record.name == spawner.__name__)
        assert spawner._SETUP_SESSION.get() is None
        if cleanup_kind == "success":
            with pytest.raises(VersionedSessionConflict, match="closed"):
                owner.check()
            assert owner not in spawner._RETAINED_CHILD_CLEANUPS
        else:
            owner.check()
            before = snapshot(owner.directory)
            with pytest.raises(RuntimeError, match="explicit owner settlement"):
                await rig.spawn(parent)
            assert snapshot(owner.directory) == before and rig.cleanup_calls == ["child"]
            actual, retained, error = spawner._RETAINED_CHILD_CLEANUPS.pop(owner)
            assert retained is owner and error is failure
            with pytest.raises(VersionedSessionConflict, match="retains"):
                VersionedSessionBinding.resume(owner.directory, session_id="child", expected=owner.expected)
            # Explicit test-owner settlement, not a production replay of cleanup.
            await rig.actual_cleanup(actual)
            owner.close()
    finally:
        rig.cleanup_error = None
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


@pytest.mark.parametrize("cleanup_kind", ["error", "cancel"])
async def test_cleanup_failure_without_primary_propagates_exact_error(rig, cleanup_kind):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    failure = OSError("PRIVATE_CLEANUP_SENTINEL") if cleanup_kind == "error" else asyncio.CancelledError("PRIVATE_CLEANUP_SENTINEL")
    rig.cleanup_error = failure
    rig.actions["child"] = AsyncMock(return_value="done")
    try:
        with pytest.raises(type(failure)) as caught:
            await rig.spawn(parent)
        assert caught.value is failure
        assert rig.cleanup_calls == ["child"]
        actual, retained, error = spawner._RETAINED_CHILD_CLEANUPS.pop(owner)
        assert retained is owner and error is failure
        owner.check()
        await rig.actual_cleanup(actual)
        owner.close()
    finally:
        rig.cleanup_error = None
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


async def test_unknown_operation_blocks_final_save_without_new_admission_or_replay(rig, monkeypatch):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    calls = []
    original_rewrite = owner._writer.rewrite
    failure = OSError("unknown physical completion")
    initial = owner.expected

    def commit_then_fail(*args, **kwargs):
        calls.append(kwargs["operation_id"])
        original_rewrite(*args, **kwargs)
        raise failure

    async def execute(actual, context, **kwargs):
        await context.add_message({"role": "user", "content": "committed before unknown"})
        monkeypatch.setattr(owner._writer, "rewrite", commit_then_fail)
        with pytest.raises(OSError) as caught:
            await actual.coordinator.get_capability("session.durable_checkpoint")()
        assert caught.value is failure
        assert owner.expected == initial
        settled = selected_prefix(owner.directory)
        assert settled != initial and settled.operation_id == calls[0]
        before = snapshot(owner.directory)
        admissions = list(rig.admissions)
        # Even the old autosave path is fail-closed when explicitly bound.
        with pytest.raises(VersionedSessionConflict, match="reconciliation"):
            await spawner._write_checkpoint(actual, actual.coordinator.get_capability("cli.session_store"), "child", {})
        assert snapshot(owner.directory) == before and rig.admissions == admissions
        return "not successful"

    rig.actions["child"] = execute
    try:
        with pytest.raises(VersionedSessionConflict, match="reconciliation"):
            await rig.spawn(parent)
        assert len(calls) == 1 and rig.admissions.count("child") == 1
        assert owner.expected == initial
        assert selected_prefix(owner.directory).operation_id == calls[0]
        assert SessionStore(rig.root).load("child")[0][0]["content"] == "committed before unknown"
        assert rig.cleanup_calls == ["child"]
    finally:
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


async def test_resume_without_binding_refuses_versioned_before_child_or_write(rig, monkeypatch):
    owner = rig.allocate("child")
    owner.close()
    native = SessionStore(rig.root)
    monkeypatch.setattr("amplifier_app_cli.session_store.SessionStore", lambda: native)
    before = snapshot(rig.root)
    with pytest.raises(RuntimeError, match="explicit live binding"):
        await spawner.resume_sub_session("child", "no implicit enrollment")
    assert snapshot(rig.root) == before and not rig.created


async def test_legacy_resume_keeps_legacy_route_and_ignores_serialized_binding_data(rig, monkeypatch):
    native = SessionStore(rig.root)
    plan = deepcopy(PLAN)
    plan["canonical_binding"] = {"session_id": "legacy", "directory": "not authority"}
    native.save("legacy", [{"role": "user", "content": "known legacy"}], {
        "config": plan, "agent_name": "named", "canonical_admit": True,
    })
    monkeypatch.setattr("amplifier_app_cli.session_store.SessionStore", lambda: native)

    async def execute(actual, context, **kwargs):
        assert actual.coordinator.get_capability("cli.session_store")._canonical_binding is None
        assert actual.coordinator.get_capability("cli.canonical_checkpoint_save") is None
        assert (await context.get_messages())[0]["content"] == "known legacy"
        return "legacy complete"
    rig.actions["legacy"] = execute
    result = await spawner.resume_sub_session("legacy", "follow-up")
    assert result["output"] == "legacy complete"
    assert not (rig.root / "legacy" / "transcript.v2").exists()
    assert rig.cleanup_calls == ["legacy"]


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
async def test_actual_child_staged_assets_are_published_by_explicit_admission(rig, image_format):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    payload = image_bytes(image_format)
    publications = []

    async def execute(actual, context, **kwargs):
        host = actual.coordinator.get_capability("cli.session_media")
        origin = {"kind": "upload", "session_id": "child", "id": "child-upload"}
        ref = (await host.capability.ingest(
            payload, source_format="image-bytes", origin=origin, policy=None,
        ))["value"]
        rig.groups["child"] = [([ref], origin)]
        await context.add_message({"role": "user", "content": "child-owned upload",
                                   "metadata": {MEDIA_NAMESPACE: {"refs": [ref]}}})
        receipt = await actual.coordinator.get_capability("session.durable_checkpoint")()
        assert type(receipt) is VersionedCheckpointReceipt
        publications.append(receipt.verify())
        await assert_count_send(actual, payload)
        return "staged child complete"

    rig.actions["child"] = execute
    try:
        assert (await rig.spawn(parent))["output"] == "staged child complete"
        assert len(publications) == 1 and rig.admissions.count("child") == 2
        assert len(list((owner.directory / "media" / "assets").iterdir())) == 1
    finally:
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


@pytest.mark.parametrize("cleanup_kind", ["success", "error", "cancel"])
async def test_external_task_cancellation_through_actual_core_keeps_cancelled_error(rig, cleanup_kind):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    entered = asyncio.Event()

    async def execute(actual, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    rig.actions["child"] = execute
    task = asyncio.create_task(rig.spawn(parent))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if cleanup_kind != "success":
            rig.cleanup_error = (RuntimeError("private cleanup") if cleanup_kind == "error"
                                 else asyncio.CancelledError("private cleanup"))
        task.cancel("actual caller cancellation")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert task.cancelled() and rig.cleanup_calls == ["child"]
        assert owner not in spawner._CHILD_BINDING_OWNERS
        if cleanup_kind == "success":
            with pytest.raises(VersionedSessionConflict, match="closed"):
                owner.check()
        else:
            owner.check()
            actual, retained, error = spawner._RETAINED_CHILD_CLEANUPS.pop(owner)
            assert retained is owner and error is rig.cleanup_error
            await rig.actual_cleanup(actual)
            owner.close()
    finally:
        rig.cleanup_error = None
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


@pytest.mark.parametrize("raised", [ValueError("fixture failure"), asyncio.CancelledError("fixture cancellation")])
async def test_actual_core_orchestrator_failures_are_wrapped_not_claimed_as_propagation(rig, raised):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")

    async def execute(actual, **kwargs):
        raise raised
    rig.actions["child"] = execute
    try:
        with pytest.raises(RuntimeError, match="Execution failed"):
            await rig.spawn(parent)
        assert rig.cleanup_calls == ["child"]
        with pytest.raises(VersionedSessionConflict, match="closed"):
            owner.check()
    finally:
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


async def test_parent_binding_and_concurrent_reuse_refuse_without_closing_live_owner(rig):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def execute(actual, **kwargs):
        entered.set()
        await finish.wait()
        return "one owner"

    rig.actions["child"] = execute
    task = None
    try:
        with pytest.raises(ValueError, match="parent's live"):
            await rig.spawn(parent, sid="parent")
        rig.owners["parent"].check()
        task = asyncio.create_task(rig.spawn(parent))
        await asyncio.wait_for(entered.wait(), timeout=5)
        before = snapshot(owner.directory)
        with pytest.raises(RuntimeError, match="already has a live cleanup owner"):
            await rig.spawn(parent)
        assert snapshot(owner.directory) == before
        owner.check()
        assert not rig.cleanup_calls
        finish.set()
        assert (await asyncio.wait_for(task, timeout=5))["output"] == "one owner"
        assert rig.cleanup_calls == ["child"]
        assert owner not in spawner._CHILD_BINDING_OWNERS
    finally:
        finish.set()
        if task is not None and not task.done():
            await task
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


async def test_nested_capability_omission_does_not_inherit_parent_binding(rig, monkeypatch):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    actual_store = SessionStore

    def private_native(*args, **kwargs):
        return actual_store(*args, **kwargs) if args or kwargs else actual_store(rig.root)
    monkeypatch.setattr("amplifier_app_cli.session_store.SessionStore", private_native)

    async def unbound(actual, **kwargs):
        native = actual.coordinator.get_capability("cli.session_store")
        assert native._canonical_binding is None
        assert actual.coordinator.get_capability("cli.canonical_checkpoint_save") is None
        owner.check()
        return "no inherited binding"

    async def child(actual, **kwargs):
        result = await actual.coordinator.get_capability("session.spawn")(
            "named", "explicit text child", actual, {"named": {}}, sub_session_id="unbound",
        )
        assert result["output"] == "no inherited binding"
        owner.check()
        return "child complete"
    rig.actions.update(child=child, unbound=unbound)
    try:
        await rig.spawn(parent)
        assert not (rig.root / "unbound" / "transcript.v2").exists()
        assert rig.cleanup_calls == ["unbound", "child"]
    finally:
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


async def test_cold_resume_exact_selector_failure_precedes_media_and_core_construction(rig):
    owner = rig.allocate("child")
    original = owner.expected
    owner.close()
    reopened = VersionedSessionBinding.resume(owner.directory, session_id="child", expected=original)
    # A changed selector after admission is detected by the actual public
    # binding loader, not replaced by SessionStore's unselected native reader.
    reopened.expected = replace(original, sequence=original.sequence + 1)
    before = snapshot(owner.directory)
    try:
        with pytest.raises(ValueError):
            await spawner.resume_sub_session(
                "child", "must not construct", canonical_binding=reopened,
                canonical_admit=lambda: pytest.fail("admission must not run"),
            )
        assert not rig.created and not rig.cleanup_calls
        assert snapshot(owner.directory) == before
        assert reopened not in spawner._CHILD_BINDING_OWNERS
        with pytest.raises(VersionedSessionConflict, match="closed"):
            reopened.check()
    finally:
        reopened.close()


@pytest.mark.parametrize("failure_kind", ["error", "cancel"])
async def test_binding_custody_covers_configuration_await_before_child_construction(rig, monkeypatch, failure_kind):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    entered = asyncio.Event()
    failure = ValueError("offline configuration failure")

    async def resolution(*args):
        assert owner in spawner._CHILD_BINDING_OWNERS
        entered.set()
        if failure_kind == "error":
            raise failure
        await asyncio.Event().wait()

    monkeypatch.setattr(spawner, "_apply_provider_preferences", resolution)
    task = asyncio.create_task(rig.spawn(parent, provider_preferences=[
        {"provider": "fixture-provider", "model": "fixture-vision"},
    ]))
    before = snapshot(owner.directory)
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if failure_kind == "cancel":
            task.cancel("cancel configuration setup")
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ValueError) as caught:
                await task
            assert caught.value is failure
        assert len(rig.created) == 1 and not rig.cleanup_calls
        assert snapshot(owner.directory) == before
        assert owner not in spawner._CHILD_BINDING_OWNERS
        with pytest.raises(VersionedSessionConflict, match="closed"):
            owner.check()
    finally:
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


@pytest.mark.parametrize("entry", ["spawn", "resume"])
@pytest.mark.parametrize("primary_kind", [
    "complete", "setup-error", "setup-cancel", "dispatch-error", "dispatch-cancel",
    "external-cancel",
])
@pytest.mark.parametrize("cleanup_kind", ["error", "cancel"])
async def test_actual_core_swallowed_media_callback_failure_keeps_binding(
    rig, monkeypatch, caplog, entry, primary_kind, cleanup_kind,
):
    """A normal Core return and host.closed=True are not cleanup settlement."""
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    rig.actions["child"] = AsyncMock(return_value="seed")
    if entry == "resume":
        await rig.spawn(parent)
        owner = VersionedSessionBinding.resume(
            owner.directory, session_id="child", expected=owner.expected,
        )
        rig.owners["child"] = owner
        rig.cleanup_calls.clear()
        rig.core_cleanup_returns.clear()
    original_close = CLISessionMedia._close
    failure = (OSError("PRIVATE_REGISTERED_CALLBACK_FAILURE") if cleanup_kind == "error"
               else asyncio.CancelledError("PRIVATE_REGISTERED_CALLBACK_CANCEL"))
    callback_attempts = []
    entered = asyncio.Event()
    primary = None
    task = None
    host = None

    async def fail_actual_host_close(current):
        if current.session_id != "child":
            return await original_close(current)
        owner.check()
        current.store._check()
        callback_attempts.append(current)
        # The real _close sets this before closing resources. Deliberately
        # preserve an open media store to exercise exactly that unsafe shortcut.
        current.closed = True
        raise failure

    monkeypatch.setattr(CLISessionMedia, "_close", fail_actual_host_close)
    if primary_kind.startswith(("setup", "dispatch")):
        primary = (asyncio.CancelledError("PRIVATE_PRIMARY_CANCEL")
                   if primary_kind.endswith("cancel") else ValueError("PRIVATE_PRIMARY_ERROR"))
        if primary_kind.startswith("setup"):
            rig.setup_error = primary
        else:
            async def fail_dispatch(actual, prompt):
                raise primary
            monkeypatch.setattr(AmplifierSession, "execute", fail_dispatch)

    async def execute(actual, **kwargs):
        if primary_kind == "external-cancel":
            entered.set()
            await asyncio.Event().wait()
        return "normal execution is not cleanup success"
    rig.actions["child"] = execute

    async def invoke():
        if entry == "spawn":
            return await rig.spawn(parent)
        return await spawner.resume_sub_session(
            "child", "offline followup", canonical_binding=owner,
            canonical_admit=rig.admit("child"),
        )

    try:
        with caplog.at_level(logging.WARNING):
            task = asyncio.create_task(invoke())
            if primary_kind == "external-cancel":
                await asyncio.wait_for(entered.wait(), timeout=5)
                task.cancel("external caller cancellation")
            expected_error = (primary if primary is not None else failure)
            if primary_kind == "external-cancel":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
                assert task.cancelled()
            else:
                with pytest.raises(type(expected_error)) as caught:
                    await asyncio.wait_for(task, timeout=5)
                assert caught.value is expected_error

        actual, retained, cleanup_error = spawner._RETAINED_CHILD_CLEANUPS[owner]
        host = actual.coordinator.get_capability("cli.session_media")
        assert type(host) is CLISessionMedia and callback_attempts == [host]
        # Critical negative control: unmodified actual Core ran the registered
        # host.close, logged/swallowed its error, and nevertheless returned.
        assert rig.cleanup_calls == ["child"] and rig.core_cleanup_returns == ["child"]
        assert host.closed is True and host.cleanup_attempted is True
        assert host.cleanup_settled is False and host.cleanup_error is failure
        assert retained is owner and cleanup_error is failure
        owner.check()
        host.store._check()  # The physical media owner is still open.
        assert owner not in spawner._CHILD_BINDING_OWNERS
        assert spawner._SETUP_SESSION.get() is None
        with pytest.raises(VersionedSessionConflict, match="retains"):
            VersionedSessionBinding.resume(
                owner.directory, session_id="child", expected=owner.expected,
            )
        before = snapshot(owner.directory)
        with pytest.raises(RuntimeError, match="explicit owner settlement"):
            await invoke()
        await host.close()  # Actual owner's no-replay latch, not _close replay.
        assert callback_attempts == [host] and rig.cleanup_calls == ["child"]
        assert snapshot(owner.directory) == before
        own_records = [record for record in caplog.records if record.name == spawner.__name__]
        assert all(record.exc_info is None and "PRIVATE_" not in record.getMessage()
                   for record in own_records)
        if primary_kind != "complete":
            assert any(record.getMessage() == "Child session cleanup failed; source owner retained"
                       for record in own_records)
    finally:
        # Explicit test-owner resource settlement. Do not replay Core cleanup,
        # host.close or the failed _close operation to make the test look green.
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        if host is None and callback_attempts:
            host = callback_attempts[0]
        if host is not None:
            for _, wrapper in host.wrappers.values():
                await wrapper.aclose()
            host.native.unbind_media_store(host.session_id, host.store)
            host.store.close()
        spawner._RETAINED_CHILD_CLEANUPS.pop(owner, None)
        rig.setup_error = None
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


@pytest.mark.parametrize("rows", [
    [{"role": "user", "content": "selected history must survive"}],
    [{"role": "user", "content": "first"}, {"role": "assistant", "content": "settled answer"}],
])
async def test_spawn_rejects_reacquired_nonempty_binding_before_acceptance_and_resume_preserves(rig, rows):
    parent, _ = await rig.parent()
    owner = rig.allocate("child")
    seed = SessionStore(rig.root, canonical_binding=owner)
    seed.save_versioned(
        "child", rows, {"config": deepcopy(PLAN), "agent_name": "named"},
        expected=owner.expected, operation_id="seed-selected-history", admit=lambda: None,
    )
    selected = owner.expected
    saved_rows = owner.load_messages()
    owner.close()
    reacquired = VersionedSessionBinding.resume(
        owner.directory, session_id="child", expected=selected,
    )
    rig.owners["child"] = reacquired
    before = snapshot(reacquired.directory)
    created, admissions, natives = list(rig.created), list(rig.admissions), list(rig.native_calls)
    try:
        with pytest.raises(ValueError, match="empty versioned source; use explicit resume"):
            await rig.spawn(parent)
        assert rig.created == created and rig.admissions == admissions
        assert rig.native_calls == natives and not rig.cleanup_calls
        assert snapshot(reacquired.directory) == before
        assert reacquired.expected == selected and reacquired.load_messages() == saved_rows
        assert reacquired not in spawner._CHILD_BINDING_OWNERS
        assert reacquired not in spawner._RETAINED_CHILD_CLEANUPS
        reacquired.check()  # Validation refusal never accepts or closes caller custody.
        with pytest.raises(VersionedSessionConflict, match="retains"):
            VersionedSessionBinding.resume(
                reacquired.directory, session_id="child", expected=selected,
            )

        async def execute(actual, context, **kwargs):
            assert await context.get_messages() == saved_rows
            assert actual.coordinator.get_capability("cli.session_store")._canonical_binding is reacquired
            return "explicit resume kept the selected history"
        rig.actions["child"] = execute
        result = await spawner.resume_sub_session(
            "child", "explicit follow-up", canonical_binding=reacquired,
            canonical_admit=rig.admit("child"),
        )
        assert result["output"] == "explicit resume kept the selected history"
        assert SessionStore(rig.root).load("child")[0] == saved_rows
        assert rig.cleanup_calls == ["child"]
    finally:
        await parent.cleanup()
        for current in rig.owners.values():
            current.close()


@pytest.mark.parametrize("resume_child", [False, True])
async def test_nested_resume_uses_actual_child_media_limit_and_cancellation_owner(rig, resume_child):
    ancestor_plan = deepcopy(PLAN)
    ancestor_plan["session_media"] = {"max_images": 5}
    ancestor, _ = await rig.parent(config=ancestor_plan)
    rig.allocate("child")
    grand = rig.allocate("grand")
    grand_plan = deepcopy(PLAN)
    grand_plan["session_media"] = {"max_images": 5}
    seed = SessionStore(rig.root, canonical_binding=grand)
    seed.save_versioned(
        "grand", [{"role": "user", "content": "historical grandchild"}],
        {"config": grand_plan, "agent_name": "named", "parent_id": "child"},
        expected=grand.expected, operation_id="seed-grand", admit=lambda: None,
    )
    selected = grand.expected
    grand.close()
    grand = VersionedSessionBinding.resume(grand.directory, session_id="grand", expected=selected)
    rig.owners["grand"] = grand
    immediate = None
    observed_tokens = []
    first_leg = True

    async def grand_execute(actual, context, **kwargs):
        assert immediate is not None
        assert ancestor.coordinator.get_capability("cli.session_media").store.policy.max_images == 5
        assert immediate.coordinator.get_capability("cli.session_media").store.policy.max_images == 1
        assert actual.config["session_media"]["max_images"] == 1
        assert actual.coordinator.get_capability("cli.session_media").store.policy.max_images == 1
        # These are actual RustCancellationToken objects. Cancelling the actual
        # child must propagate to its resumed grandchild, never the ancestor.
        child_token = immediate.coordinator.cancellation
        grand_token = actual.coordinator.cancellation
        ancestor_token = ancestor.coordinator.cancellation
        assert not grand_token.is_cancelled and not ancestor_token.is_cancelled
        assert child_token.request_graceful()
        assert grand_token.is_graceful and child_token.is_graceful
        assert not ancestor_token.is_cancelled
        observed_tokens.append(grand_token)
        child_token.reset()
        grand_token.reset()
        return "narrowed by actual child"

    async def child_execute(actual, **kwargs):
        nonlocal immediate, first_leg
        if resume_child and first_leg:
            first_leg = False
            return "seed child leg"
        immediate = actual
        assert actual.config["session_media"]["max_images"] == 1
        owner = rig.owners["child"]
        owner.check()
        result = await actual.coordinator.get_capability("session.resume")(
            "grand", "nested resume", canonical_binding=grand,
            canonical_admit=rig.admit("grand"),
        )
        assert result["output"] == "narrowed by actual child"
        assert len(observed_tokens) == 1
        # The same immediate owner must also unregister the exact child token.
        assert actual.coordinator.cancellation.request_graceful()
        assert not observed_tokens[0].is_cancelled
        assert not ancestor.coordinator.cancellation.is_cancelled
        actual.coordinator.cancellation.reset()
        owner.check()
        return "immediate child remained owner"

    rig.actions.update(child=child_execute, grand=grand_execute)
    try:
        await spawner.spawn_sub_session(
            "named", "offline child", ancestor,
            {"named": {"session_media": {"max_images": 1}}}, sub_session_id="child",
            canonical_binding=rig.owners["child"], canonical_admit=rig.admit("child"),
        )
        if resume_child:
            previous = rig.owners["child"]
            reopened = VersionedSessionBinding.resume(
                previous.directory, session_id="child", expected=previous.expected,
            )
            rig.owners["child"] = reopened
            assert (await spawner.resume_sub_session(
                "child", "resume immediate child", parent_session=ancestor,
                canonical_binding=reopened, canonical_admit=rig.admit("child"),
            ))["output"] == "immediate child remained owner"
        assert len(observed_tokens) == 1 and rig.cleanup_calls.count("grand") == 1
        ancestor.coordinator.get_capability("cli.session_store")._canonical_binding.check()
    finally:
        await ancestor.cleanup()
        for current in rig.owners.values():
            current.close()


async def test_actual_registered_root_resume_refuses_its_own_binding_before_child_acceptance(rig):
    from amplifier_app_cli.session_runner import register_session_spawning

    root_plan = deepcopy(PLAN)
    root_plan["session_media"] = {"enabled": False}
    actual_root, native = await rig.parent(config=root_plan)
    owner = rig.owners["parent"]
    rows = [{"role": "user", "content": "root history must not become a child"}]
    native.save_versioned(
        "parent", rows, {"config": root_plan, "agent_name": "named"},
        expected=owner.expected, operation_id="seed-root", admit=lambda: None,
    )
    await actual_root.coordinator.get("context").set_messages(owner.load_messages())
    register_session_spawning(actual_root)
    resume = actual_root.coordinator.get_capability("session.resume")
    created, admissions = list(rig.created), list(rig.admissions)
    before = snapshot(owner.directory)
    selected = owner.expected
    try:
        assert actual_root.coordinator.get_capability("cli.session_media").durable is False
        with pytest.raises(ValueError, match="parent's live versioned binding"):
            await resume(
                "parent", "must not borrow root", canonical_binding=owner,
                canonical_admit=rig.admit("parent"),
            )
        assert rig.created == created and rig.admissions == admissions
        assert not rig.cleanup_calls
        assert snapshot(owner.directory) == before and owner.expected == selected
        assert owner not in spawner._CHILD_BINDING_OWNERS
        assert owner not in spawner._RETAINED_CHILD_CLEANUPS
        owner.check()
        with pytest.raises(VersionedSessionConflict, match="retains"):
            VersionedSessionBinding.resume(
                owner.directory, session_id="parent", expected=selected,
            )
        assert native.load("parent")[0] == await actual_root.coordinator.get("context").get_messages()
        assert await actual_root.execute("root still usable") == "parent"
        owner.check()
    finally:
        await actual_root.cleanup()
        owner.close()
