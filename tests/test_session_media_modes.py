"""CLI-owned media graph transitions through the installed modes participants.

The controlled loader supplies only offline context/provider/loop fixtures and
calls the real installed ModeTool/ModeHooks mounts for the declared modules.
Both ModeTool.execute and CommandProcessor._handle_mode use the actual owner.
This is host wiring regression coverage, NOT entry-point/source validation,
live-provider qualification, or a claim about arbitrary third-party callbacks.
"""

import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from functools import partial
from io import StringIO
import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from amplifier_core import AmplifierSession, HookResult, ModuleCoordinator
from amplifier_core.message_models import ChatRequest, ImageBlock, Message
from amplifier_foundation import (
    RUNTIME_CONTEXT_OVERLAY_CAPABILITY,
    RuntimeOverlay,
    TransitionResult,
)
from amplifier_foundation.bundle import Bundle, PreparedBundle
from amplifier_foundation.configurator import SessionConfigurator
from amplifier_foundation.session.media_hook_bridge import MediaHookBridge
from amplifier_foundation.session.media_provider import MediaPreparedProvider
import amplifier_module_hooks_mode as hooks_mode
import amplifier_module_tool_mode as tool_mode

from amplifier_app_cli.main import CommandProcessor
from amplifier_app_cli.session_hooks import (
    seal_session_hook_graph,
    session_hook_coordinator,
)
from amplifier_app_cli.session_runner import SessionConfig, create_initialized_session
from amplifier_app_cli.session_store import SessionStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.name != "posix", reason="POSIX durable media acknowledgement"),
]

LIFECYCLE = ("mode:activated", "mode:changed", "mode:cleared")
# Static synthetic 2x3 rasters, never a desktop capture or external input.
RASTERS = {
    "PNG": base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAFElEQVR4nGMUDN7PwMDA"
        "xMDAgKAAFT8BKTIzlqEAAAAASUVORK5CYII="
    ),
    "JPEG": base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
        "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
        "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
        "MjIyMjIyMjL/wAARCAADAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQF"
        "BgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0"
        "KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZn"
        "aGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
        "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAA"
        "AAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMi"
        "MoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldY"
        "WVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6"
        "wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi6KKK"
        "+6Pmj//Z"
    ),
}


class _Context:
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


class _OfflineProvider:
    name = "mode-fixture-provider"
    default_model = "synthetic-vision"

    def __init__(self):
        self.observed = []

    def get_media_request_support(self, *, model, request_options):
        return {"v": 1, "positions": ["user", "tool"]}

    def _observe(self, phase, request):
        pixels = [
            base64.b64decode(block.source["data"], validate=True)
            for message in request.messages if isinstance(message.content, list)
            for block in message.content if isinstance(block, ImageBlock)
        ]
        self.observed.append((phase, request, pixels))

    async def request_budget(self, request, *, request_options=None):
        self._observe("count", request)
        return {
            "estimated_input_tokens": 10, "input_limit_tokens": 100,
            "context_token_budget": 100,
            "measurement": {
                "kind": "provider_count", "source": "offline-mode-fixture", "input_tokens": 10,
            },
        }

    async def complete(self, request):
        self._observe("send", request)
        return "offline result"


async def _no_inference(*args, **kwargs):
    raise AssertionError("Mode graph checks must not execute an orchestrator")


class _ControlledLoader:
    """No installation/discovery: real mode mounts, explicitly synthetic remainder."""

    def __init__(self):
        self.queue = []
        self.real_mounts = {}

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        if module_id in {"tool-mode", "hooks-mode"}:
            module = tool_mode if module_id == "tool-mode" else hooks_mode
            mount = partial(module.mount, config=config)
            self.real_mounts[module_id] = (mount, coordinator)
            return mount

        async def mount(current):
            if module_id == "mode-fixture-loop":
                await current.mount("orchestrator", SimpleNamespace(execute=_no_inference))
            elif module_id == "mode-fixture-context":
                await current.mount("context", _Context())
            elif module_id == "mode-fixture-provider":
                await current.mount("providers", _OfflineProvider(), name=module_id)
            else:
                raise AssertionError(f"Unexpected module: {module_id}")
        return mount

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


