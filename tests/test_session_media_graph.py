"""Actual Core/PreparedBundle CLI graph fixtures; no network or vendor modules."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from io import StringIO
import io
import hashlib
import base64
import os

from amplifier_core import AmplifierSession, HookResult
from amplifier_core.message_models import ChatRequest, Message, ImageBlock
from PIL import Image
from amplifier_foundation.bundle import Bundle, PreparedBundle
# Import before the local factory patch: bridge type checks must see real Core.
from amplifier_foundation.session.media_hook_bridge import MediaHookBridge
from amplifier_app_cli.session_hooks import seal_session_hook_graph, session_hooks
from amplifier_app_cli.session_runner import SessionConfig, create_initialized_session
from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.asyncio


class Context:
    def __init__(self): self.rows = []
    async def add_message(self, row): self.rows.append(deepcopy(row))
    async def get_messages(self): return deepcopy(self.rows)
    async def set_messages(self, rows): self.rows[:] = deepcopy(rows)
    async def set_system_prompt_factory(self, factory): self.factory = factory


class Loader:
    def __init__(self, execute=None): self.queue, self.execute = [], execute
    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            if module_id == "fixture-loop":
                await current.mount("orchestrator", SimpleNamespace(execute=self.execute or AsyncMock(return_value="done")))
            elif module_id == "fixture-context":
                await current.mount("context", Context())
            elif module_id == "fixture-provider":
                await current.mount("providers", Provider(support=config.get("support", True)), name="fixture-provider")
            else:
                raise AssertionError(f"Unexpected fixture module: {module_id}")
        return mount
    def enqueue_on_session_ready(self, name, callback): self.queue.append((name, callback))
    def get_on_session_ready_queue(self): return self.queue
    def clear_on_session_ready_queue(self): self.queue.clear()


class Provider:
    name = "selected-fixture"
    default_model = "selected-vision"
    def __init__(self, *, support=True): self.observed, self.support = [], support
    def get_media_request_support(self, *, model, request_options):
        return {"v": 1, "positions": ["user", "tool"] if self.support else []}
    def observe(self, request):
        images = [
            hashlib.sha256(base64.b64decode(block.source["data"])).hexdigest()
            for message in request.messages if isinstance(message.content, list)
            for block in message.content if isinstance(block, ImageBlock)
        ]
        self.observed.append((request, images, str(request)))
    async def request_budget(self, request, *, request_options=None):
        self.observe(request)
        return {"estimated_input_tokens": 10, "input_limit_tokens": 100,
                "context_token_budget": 100, "measurement": {
                    "kind": "provider_count", "source": "fixture", "input_tokens": 10}}
    async def complete(self, request):
        self.observe(request)
        return "synthetic provider result"


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("image_format", [
    None,
    pytest.param("PNG", marks=pytest.mark.skipif(os.name != "posix", reason="POSIX media durability")),
    pytest.param("JPEG", marks=pytest.mark.skipif(os.name != "posix", reason="POSIX media durability")),
])
async def test_actual_root_preinit_configure_late_participants_and_receipt(tmp_path, resume, image_format):
    native = SessionStore(tmp_path)
    restored = [{"role": "user", "content": "saved observation"}] if resume else None
    if resume:
        native.save("root", restored, {})
    plan = {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}}
    if image_format:
        plan["providers"] = [{"module": "fixture-provider"}]
    else:
        plan["session_media"] = {"enabled": False}
    prepared = PreparedBundle(deepcopy(plan), MagicMock(), Bundle(name="fixture"))
    config = SessionConfig(config=deepcopy(plan), search_paths=[], verbose=False, session_id="root",
                           prepared_bundle=prepared, bundle_name="fixture", shared_root=False,
                           initial_transcript=restored, invocation_mode="chat")
    created = []
    def actual_factory(*args, **kwargs):
        actual = AmplifierSession(*args, **kwargs)
        actual.coordinator.loader = Loader()
        created.append(actual)
        return actual
    settings = MagicMock()
    settings.with_session.return_value.get_merged_settings.return_value = {"configurator": {}}
    with (
        patch("amplifier_core.AmplifierSession", side_effect=actual_factory),
        patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
        patch("amplifier_app_cli.session_runner.SessionStore", return_value=native),
        patch("amplifier_app_cli.session_runner.AppSettings", return_value=settings),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.runtime.config.inject_user_providers"),
    ):
        initialized = await create_initialized_session(config, Console(file=StringIO()))
    actual = initialized.session
    try:
        assert created == [actual]
        bridge = actual.coordinator.get_capability("session.media.tool_post")
        assert isinstance(bridge, MediaHookBridge)
        session_hooks(actual, "host.incremental_save").register(
            "tool:post", lambda e, d: HookResult(), priority=900, name="fixture-save")
        transitions = []
        def activate_mode(event, data):
            actual.config["fixture_mode"] = data["name"]
            transitions.append(data["name"])
            session_hooks(actual, "host.mode").register(
                "tool:post", lambda e, d: HookResult(), name="fixture-mode-post")
            return HookResult()
        session_hooks(actual, "host.mode").register("mode:activated", activate_mode, name="fixture-mode")
        seal_session_hook_graph(actual)
        receipt = bridge.graph_receipt
        assert bridge.validate_receipt(receipt)
        if image_format:
            host = actual.coordinator.get_capability("cli.session_media")
            assert host.durable and host.capability.enabled
            assert host.store.policy.max_images is None and host.store.policy.ttl_seconds is None
            data = io.BytesIO()
            Image.new("RGB", (3, 2), "orange").save(data, image_format)
            payload = data.getvalue()
            origin = {"kind": "tool", "session_id": "root", "id": "generated-call"}
            ref = (await host.capability.ingest(payload, source_format="image-bytes",
                                               origin=origin, policy=None))["value"]
            outcome = await bridge({"tool_name": "synthetic", "tool_call_id": "generated-call",
                                    "result": {"refs": [ref]}})
            assert outcome["complete"]
            await host.capability.publish(outcome["result"]["refs"], origin=origin, disposition="retain")
            context = actual.coordinator.get("context")
            await context.add_message({"role": "user", "content": "synthetic observation",
                                       "metadata": {"experimental.media.v1": {"refs": [ref]}}})
            await actual.coordinator.get_capability("session.durable_checkpoint")()
            canonical = await context.get_messages()
            before = deepcopy(canonical)
            request = ChatRequest(messages=[Message(**row) for row in canonical])
            wrapped = actual.coordinator.get("providers")["fixture-provider"]
            original = next(original for original, wrapper in host.wrappers.values() if wrapper is wrapped)
            await wrapped.request_budget(request)
            with pytest.raises(ValueError, match="active leases"):
                with actual.coordinator.get_capability("cli.media.graph_transition")():
                    raise AssertionError("must not enter while counted candidate is live")
            assert await wrapped.complete(request) == "synthetic provider result"
            counted, sent = original.observed
            assert counted[0] is sent[0] and counted[0] is not request
            assert counted[1] == sent[1] == [hashlib.sha256(payload).hexdigest()]
            assert await context.get_messages() == before
            assert native.load("root")[0] == before
        from amplifier_app_cli.main import CommandProcessor
        processor = CommandProcessor(actual, "fixture")
        processor.configurator = initialized.configurator
        before_typo = bridge.graph_receipt
        unchanged = deepcopy(actual.config)
        assert "not found" in await processor._handle_config_toggle(
            "providers", "disable", "typo-not-mounted"
        )
        assert actual.config == unchanged
        assert bridge.graph_receipt is before_typo and bridge.validate_receipt(before_typo)
        if image_format:
            assert "Already enabled" in await processor._handle_config_toggle(
                "providers", "enable", "fixture-provider"
            )
            assert actual.config == unchanged
            assert bridge.graph_receipt is before_typo and bridge.validate_receipt(before_typo)
        assert await processor._handle_mode("synthetic on") == "Mode: synthetic"
        next_receipt = bridge.graph_receipt
        assert next_receipt is not receipt
        assert not bridge.validate_receipt(receipt)
        assert bridge.validate_receipt(next_receipt)
        assert transitions == ["synthetic"]
        if image_format:
            await wrapped.request_budget(request)
            assert "Already" in await processor._handle_mode("synthetic on")
            assert "Not in" in await processor._handle_mode("different off")
            assert bridge.graph_receipt is next_receipt and bridge.validate_receipt(next_receipt)
            assert await wrapped.complete(request) == "synthetic provider result"
            assert original.observed[-1][1] == [hashlib.sha256(payload).hexdigest()]
            assert await context.get_messages() == before
        assert (await bridge({"tool_name": "synthetic", "tool_call_id": "second", "result": "next"}))["complete"]
        result = await bridge({"tool_name": "synthetic", "tool_call_id": "call", "result": {"value": "kept"}})
        assert result["complete"] and result["result"] == {"value": "kept"}
        assert bridge.validate_receipt(next_receipt)
        assert actual.config["root_session_id"] == "root"
        assert actual.config["project_dir"]
        if resume and not image_format:
            assert await actual.coordinator.get("context").get_messages() == restored
    finally:
        await initialized.cleanup()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX media durability")
async def test_actual_raw_spawn_and_cold_resume_have_fresh_graph_and_owned_pixels(tmp_path, monkeypatch, image_format):
    from amplifier_app_cli.session_hooks import install_session_hook_graph
    from amplifier_app_cli.session_media import install_session_media
    from amplifier_app_cli.session_checkpoint import install_session_checkpoint
    from amplifier_app_cli.session_spawner import spawn_sub_session, resume_sub_session
    native = SessionStore(tmp_path)
    plan = {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
            "providers": [{"module": "fixture-provider"}]}
    parent = AmplifierSession(deepcopy(plan), session_id="parent")
    parent.coordinator.loader = Loader()
    install_session_hook_graph(parent, owner="fixture.parent", host_participants=("host.media",))
    host = install_session_media(parent, native)
    install_session_checkpoint(parent, native, session_id="parent")
    await parent.initialize()
    # The trusted caller explicitly selects canonical rows and has committed
    # their assets before passing them to the public child entry point.
    data = io.BytesIO()
    Image.new("RGB", (2, 3), "blue").save(data, image_format)
    payload = data.getvalue()
    origin = {"kind": "tool", "session_id": "parent", "id": "selected-call"}
    ref = (await host.capability.ingest(payload, source_format="image-bytes", origin=origin, policy=None))["value"]
    await host.capability.publish([ref], origin=origin, disposition="retain")
    selected = [{"role": "user", "content": "selected observation",
                 "metadata": {"experimental.media.v1": {"refs": [ref]}}}]
    await parent.coordinator.get("context").set_messages(selected)
    await parent.coordinator.get_capability("session.durable_checkpoint")()
    created, receipts, requests = [], [], []
    async def execute(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        actual = created[-1]
        bridge = actual.coordinator.get_capability("session.media.tool_post")
        assert bridge.validate_receipt(bridge.graph_receipt)
        assert (await bridge({"tool_name": "synthetic", "tool_call_id": "child-call", "result": "kept"}))["complete"]
        rows = await context.get_messages()
        saved = native.load("child")[0]
        assert rows == saved
        child_ref = rows[0]["metadata"]["experimental.media.v1"]["refs"][0]
        assert child_ref["session_id"] == "child"
        assert child_ref["provenance"] == ref["provenance"]
        request = ChatRequest(messages=[Message(**row) for row in rows])
        provider = providers["fixture-provider"]
        await provider.request_budget(request)
        await provider.complete(request)
        media = actual.coordinator.get_capability("cli.session_media")
        raw = next(raw for raw, wrapped in media.wrappers.values() if wrapped is provider)
        assert raw.observed[-2][0] is raw.observed[-1][0] and raw.observed[-1][0] is not request
        assert raw.observed[-1][1] == [hashlib.sha256(payload).hexdigest()]
        assert await context.get_messages() == rows
        receipts.append(bridge.graph_receipt)
        requests.append(request)
        return "child done"
    def actual_factory(*args, **kwargs):
        actual = AmplifierSession(*args, **kwargs)
        actual.coordinator.loader = Loader(execute)
        created.append(actual)
        return actual
    monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "-1")
    settings = MagicMock()
    settings.get_config_overrides.return_value = {}
    settings.get_provider_overrides.return_value = []
    settings.get_notification_hook_overrides.return_value = []
    try:
        with (
            patch("amplifier_app_cli.session_spawner.AmplifierSession", side_effect=actual_factory),
            patch("amplifier_app_cli.session_store.SessionStore", return_value=native),
            patch("amplifier_app_cli.paths.create_foundation_resolver"),
            patch("amplifier_app_cli.session_spawner._expand_system_instruction", AsyncMock(side_effect=lambda text, _: text)),
            patch("amplifier_app_cli.lib.settings.AppSettings", return_value=settings),
        ):
            result = await spawn_sub_session(
                agent_name="named", instruction="synthetic", parent_session=parent,
                agent_configs={"named": {}}, sub_session_id="child", parent_messages=selected,
            )
            assert result["output"] == "child done"
            await parent.cleanup()
            import shutil
            shutil.rmtree(tmp_path / "parent")
            resumed = await resume_sub_session("child", "follow-up", parent_session=None)
            assert resumed["output"] == "child done"
        assert len(created) == 2 and created[0] is not created[1]
        assert receipts[0] is not receipts[1]
        assert selected[0]["metadata"]["experimental.media.v1"]["refs"][0]["session_id"] == "parent"
    finally:
        await parent.cleanup()


@pytest.mark.parametrize("selected_alias", ["account-A", "account-B"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX media durability")
async def test_two_configured_aliases_keep_exact_selected_adapter_and_unsupported_warning(tmp_path, selected_alias):
    native = SessionStore(tmp_path)
    plan = {
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "providers": [
            {"module": "fixture-provider", "instance_id": "account-A", "config": {"support": True}},
            {"module": "fixture-provider", "instance_id": "account-B", "config": {"support": False}},
        ],
    }
    prepared = PreparedBundle(deepcopy(plan), MagicMock(), Bundle(name="fixture"))
    config = SessionConfig(config=deepcopy(plan), search_paths=[], verbose=False, session_id="root",
                           prepared_bundle=prepared, bundle_name="fixture", shared_root=False,
                           invocation_mode="single")
    def factory(*args, **kwargs):
        actual = AmplifierSession(*args, **kwargs)
        actual.coordinator.loader = Loader()
        return actual
    settings = MagicMock()
    settings.with_session.return_value.get_merged_settings.return_value = {"configurator": {}}
    with (
        patch("amplifier_core.AmplifierSession", side_effect=factory),
        patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
        patch("amplifier_app_cli.session_runner.SessionStore", return_value=native),
        patch("amplifier_app_cli.session_runner.AppSettings", return_value=settings),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.runtime.config.inject_user_providers"),
    ):
        initialized = await create_initialized_session(config, Console(file=StringIO()))
    actual = initialized.session
    try:
        seal_session_hook_graph(actual)
        bridge = actual.coordinator.get_capability("session.media.tool_post")
        assert bridge.validate_receipt(bridge.graph_receipt)
        host = actual.coordinator.get_capability("cli.session_media")
        providers = actual.coordinator.get("providers")
        assert set(providers) == {"account-A", "account-B"}
        assert all(provider.name != alias for alias, provider in providers.items())
        raw_by_alias = {
            alias: next(raw for raw, wrapper in host.wrappers.values() if wrapper is wrapped)
            for alias, wrapped in providers.items()
        }
        evidence = actual.coordinator.get_capability("cli.hooks.participating_graph").construction.participants_evidence()
        adapters = evidence["adapters"]
        assert all(any(edge["previous"] is raw_by_alias[alias] and edge["installed"] is providers[alias]
                       for edge in adapters) for alias in providers)
        data = io.BytesIO()
        Image.new("RGB", (2, 2), "green").save(data, "PNG")
        origin = {"kind": "tool", "session_id": "root", "id": "capture"}
        ref = (await host.capability.ingest(data.getvalue(), source_format="image-bytes", origin=origin, policy=None))["value"]
        await host.capability.publish([ref], origin=origin, disposition="retain")
        context = actual.coordinator.get("context")
        rows = [{"role": "user", "content": "observation",
                 "metadata": {"experimental.media.v1": {"refs": [ref]}}}]
        await context.set_messages(rows)
        await actual.coordinator.get_capability("session.durable_checkpoint")()
        request = ChatRequest(messages=[Message(**row) for row in rows])
        selected = providers[selected_alias]
        await selected.request_budget(request)
        await selected.complete(request)
        counted, sent = raw_by_alias[selected_alias].observed
        assert counted[0] is sent[0] and counted[0] is not request
        if selected_alias == "account-A":
            assert counted[1] == sent[1] == [ref["sha256"]]
        else:
            assert counted[1] == sent[1] == []
            assert "Image unavailable" in sent[2]
        other = "account-B" if selected_alias == "account-A" else "account-A"
        assert raw_by_alias[other].observed == []
        assert await context.get_messages() == rows
        assert bridge.validate_receipt(bridge.graph_receipt)
    finally:
        await initialized.cleanup()
