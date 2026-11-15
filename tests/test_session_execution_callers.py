"""Actual public CLI callers join selected Python exit before physical cleanup.

No Core execute/initialize/cleanup methods, execution-owner helpers, or positive
InitializedSession results are replaced. The selected synthetic Python loop is
mounted by an offline loader, then wrapped by production's participating adapter.
Scripted input is presentation-only: no TTY, provider network or EP qualification.
"""

import asyncio
import base64
from contextlib import nullcontext
from copy import deepcopy
from importlib import import_module
from io import BytesIO, StringIO
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image
from rich.console import Console

from amplifier_core import AmplifierSession
from amplifier_core.message_models import ChatRequest, ImageBlock, Message
from amplifier_foundation.bundle import Bundle, PreparedBundle
# Freeze Foundation's actual Core identity before the controlled factory patch.
from amplifier_foundation.session.media_hook_bridge import MediaHookBridge
from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding, VersionedSessionConflict
from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt

from amplifier_app_cli import session_spawner as spawner
from amplifier_app_cli.session_binding_custody import binding_custody
from amplifier_app_cli.session_checkpoint import install_session_checkpoint
from amplifier_app_cli.session_hooks import install_session_hook_graph, seal_session_hook_graph
from amplifier_app_cli.session_media import cleanup_session_media_owner, install_session_media, mount_session_media
from amplifier_app_cli.session_runner import SessionConfig, create_initialized_session
from amplifier_app_cli.session_store import SessionStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "linux", reason="Actual Foundation prospective custody is Linux-only"),
]
PLAN = {
    "session": {"orchestrator": "exit-loop", "context": "exit-context"},
    "providers": [{"module": "exit-provider"}],
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
    name, default_model = "exit-provider", "synthetic-vision"

    def __init__(self):
        self.observed = []

    def get_media_request_support(self, *, model, request_options):
        return {"v": 1, "positions": ["user", "tool"]}

    def observe(self, request):
        pixels = [base64.b64decode(block.source["data"])
                  for message in request.messages if isinstance(message.content, list)
                  for block in message.content if isinstance(block, ImageBlock)]
        self.observed.append((request, pixels))

    async def request_budget(self, request, *, request_options=None):
        self.observe(request)
        return {"estimated_input_tokens": 4, "input_limit_tokens": 100,
                "context_token_budget": 100, "measurement": {
                    "kind": "provider_count", "source": "exit-fixture", "input_tokens": 4}}

    async def complete(self, request):
        self.observe(request)
        return "synthetic observation accepted"


class Loader:
    def __init__(self, selected):
        self.selected, self.queue = selected, []

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            if module_id == "exit-loop":
                await current.mount("orchestrator", self.selected)
            elif module_id == "exit-context":
                await current.mount("context", Context())
            elif module_id == "exit-provider":
                await current.mount("providers", Provider(), name=module_id)
            else:
                raise AssertionError(f"Unapproved offline module: {module_id}")
        return mount

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


def prefix(root):
    with open_committed_source(root) as source:
        return source.commit


async def bounded(awaitable):
    """Timeout only local fixture observation, never cancel the actual caller."""
    return await asyncio.wait_for(awaitable, timeout=5)


class Callers:
    def __init__(self, tmp_path, monkeypatch, image_format):
        self.parent_dir = tmp_path / "sessions"
        self.parent_dir.mkdir(mode=0o700)
        self.monkeypatch = monkeypatch
        self.created, self.bindings, self.groups = [], {}, {}
        self.admissions, self.cleanups, self.physical_closes, self.python_exits = [], [], [], []
        self.receipts, self.selected, self.pending_data = {}, {}, {}
        self.entries, self.save_phases, self.cancelled_targets = [], [], set()
        self.held = asyncio.Event()
        self.release = asyncio.Event()
        self.exit_seen = asyncio.Event()
        self.observe_cancel = []
        self.seed = False
        self.target = "root"
        self.image_format = image_format
        stream = BytesIO()
        Image.new("RGB", (3, 2), (17, 37, 83)).save(stream, image_format)
        self.payload = stream.getvalue()
        self.main = import_module("amplifier_app_cli.main")
        self.parent = None

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "-1")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("amplifier_core.AmplifierSession", self.factory)
        monkeypatch.setattr(spawner, "AmplifierSession", self.factory)
        monkeypatch.setattr("amplifier_app_cli.commands.init.check_first_run", lambda: False)
        monkeypatch.setattr("amplifier_app_cli.paths.create_foundation_resolver", lambda: object())
        monkeypatch.setattr("amplifier_app_cli.runtime.config.inject_user_providers", lambda *a, **k: None)
        settings = MagicMock()
        settings.with_session.return_value.get_merged_settings.return_value = {"configurator": {}}
        settings.get_config_overrides.return_value = {}
        settings.get_provider_overrides.return_value = []
        settings.get_notification_hook_overrides.return_value = []
        settings.get_slash_popup_enabled.return_value = False
        monkeypatch.setattr("amplifier_app_cli.session_runner.AppSettings", lambda: settings)
        monkeypatch.setattr("amplifier_app_cli.lib.settings.AppSettings", lambda: settings)
        monkeypatch.setattr(self.main, "AppSettings", lambda: settings)
        monkeypatch.setattr(self.main, "console", Console(file=StringIO()))
        monkeypatch.setattr("amplifier_app_cli.session_runner.SessionStore", self.native)
        monkeypatch.setattr("amplifier_app_cli.session_store.SessionStore", self.native)
        monkeypatch.setattr(self.main, "SessionStore", self.native)
        monkeypatch.setattr("amplifier_app_cli.session_handoff.acquire_root", self.no_legacy_acquire)
        actual_save = SessionStore.save_versioned

        def save(native, sid, rows, metadata, **options):
            actual = next(s for s in reversed(self.created) if s.session_id == sid)
            exited = id(actual) in self.python_exits
            if sid in self.cancelled_targets:
                assert exited  # Public failed-turn saves cannot race Python's finally.
            result = actual_save(native, sid, rows, metadata, **options)
            self.save_phases.append((actual, exited, native._canonical_binding.expected, deepcopy(rows)))
            return result
        monkeypatch.setattr(SessionStore, "save_versioned", save)

        async def expand(text, session):
            return text
        monkeypatch.setattr(spawner, "_expand_system_instruction", expand)

        # The steering reader is presentation/input only. Do not let it discover
        # /dev/tty; the public interactive loop and execute_task remain real.
        async def no_input(manager):
            await asyncio.Event().wait()
        monkeypatch.setattr("amplifier_app_cli.steering_input.SteeringInputManager.run", no_input)
        monkeypatch.setattr(self.main, "patch_stdout", lambda **kwargs: nullcontext())
        prompts = iter(["inspect fixture", "exit"])

        async def prompt_async(**kwargs):
            return next(prompts)
        monkeypatch.setattr(self.main, "_create_prompt_session", lambda **kwargs: SimpleNamespace(prompt_async=prompt_async))

    async def no_legacy_acquire(self, *args, **kwargs):
        raise AssertionError("No legacy root writer may acquire")

    def native(self, *args, **kwargs):
        return SessionStore(*args, **kwargs) if args or kwargs else SessionStore(self.parent_dir)

    def allocate(self, sid):
        def admit_empty():
            assert not (self.parent_dir / sid).exists()
            assert not any(s.session_id == sid for s in self.created)
        binding = VersionedSessionBinding.create_new(
            self.parent_dir / sid, session_id=sid, operation_id="allocate-" + sid, admit=admit_empty,
        )
        self.bindings[sid] = binding
        return binding

    def admit(self, sid):
        def publish():
            self.bindings[sid].check()
            actual = next(s for s in reversed(self.created) if s.session_id == sid)
            host = actual.coordinator.get_capability("cli.session_media")
            assert host.native._canonical_binding is self.bindings[sid]
            for ref, origin in self.groups.get(sid, []):
                host.store.publish_sync([ref], origin=origin, disposition="retain")
            self.admissions.append(sid)
        return publish

    def factory(self, *args, **kwargs):
        actual = AmplifierSession(*args, **kwargs)
        self.created.append(actual)

        async def execute(prompt, context, providers, tools, hooks, coordinator, **options):
            sid = actual.session_id
            self.entries.append(actual)
            seed = self.seed
            host = coordinator.get_capability("cli.session_media")
            binding = self.bindings[sid]
            assert host.native._canonical_binding is binding
            bridge = coordinator.get_capability("session.media.tool_post")
            assert isinstance(bridge, MediaHookBridge)
            assert bridge.validate_receipt(bridge.graph_receipt)
            owner = coordinator.get_capability("cli.session_execution")
            assert owner.selected is self.selected[id(actual)]
            assert coordinator.get("orchestrator") is owner.proxy
            graph = coordinator.get_capability("cli.hooks.participating_graph")
            assert any(edge["previous"] is owner.selected and edge["installed"] is owner.proxy
                       for edge in graph.construction.participants_evidence()["adapters"])
            try:
                rows = await context.get_messages()
                if not rows:
                    origin = {"kind": "tool", "session_id": sid, "id": "fixture-image"}
                    ref = (await host.capability.ingest(self.payload, source_format="image-bytes",
                                                        origin=origin, policy=None))["value"]
                    outcome = await bridge({"tool_name": "fixture-image", "tool_call_id": origin["id"],
                                            "result": {"refs": [ref]}})
                    assert outcome["complete"]
                    self.groups[sid] = [(ref, origin)]
                    await context.add_message({"role": "user", "content": "inspect fixture", "metadata": {
                        "experimental.media.v1": {"refs": [ref]},
                    }})
                    receipt = await coordinator.get_capability("session.durable_checkpoint")()
                    assert type(receipt) is VersionedCheckpointReceipt
                    self.receipts[id(actual)] = receipt
                else:
                    # Cold resume acknowledged actual committed refs during setup,
                    # never restages/republishes the originating image.
                    assert type(binding.last_receipt) is VersionedCheckpointReceipt
                    self.receipts[id(actual)] = binding.last_receipt
                canonical = await context.get_messages()
                request = ChatRequest(messages=[Message(**row) for row in canonical])
                native = host.native.load(sid)[0]
                assert native == canonical
                provider = providers["exit-provider"]
                raw = next(raw for raw, wrapped in host.wrappers.values() if wrapped is provider)
                await provider.request_budget(request)
                assert await provider.complete(request) == "synthetic observation accepted"
                counted, sent = raw.observed[-2:]
                assert counted[0] is sent[0] and counted[0] is not request
                assert counted[1] == sent[1] == [self.payload]
                assert await context.get_messages() == canonical
                assert host.native.load(sid)[0] == canonical
                assert base64.b64encode(self.payload).decode() not in json.dumps(canonical)
                self.pending_data[id(actual)] = (host, raw, canonical)

                async def cleanup_probe():
                    self.cleanups.append((actual, id(actual) in self.python_exits))
                coordinator.register_cleanup(cleanup_probe)
                physical_close = host.store.close

                def close():
                    assert id(actual) in self.python_exits
                    binding.check()
                    physical_close()
                    self.physical_closes.append(actual)
                self.monkeypatch.setattr(host.store, "close", close)
                return "finite fixture result"
            finally:
                if not seed:
                    self.held.set()  # Real selected Python finally, BEFORE cancellation.
                    await self.release.wait()
                self.python_exits.append(id(actual))
                if not seed:
                    self.exit_seen.set()

        selected = SimpleNamespace(execute=execute)
        self.selected[id(actual)] = selected
        actual.coordinator.loader = Loader(selected)
        return actual

    async def raw_parent(self):
        sid = "parent"
        self.allocate(sid)
        actual = self.factory(deepcopy(PLAN), session_id=sid)
        native = SessionStore(self.parent_dir, canonical_binding=self.bindings[sid])
        install_session_hook_graph(actual, owner="exit-fixture.parent", host_participants=("host.media",))
        install_session_checkpoint(actual, native, session_id=sid, canonical_admit=self.admit(sid))
        install_session_media(actual, native)
        await actual.initialize()
        await mount_session_media(actual)
        seal_session_hook_graph(actual)
        self.parent = actual
        return actual

    async def spawn(self):
        return await spawner.spawn_sub_session(
            "named", "inspect fixture", self.parent, {"named": {}}, sub_session_id="child",
            canonical_binding=self.bindings["child"], canonical_admit=self.admit("child"),
        )

    async def start(self, route):
        self.target = "root" if route in {"headless", "interactive"} else "child"
        if route in {"spawn", "resume"}:
            await self.raw_parent()
            self.allocate("child")
            if route == "resume":
                self.seed = True
                result = await self.spawn()
                assert result["output"] == "finite fixture result"
                selected = self.bindings["child"].expected
                assert binding_custody(self.bindings["child"]) is None
                with pytest.raises(VersionedSessionConflict, match="closed"):
                    self.bindings["child"].check()
                self.bindings["child"] = VersionedSessionBinding.resume(
                    self.parent_dir / "child", session_id="child", expected=selected,
                )
                self.groups["child"] = []
                self.seed = False
        else:
            self.allocate("root")
        if route == "spawn":
            return self.spawn()
        if route == "resume":
            return spawner.resume_sub_session(
                "child", "inspect fixture", parent_session=self.parent,
                canonical_binding=self.bindings["child"], canonical_admit=self.admit("child"),
            )
        prepared = PreparedBundle(deepcopy(PLAN), MagicMock(), Bundle(name="exit-fixture"))
        common = dict(config=deepcopy(PLAN), search_paths=[], verbose=False, session_id="root",
                      bundle_name="exit-fixture", prepared_bundle=prepared,
                      canonical_binding=self.bindings["root"], canonical_admit=self.admit("root"))
        if route == "headless":
            return self.main.execute_single(prompt="inspect fixture", **common)
        return self.main.interactive_chat(**common)

    async def capture(self, awaitable):
        try:
            return await awaitable
        except asyncio.CancelledError as error:
            self.observe_cancel.append(error)
            raise

    def assert_pending(self, actual):
        binding = self.bindings[self.target]
        host, raw, rows = self.pending_data[id(actual)]
        assert not host.closed and not host.cleanup_attempted and not host.cleanup_settled
        assert host.native._media_stores[self.target] is host.store
        assert host.wrappers and actual.coordinator.get("providers")["exit-provider"] is not None
        assert binding_custody(binding) is not None
        binding.check()
        with pytest.raises(VersionedSessionConflict, match="retains the source lease"):
            VersionedSessionBinding.resume(binding.directory, session_id=self.target, expected=binding.expected)
        assert actual not in self.physical_closes
        assert not any(s is actual for s, _ in self.cleanups)
        assert id(actual) not in self.python_exits
        assert host.native.load(self.target)[0] == rows
        assert actual.coordinator.get_capability("cli.session_execution").invocation.entered
        assert not actual.coordinator.get_capability("cli.session_execution").invocation.exited.done()
        return (snapshot(binding.directory), len(self.admissions), len(raw.observed), binding.expected,
                binding.last_receipt)