class _RootProbe:
    def __init__(self, initialized, native, loader):
        self.initialized = initialized
        self.session = initialized.session
        self.native = native
        self.loader = loader
        self.coordinator = self.session.coordinator
        self.host = self.coordinator.get_capability("cli.session_media")
        self.bridge = self.coordinator.get_capability("session.media.tool_post")
        self.graph = self.coordinator.get_capability("cli.hooks.participating_graph")
        self.tool = self.coordinator.get("tools").get("mode")
        self.mode_hooks = self.coordinator.session_state.get("mode_hooks")
        self.processor = CommandProcessor(self.session, "mode-fixture")
        self.processor.configurator = initialized.configurator
        self.view = session_hook_coordinator(self.session, "host.mode")
        self.events = []
        self.posts = []
        self.remove_post = None
        self.fault = None
        self.fault_event = None
        for event in LIFECYCLE:
            self.view.hooks.register(event, self._contribute, priority=100, name=f"host-{event}")
        seal_session_hook_graph(self.session)

    def _contribute(self, event, data):
        """A second REAL enrolled host owner, not a replacement ModeHooks callback."""
        target = None if event == "mode:cleared" else data.get("new", data.get("name"))
        self.events.append((event, target))
        self.view.config["mode_fixture_active"] = target
        if self.remove_post:
            self.remove_post()
            self.remove_post = None
        if target is not None:
            def post(_event, _data):
                self.posts.append(target)
                return HookResult()
            self.remove_post = self.view.hooks.register(
                "tool:post", post, name=f"mode-fixture-post-{target}", priority=90,
            )
        if event == self.fault_event and self.fault is not None:
            raise self.fault
        return HookResult()

    def evidence(self):
        return self.graph.construction.participants_evidence()

    async def add_pixels(self, image_format):
        self.payload = RASTERS[image_format]
        origin = {"kind": "tool", "session_id": "mode-root", "id": "synthetic-raster"}
        ingested = await self.host.capability.ingest(
            self.payload, source_format="image-bytes", origin=origin, policy=None,
        )
        self.ref = ingested["value"]
        outcome = await self.bridge({
            "tool_name": "synthetic-raster", "tool_call_id": origin["id"],
            "result": {"refs": [self.ref]},
        })
        assert outcome["complete"] and outcome["result"]["refs"] == [self.ref]
        await self.host.capability.publish([self.ref], origin=origin, disposition="retain")
        self.context = self.coordinator.get("context")
        await self.context.add_message({
            "role": "user", "content": "Synthetic, private canonical observation",
            "metadata": {"experimental.media.v1": {"refs": [self.ref]}},
        })
        # Actual CLI native save -> Foundation verification -> live store ACK.
        self.ack = await self.coordinator.get_capability("session.durable_checkpoint")()
        assert self.ack is not None
        self.canonical = await self.context.get_messages()
        assert self.native.load("mode-root")[0] == self.canonical
        self.request = ChatRequest(messages=[Message(**row) for row in self.canonical])
        self.request_before = self.request.model_dump(mode="json")
        self.wrapped = self.coordinator.get("providers")["mode-fixture-provider"]
        assert isinstance(self.wrapped, MediaPreparedProvider)
        self.raw = next(raw for raw, wrapped in self.host.wrappers.values() if wrapped is self.wrapped)

    async def unchanged_canonical(self):
        assert await self.context.get_messages() == self.canonical
        assert self.native.load("mode-root")[0] == self.canonical
        assert self.request.model_dump(mode="json") == self.request_before
        serialized = json.dumps(self.canonical)
        assert base64.b64encode(self.payload).decode() not in serialized
        assert "data:image/" not in serialized
        assert "\"type\": \"image\"" not in serialized

    async def finish_counted(self):
        counted = self.raw.observed[-1]
        assert counted[0] == "count" and counted[1] is not self.request
        assert await self.wrapped.complete(self.request) == "offline result"
        sent = self.raw.observed[-1]
        assert sent[0] == "send" and counted[1] is sent[1]
        assert counted[2] == sent[2] == [self.payload]
        await self.unchanged_canonical()

    async def count_and_send(self):
        await self.wrapped.request_budget(self.request)
        await self.finish_counted()


@contextmanager
def _observe_real_mode_verify(root):
    """Observe, never replace, the installed tracker's actual body failure.

    A privacy-safe native callback can complete while its tracked operation
    failed. Public contribution evidence describes callbacks, not verify().
    Retain the actual verify entry/exception to distinguish these two facts.
    """
    entries = []
    original_verify = hooks_mode.ModeTransition.verify

    def observed_verify(operation):
        entry = {
            "operation": operation,
            "error": operation.error,
            "completed": operation.completed,
            "graph_state": root.evidence()["state"],
            "receipt": root.bridge.graph_receipt,
            "raised": None,
        }
        entries.append(entry)
        try:
            return original_verify(operation)
        except BaseException as error:
            entry["raised"] = error
            raise

    with patch.object(hooks_mode.ModeTransition, "verify", new=observed_verify):
        yield entries


@pytest.fixture
def mode_root(tmp_path, monkeypatch):
    """Public CLI root; isolate external settings/home/native storage only."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    modes = tmp_path / "modes"
    modes.mkdir()
    for name in ("alpha", "beta"):
        context = tmp_path / f"{name}-context.txt"
        context.write_text(f"synthetic contributed context for {name}", encoding="utf-8")
        (modes / f"{name}.md").write_text(
            "---\nmode:\n"
            f"  name: {name}\n  description: synthetic {name} mode\n"
            "  default_action: allow\n  contributes:\n"
            f"    context: [{json.dumps(str(context))}]\n"
            f"---\nSynthetic {name} mode guidance.\n",
            encoding="utf-8",
        )

    @asynccontextmanager
    async def create(*, gate="auto", mode_modules=True):
        native = SessionStore(tmp_path / "native")
        plan = {
            "session": {"orchestrator": "mode-fixture-loop", "context": "mode-fixture-context"},
            "providers": [{"module": "mode-fixture-provider"}],
        }
        if mode_modules:
            plan["tools"] = [{"module": "tool-mode", "config": {"gate_policy": gate}}]
            plan["hooks"] = [{"module": "hooks-mode", "config": {"search_paths": [str(modes)]}}]
        prepared = PreparedBundle(deepcopy(plan), MagicMock(), Bundle(name="mode-fixture"))
        config = SessionConfig(
            config=deepcopy(plan), search_paths=[], verbose=False, session_id="mode-root",
            prepared_bundle=prepared, bundle_name="mode-fixture", shared_root=False,
            invocation_mode="single",
        )
        created = []
        loader = _ControlledLoader()

        def actual_core(*args, **kwargs):
            session = AmplifierSession(*args, **kwargs)
            session.coordinator.loader = loader
            created.append(session)
            return session

        settings = MagicMock()
        settings.with_session.return_value.get_merged_settings.return_value = {"configurator": {}}
        with (
            patch("amplifier_core.AmplifierSession", side_effect=actual_core),
            patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
            patch("amplifier_app_cli.session_runner.SessionStore", return_value=native),
            patch("amplifier_app_cli.session_runner.AppSettings", return_value=settings),
            patch("amplifier_app_cli.paths.create_foundation_resolver"),
            patch("amplifier_app_cli.runtime.config.inject_user_providers"),
        ):
            initialized = await create_initialized_session(config, Console(file=StringIO()))
        try:
            assert created == [initialized.session]  # No hidden self-healing/replayed construction.
            assert type(initialized.session.coordinator) is ModuleCoordinator
            assert isinstance(initialized.configurator, SessionConfigurator)
            probe = _RootProbe(initialized, native, loader)
            assert isinstance(probe.bridge, MediaHookBridge)
            if mode_modules:
                assert isinstance(probe.tool, tool_mode.ModeTool)
                assert isinstance(probe.mode_hooks, hooks_mode.ModeHooks)
            else:
                assert probe.tool is None and probe.mode_hooks is None
                assert not loader.real_mounts
            assert probe.coordinator.get_capability("session.media.graph_transition") is (
                probe.coordinator.get_capability("cli.media.graph_transition")
            )
            assert probe.bridge.validate_receipt(probe.bridge.graph_receipt)
            loads = {row["module"]: row for row in probe.evidence()["loads"]}
            modules = (("tool-mode", tool_mode), ("hooks-mode", hooks_mode)) if mode_modules else ()
            for module_id, module in modules:
                mount, view = loader.real_mounts[module_id]
                assert mount.func is module.mount
                assert loads[module_id]["mount"] is mount and loads[module_id]["mounted"]
                assert view is not probe.coordinator  # Actual declared-participant view supplied by CLI installer.
            registrations = probe.evidence()["mode_registrations"]
            actual_callbacks = {
                "mode:activated": probe.mode_hooks.handle_mode_activated,
                "mode:changed": probe.mode_hooks.handle_mode_changed,
                "mode:cleared": probe.mode_hooks.handle_mode_cleared,
            } if mode_modules else {}
            for event, callback in actual_callbacks.items():
                assert any(row["event"] == event and row["participant"] == "hooks-mode"
                           and row["original"] == callback for row in registrations)
            yield probe
        finally:
            await initialized.cleanup()

    yield create


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
@pytest.mark.parametrize("entrypoint", ["tool", "command"])
async def test_actual_mode_lifecycle_reseals_all_owners_and_preserves_private_candidate(
    mode_root, image_format, entrypoint,
):
    async with mode_root() as root:
        await root.add_pixels(image_format)
        await root.count_and_send()
        receipts = [root.bridge.graph_receipt]
        configurator = root.initialized.configurator
        for event, target in zip(LIFECYCLE, ("alpha", "beta", None)):
            before_config = deepcopy(root.session.config)
            before_active = root.coordinator.session_state["active_mode"]
            before_events = list(root.events)
            generation = root.host.generation
            overlay = root.coordinator.session_state.get("mode_runtime_overlay")
            before_overlay = overlay.dump_state() if overlay else None
            await root.wrapped.request_budget(root.request)
            counted = root.raw.observed[-1]
            args = {"operation": "clear"} if target is None else {"operation": "set", "name": target}
            command = "off" if target is None else f"{target} on"
            if entrypoint == "command":
                # CLI read/no-op paths must not open a transition, including
                # while this real counted candidate retains its request scope.
                inactive = "alpha" if before_active != "alpha" else "beta"
                no_ops = [
                    ("", f"Active mode: {before_active}" if before_active else "No mode active"),
                    ("info", "Usage:"),
                    ("info alpha", "synthetic alpha mode"),
                    ("missing-mode on", "Unknown mode:"),
                    (f"{inactive} off", f"Not in {inactive} mode"),
                    ((f"{before_active} on", f"Already in {before_active} mode")
                     if before_active else ("off", "No mode active")),
                ]
                for no_op, expected in no_ops:
                    assert expected in await root.processor._handle_mode(no_op)
                    assert root.bridge.graph_receipt is receipts[-1]
                    assert root.bridge.validate_receipt(receipts[-1])
                    assert root.session.config == before_config
                    assert root.coordinator.session_state["active_mode"] == before_active
                    assert root.events == before_events
                    assert root.host.generation == generation
                    assert root.raw.observed[-1] is counted
            # Real entry point must refuse BEFORE events/config/overlay effects while the
            # actual MediaPreparedProvider's counted candidate owns its graph scope.
            if entrypoint == "tool":
                refused = await root.tool.execute(args)
                assert refused.success is False and refused.error["code"] == "internal_error"
            else:
                with pytest.raises(ValueError, match="active leases"):
                    await root.processor._handle_mode(command)
            assert root.events == before_events
            assert root.session.config == before_config
            assert root.coordinator.session_state["active_mode"] == before_active
            assert root.coordinator.session_state.get("mode_runtime_overlay") is overlay
            assert (overlay.dump_state() if overlay else None) == before_overlay
            assert root.host.generation == generation
            assert root.bridge.graph_receipt is receipts[-1]
            assert root.bridge.validate_receipt(receipts[-1])
            assert "mode_transition_failure" not in root.coordinator.session_state
            assert root.raw.observed[-1] is counted
            await root.finish_counted()  # Exactly the same private counted request is sent.

            if entrypoint == "tool":
                assert (await root.tool.execute(args)).success is True
            else:
                result = await root.processor._handle_mode(command)
                assert result == (
                    f"Mode: {target} — synthetic {target} mode" if target
                    else f"Mode off: {before_active}"
                )
            assert root.events == before_events + [(event, target)]
            assert root.session.config["mode_fixture_active"] == target
            assert root.coordinator.session_state["active_mode"] == target
            assert root.host.generation != generation
            assert root.initialized.configurator is configurator  # Root owner is never replaced.
            assert root.processor.configurator is configurator
            receipt = root.bridge.graph_receipt
            assert receipt is not None and root.bridge.validate_receipt(receipt)
            assert all(not root.bridge.validate_receipt(old) for old in receipts)
            receipts.append(receipt)
            contributions = root.evidence()["contributions"]
            for owner in ("hooks-mode", "host.mode"):
                assert any(row["event"] == event and row["kind"] == "callback"
                           and row["participant"] == owner and row["state"] == "completed"
                           for row in contributions)
            overlay = root.coordinator.session_state.get("mode_runtime_overlay")
            paths = root.coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY)
            if target:
                assert isinstance(overlay, RuntimeOverlay)
                assert set(overlay.dump_state()["scope_claims"]) == {f"mode:{target}"}
                assert len(paths) == 1 and paths[0].endswith(f"{target}-context.txt")
            else:
                assert overlay is None and not paths
            posts_before = len(root.posts)
            outcome = await root.bridge({
                "tool_name": "offline-probe", "tool_call_id": f"after-{event}",
                "result": {"unchanged": True},
            })
            assert outcome["complete"] and outcome["result"] == {"unchanged": True}
            assert root.posts[posts_before:] == ([target] if target else [])
            await root.count_and_send()
            assert root.bridge.validate_receipt(receipt)


@pytest.mark.parametrize("gate", ["warn", "confirm"])
async def test_actual_mode_read_validation_and_gate_denials_preserve_counted_receipt(mode_root, gate):
    async with mode_root(gate=gate) as root:
        await root.add_pixels("PNG")
        before_config = deepcopy(root.session.config)
        receipt = root.bridge.graph_receipt
        await root.wrapped.request_budget(root.request)
        for args, success in (
            ({"operation": "list"}, True),
            ({"operation": "current"}, True),
            ({"operation": "invalid"}, False),
            ({"operation": "set"}, False),
            ({"operation": "set", "name": "missing-mode"}, False),
            ({"operation": "set", "name": "alpha"}, False),
            ({"operation": "clear"}, False),
        ):
            result = await root.tool.execute(args)
            assert result.success is success
            assert root.bridge.graph_receipt is receipt and root.bridge.validate_receipt(receipt)
            assert root.session.config == before_config and not root.events
            assert root.coordinator.session_state["active_mode"] is None
            assert "mode_transition_failure" not in root.coordinator.session_state
        if gate == "warn":
            # Warn retry is eligible but still cannot bypass the live count lease.
            denied = await root.tool.execute({"operation": "set", "name": "alpha"})
            assert not denied.success and denied.error["code"] == "internal_error"
            assert root.bridge.graph_receipt is receipt and root.bridge.validate_receipt(receipt)
            assert root.session.config == before_config and not root.events
        await root.finish_counted()
        result = await root.tool.execute({"operation": "set", "name": "alpha"})
        assert result.success is (gate == "warn")
        if gate == "warn":
            assert not root.bridge.validate_receipt(receipt)
            assert root.bridge.validate_receipt(root.bridge.graph_receipt)
            await root.count_and_send()
        else:
            assert root.bridge.graph_receipt is receipt and root.bridge.validate_receipt(receipt)
            assert root.session.config == before_config and not root.events


@pytest.mark.parametrize("event", LIFECYCLE)
@pytest.mark.parametrize("failure", ["external-error", "external-cancel", "overlay-false", "overlay-error"])
@pytest.mark.parametrize("entrypoint", ["tool", "command"])
async def test_actual_mode_failure_invalidates_successor_and_latches_without_replay(
    mode_root, event, failure, entrypoint,
):
    async with mode_root() as root:
        await root.add_pixels("PNG")
        if event != "mode:activated":
            if entrypoint == "tool":
                assert (await root.tool.execute({"operation": "set", "name": "alpha"})).success
            else:
                assert await root.processor._handle_mode("alpha on") == "Mode: alpha — synthetic alpha mode"
        await root.count_and_send()
        receipt = root.bridge.graph_receipt
        predecessor = root.graph.construction
        previous_mode = root.coordinator.session_state["active_mode"]
        args = ({"operation": "clear"} if event == "mode:cleared" else
                {"operation": "set", "name": "beta" if event == "mode:changed" else "alpha"})
        target = args.get("name")
        command = "off" if target is None else f"{target} on"
        events_before_failure = list(root.events)
        root.fault_event = event
        root.fault = asyncio.CancelledError("synthetic cancellation") if failure == "external-cancel" else (
            RuntimeError("synthetic external contribution failure") if failure == "external-error" else None
        )
        overlay = root.mode_hooks._get_or_create_overlay()
        assert isinstance(overlay, RuntimeOverlay)
        method = "revoke" if event == "mode:cleared" else "apply"
        original = getattr(overlay, method)
        calls = []
        overlay_error = RuntimeError("synthetic overlay exception")

        async def faulted_overlay(scope, *values):
            calls.append(scope)
            if failure == "overlay-false":
                return TransitionResult(success=False, scope=scope, error="synthetic overlay refusal")
            if failure == "overlay-error":
                raise overlay_error
            return await original(scope, *values)

        with (
            patch.object(overlay, method, side_effect=faulted_overlay),
            _observe_real_mode_verify(root) as verifications,
        ):
            # Actual Core converts even an external callback's cancellation into
            # a handler failure. The issuer must still reject the successor;
            # this must not rely on a fake event bus propagating the exception.
            if entrypoint == "tool":
                result = await root.tool.execute(args)
                assert not result.success and result.error["code"] == "internal_error"
            else:
                # Internal callback outcomes reach the modes-owned tracker;
                # external callback errors/cancellation are swallowed by real
                # Core and detected by the issuer when sealing the successor.
                expected = RuntimeError if failure.startswith("overlay-") else ValueError
                message = (
                    f"Mode contribution {method} failed" if failure == "overlay-false"
                    else "synthetic overlay" if failure == "overlay-error"
                    else "graph cannot seal"
                )
                with pytest.raises(expected, match=message) as caught:
                    await root.processor._handle_mode(command)
                if failure == "overlay-error":
                    assert caught.value is overlay_error
            assert len(calls) == 1
            assert root.events == events_before_failure + [(event, target)]
            assert not root.bridge.validate_receipt(receipt)
            assert root.graph.construction is not predecessor
            assert root.bridge.graph_receipt is None
            assert root.evidence()["state"] == "invalid"
            assert len(verifications) == 1
            verification = verifications[0]
            assert verification["receipt"] is None
            callbacks = [
                row for row in root.evidence()["contributions"]
                if row["event"] == event and row["kind"] == "callback"
            ]
            if failure.startswith("overlay-"):
                assert verification["graph_state"] == "open"  # Still inside host body.
                assert verification["completed"] is False
                assert verification["raised"] is verification["error"]
                if failure == "overlay-error":
                    assert verification["error"] is overlay_error
                else:
                    assert str(verification["error"]) == f"Mode contribution {method} failed"
                # ModeHooks safely returned CONTINUE so Core never logs the
                # private exception; verify(), not the native callback, fails.
                assert callbacks and all(row["state"] == "completed" for row in callbacks)
            else:
                assert verification["error"] is None and verification["raised"] is None
                assert verification["completed"] is True
                assert any(row["participant"] == "host.mode" and row["state"] == "failed"
                           for row in callbacks)
            state = root.coordinator.session_state
            if entrypoint == "tool":
                assert state["active_mode"] == previous_mode
                assert state["mode_transition_failure"]["previous_mode"] == previous_mode
                assert state["mode_transition_failure"]["target_mode"] == target
            else:
                assert state["active_mode"] is None
                assert state["mode_transition_failure"] == {
                    "source": "cli.command",
                    "target_mode": target or "off",
                    "reason": "participating-mode-operation-did-not-settle",
                }
            # Neither current/list nor moderation may advertise an operational mode.
            assert not (await root.tool.execute({"operation": "current"})).success
            assert not (await root.tool.execute({"operation": "list"})).success
            moderation = await root.mode_hooks.handle_tool_pre("tool:pre", {"tool_name": "ordinary"})
            assert moderation.action == "deny"
            before_events = list(root.events)
            before_config = deepcopy(root.session.config)
            before_overlay = overlay.dump_state()
            before_failure = deepcopy(state["mode_transition_failure"])
            if entrypoint == "tool":
                repeat = await root.tool.execute(args)
                assert not repeat.success and repeat.error["code"] == "internal_error"
            else:
                # A latched participating command is not an inactive mode.
                # Even off/info must report recovery rather than advertise
                # successful clearing, revive old state, or replay contributions.
                for attempted in ("alpha on", "beta on", "alpha", "beta", "off", "", "info alpha"):
                    with pytest.raises(RuntimeError, match="requires host recovery"):
                        await root.processor._handle_mode(attempted)
                assert state["active_mode"] is None
                assert state["mode_transition_failure"] == before_failure
                assert root.bridge.graph_receipt is None
                assert not root.bridge.validate_receipt(receipt)
            assert len(calls) == 1 and root.events == before_events
            assert root.session.config == before_config and overlay.dump_state() == before_overlay
        before_observed = list(root.raw.observed)
        with pytest.raises(ValueError, match="graph"):
            await root.wrapped.request_budget(root.request)
        with pytest.raises(ValueError, match="graph"):
            await root.wrapped.complete(root.request)
        assert root.raw.observed == before_observed  # No downstream count/send after failed graph.
        await root.unchanged_canonical()


@pytest.mark.parametrize("event", LIFECYCLE)
@pytest.mark.parametrize("entrypoint", ["tool", "command"])
@pytest.mark.parametrize("outcome", ["false", "error", "cancel"])
@pytest.mark.parametrize("notification_fails", [False, True])
async def test_participating_modes_keep_private_diagnostics_out_of_event_and_logger_entry(
    mode_root, event, entrypoint, outcome, notification_fails,
):
    """Only Modes' participating boundary; not RuntimeOverlay's own logs.

    Replace the failing overlay method itself. Inspect event payloads and raw
    Logger._log arguments AT ENTRY, before serialization/formatting could hide
    an exception, traceback, hostile error object, note, or chain.
    """
    private = "PRIVATE-MODE-DIAGNOSTIC-SENTINEL"
    conversions = []

    class PrivateError(RuntimeError):
        def __str__(self):
            conversions.append("error-str")
            return private

        def __repr__(self):
            conversions.append("error-repr")
            return private

    class PrivateCancellation(asyncio.CancelledError):
        def __str__(self):
            conversions.append("cancel-str")
            return private

        def __repr__(self):
            conversions.append("cancel-repr")
            return private

    class PrivateResultError:
        def __str__(self):
            conversions.append("result-str")
            return private

        def __repr__(self):
            conversions.append("result-repr")
            return private

    primary = PrivateCancellation(private) if outcome == "cancel" else PrivateError(private)
    primary.__cause__ = ValueError(f"{private}-cause")
    primary.__context__ = ValueError(f"{private}-context")
    BaseException.add_note(primary, f"{private}-note")
    secondary = PrivateError(f"{private}-notification")
    BaseException.add_note(secondary, f"{private}-notification-note")
    result_error = PrivateResultError()

    async with mode_root() as root:
        await root.add_pixels("PNG")
        if event != "mode:activated":
            assert await root.processor._handle_mode("alpha on") == "Mode: alpha — synthetic alpha mode"
        await root.count_and_send()
        predecessor = root.graph.construction
        receipt = root.bridge.graph_receipt
        overlay = root.mode_hooks._get_or_create_overlay()
        method = "revoke" if event == "mode:cleared" else "apply"
        target = None if event == "mode:cleared" else "beta" if event == "mode:changed" else "alpha"
        command = "off" if target is None else f"{target} on"
        args = {"operation": "clear"} if target is None else {"operation": "set", "name": target}
        mode_events, log_entries, calls = [], [], []
        native_emit = root.mode_hooks.coordinator.hooks.emit

        async def event_entry(name, payload):
            # Do not format a private object on a regression path.
            mode_events.append((name, payload))
            if name == "mode:activation_failed":
                assert payload == {
                    "code": "mode_transition_failed",
                    "error": "Participating mode contribution failed.",
                }
                if notification_fails:
                    raise secondary
            return await native_emit(name, payload)

        def logger_entry(logger, level, msg, arguments, exc_info=None, extra=None,
                         stack_info=False, stacklevel=1):
            log_entries.append({
                "name": logger.name, "level": level, "message": msg, "arguments": arguments,
                "exc_info": exc_info, "extra": extra, "stack_info": stack_info,
            })
            # No forwarding: this is the raw diagnostic entry boundary.

        async def overlay_outcome(scope, *values):
            calls.append(scope)
            if outcome == "false":
                # The diagnostic field is deliberately NOT a string. The
                # participating owner must never format or otherwise consume it.
                return TransitionResult(success=False, scope=scope, error=result_error)
            raise primary

        with (
            patch.object(overlay, method, side_effect=overlay_outcome),
            patch.object(root.mode_hooks.coordinator.hooks, "emit", side_effect=event_entry),
            patch.object(logging.Logger, "_log", new=logger_entry),
            _observe_real_mode_verify(root) as verifications,
        ):
            if outcome == "cancel":
                with pytest.raises(asyncio.CancelledError) as caught:
                    if entrypoint == "tool":
                        await root.tool.execute(args)
                    else:
                        await root.processor._handle_mode(command)
                assert caught.value is primary
            elif entrypoint == "tool":
                result = await root.tool.execute(args)
                assert not result.success and result.error["code"] == "internal_error"
                assert private not in json.dumps(result.model_dump(mode="json"))
            else:
                with pytest.raises(RuntimeError) as caught:
                    await root.processor._handle_mode(command)
                if outcome == "error":
                    assert caught.value is primary
                else:
                    assert str(caught.value) == f"Mode contribution {method} failed"

        assert len(calls) == 1 and len(verifications) == 1
        verification = verifications[0]
        assert verification["graph_state"] == "open" and verification["receipt"] is None
        assert verification["completed"] is False
        assert verification["raised"] is verification["error"]
        if outcome == "false":
            assert str(verification["error"]) == f"Mode contribution {method} failed"
        else:
            assert verification["error"] is primary
            assert primary.args == (private,)
            assert primary.__notes__ == [f"{private}-note"]
            assert primary.__cause__.args == (f"{private}-cause",)
            assert primary.__context__.args == (f"{private}-context",)
        assert root.graph.construction is not predecessor
        assert not root.bridge.validate_receipt(receipt) and root.bridge.graph_receipt is None
        assert root.evidence()["state"] == "invalid"
        callbacks = [row for row in root.evidence()["contributions"]
                     if row["event"] == event and row["kind"] == "callback"]
        assert callbacks and all(row["state"] == "completed" for row in callbacks)
        assert root.coordinator.session_state["mode_transition_failure"]
        assert root.coordinator.session_state["active_mode"] == (
            None if entrypoint == "command" else None if event == "mode:activated" else "alpha"
        )
        failure_events = [payload for name, payload in mode_events if name == "mode:activation_failed"]
        assert len(failure_events) == (0 if outcome == "cancel" else 1)
        for name, payload in mode_events:
            assert type(name) is str and type(payload) is dict
            assert private not in json.dumps(payload)
        allowed_logs = {
            "Could not emit participating mode failure notification",
            "Participating mode set failed.",
            "Participating mode clear failed.",
        }
        for entry in log_entries:
            assert entry["name"] in {hooks_mode.__name__, tool_mode.__name__}
            assert entry["message"] in allowed_logs
            assert not entry["arguments"] and entry["exc_info"] is None
            assert entry["stack_info"] is False and entry["extra"] is None
        expected_logs = int(notification_fails and outcome != "cancel") + int(
            entrypoint == "tool" and outcome != "cancel"
        )
        assert len(log_entries) == expected_logs
        assert conversions == []  # No str/repr of original, chain, or result.error.
        before_observed = list(root.raw.observed)
        with pytest.raises(ValueError, match="graph"):
            await root.wrapped.request_budget(root.request)
        with pytest.raises(ValueError, match="graph"):
            await root.wrapped.complete(root.request)
        assert root.raw.observed == before_observed
        await root.unchanged_canonical()


async def test_command_without_mode_hooks_keeps_legacy_host_contributor_lane(mode_root):
    """The new tracker must not be required when the modes module is absent."""
    async with mode_root(mode_modules=False) as root:
        assert "mode_hooks" not in root.coordinator.session_state
        await root.add_pixels("PNG")
        await root.count_and_send()
        receipts = [root.bridge.graph_receipt]
        for event, command, target, response in (
            ("mode:activated", "alpha on", "alpha", "Mode: alpha"),
            ("mode:changed", "beta on", "beta", "Mode: beta"),
            ("mode:cleared", "off", None, "Mode off: beta"),
        ):
            assert await root.processor._handle_mode(command) == response
            assert root.events[-1] == (event, target)
            assert root.session.config["mode_fixture_active"] == target
            assert root.coordinator.session_state["active_mode"] == target
            assert not root.coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY)
            assert "mode_hooks" not in root.coordinator.session_state
            assert "mode_transition_failure" not in root.coordinator.session_state
            receipt = root.bridge.graph_receipt
            assert root.bridge.validate_receipt(receipt)
            assert all(not root.bridge.validate_receipt(old) for old in receipts)
            receipts.append(receipt)
            posts_before = len(root.posts)
            outcome = await root.bridge({
                "tool_name": "legacy-post", "tool_call_id": event, "result": "kept",
            })
            assert outcome["complete"] and outcome["result"] == "kept"
            assert root.posts[posts_before:] == ([target] if target else [])
            await root.count_and_send()