@pytest.mark.parametrize("route", ["headless", "interactive", "spawn", "resume"])
@pytest.mark.parametrize("cancel_count,image_format", [(1, "PNG"), (2, "JPEG")])
async def test_actual_public_caller_cancellation_joins_selected_python_finally_before_cleanup(
    tmp_path, monkeypatch, route, cancel_count, image_format,
):
    rig = Callers(tmp_path, monkeypatch, image_format)
    task = None
    retained_interactive = route == "interactive" and cancel_count == 2
    retained_host = None
    try:
        awaitable = await rig.start(route)
        task = asyncio.create_task(rig.capture(awaitable))
        await bounded(rig.held.wait())
        actual = next(s for s in reversed(rig.created) if s.session_id == rig.target)
        baseline = rig.assert_pending(actual)
        rig.cancelled_targets.add(rig.target)
        assert task.cancel("outer-first-cancel")
        await asyncio.sleep(0.05)
        assert not task.done()
        assert rig.assert_pending(actual) == baseline
        if cancel_count == 2:
            assert task.cancel("outer-second-cancel")
            if retained_interactive:
                done, _ = await asyncio.wait({task}, timeout=5)
                assert task in done, "second interactive cancellation did not report retained cleanup"
            else:
                await asyncio.sleep(0.05)
            assert task.done() is retained_interactive
            assert rig.assert_pending(actual) == baseline
            if retained_interactive:
                current = binding_custody(rig.bindings[rig.target])
                assert current is not None and current.error is not None
        # A competing opposite-role adoption must refuse synchronously before
        # construction or physical close, even though outer cancellation ran.
        count = len(rig.created)
        if route in {"headless", "interactive"}:
            with pytest.raises(RuntimeError, match="custody"):
                await spawner.resume_sub_session(
                    rig.target, "must not execute", parent_session=rig.parent,
                    canonical_binding=rig.bindings[rig.target], canonical_admit=rig.admit(rig.target),
                )
        else:
            prepared = PreparedBundle(deepcopy(PLAN), MagicMock(), Bundle(name="opposite-root"))
            with pytest.raises(RuntimeError, match="custody"):
                await create_initialized_session(SessionConfig(
                    config=deepcopy(PLAN), search_paths=[], verbose=False, session_id=rig.target,
                    prepared_bundle=prepared, canonical_binding=rig.bindings[rig.target],
                    canonical_admit=rig.admit(rig.target),
                ), Console(file=StringIO()))
        assert len(rig.created) == count
        assert rig.assert_pending(actual) == baseline
        rig.release.set()
        await bounded(rig.exit_seen.wait())
        done, _ = await asyncio.wait({task}, timeout=5)
        assert task in done, "actual public caller did not settle after selected Python exit"
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await task
        assert cancelled.value is rig.observe_cancel[0]
        assert cancelled.value.args == (
            "outer-second-cancel" if retained_interactive else "outer-first-cancel",
        )
        host, raw, canonical = rig.pending_data[id(actual)]
        assert rig.entries.count(actual) == 1
        assert rig.python_exits.count(id(actual)) == 1
        binding = rig.bindings[rig.target]
        if retained_interactive:
            # A second cancellation interrupted the cleanup JOIN, not selected
            # Python. Actual exit is now observed, but cannot retrospectively
            # clear the retained ownership failure or imply successful close.
            retained_host = host
            owner = actual.coordinator.get_capability("cli.session_execution")
            assert owner.invocation.exited.done()
            assert owner.failure is not None
            assert not host.closed and not host.cleanup_attempted and not host.cleanup_settled
            assert host.native._media_stores[rig.target] is host.store
            assert host.wrappers and actual not in rig.physical_closes
            assert not any(s is actual for s, _ in rig.cleanups)
            current = binding_custody(binding)
            assert current is not None and current.error is cancelled.value
            binding.check()
            with pytest.raises(VersionedSessionConflict, match="retains the source lease"):
                VersionedSessionBinding.resume(binding.directory, session_id=rig.target, expected=binding.expected)
            assert snapshot(binding.directory) == baseline[0]
            assert len(rig.admissions) == baseline[1] and len(raw.observed) == baseline[2]
            assert binding.expected == baseline[3] and binding.last_receipt is baseline[4]
            return  # Deliberate retained uncertainty, NOT a release qualification.
        assert rig.physical_closes.count(actual) == 1
        assert [(s, exited) for s, exited in rig.cleanups if s is actual] == [(actual, True)]
        assert host.closed and host.cleanup_attempted and host.cleanup_settled
        assert host.native._media_stores == {} and host.wrappers == {}
        assert binding_custody(binding) is None
        with pytest.raises(VersionedSessionConflict, match="closed"):
            binding.check()
        selected = prefix(binding.directory)
        # Headless may perform its documented failed-turn final save, but only
        # AFTER the actual selected finally exits. No saved rows may be erased.
        reader = SessionStore(rig.parent_dir)
        assert reader.load(rig.target)[0] == canonical
        after_exit_saves = [(saved_prefix, rows) for session, exited, saved_prefix, rows in rig.save_phases
                            if session is actual and exited]
        assert all(rows == canonical for _, rows in after_exit_saves)
        if selected != baseline[3]:
            assert after_exit_saves and selected == after_exit_saves[-1][0]
        before_reopen = snapshot(binding.directory)
        reopened = VersionedSessionBinding.resume(binding.directory, session_id=rig.target, expected=selected)
        try:
            assert reopened.load_messages() == canonical
            assert snapshot(binding.directory) == before_reopen
        finally:
            reopened.close()
    finally:
        # Failure-only fixture recovery does not count as caller qualification.
        # Release the synthetic wait; never leave a running Python orchestrator.
        rig.release.set()
        if task is not None and not task.done():
            done, _ = await asyncio.wait({task}, timeout=5)
            if task not in done:
                pytest.fail("fixture caller remains pending after explicit gate release")
        if task is not None and task.done() and not task.cancelled():
            task.exception()
        if rig.parent is not None:
            await cleanup_session_media_owner(rig.parent)
        if retained_host is not None:
            # Isolated failure-case fixture recovery only. This is NOT host
            # cleanup, does not reset witnesses/custody, and is never counted
            # as successful release in the assertions above.
            retained_host.store.close()
        for binding in rig.bindings.values():
            binding.close()  # isolated fixture cleanup only, never proof above.
