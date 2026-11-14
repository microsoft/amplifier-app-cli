"""Public host binding/projection controls using synthetic candidate Foundation."""

import base64
import asyncio
import copy
import hashlib
import io
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("amplifier_foundation.session.media")
pytest.importorskip("amplifier_foundation.session.media_provider")
Image = pytest.importorskip("PIL.Image")

from amplifier_core.message_models import ChatRequest, Message, ImageBlock
from amplifier_app_cli.session_checkpoint import install_session_checkpoint
from amplifier_app_cli.session_media import (
    install_session_media, media_configuration, mount_session_media,
)
from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.asyncio

POLICY = {
    "enabled": True, "max_bytes": 1024 * 1024, "max_pixels": 10000,
    "max_images": 10, "ttl_seconds": 300,
}


async def test_actual_core_public_neutral_mount_before_initialization(tmp_path):
    from amplifier_core import AmplifierSession
    actual = AmplifierSession(config={"session": {"orchestrator": "loop-basic", "context": "context-simple"}}, session_id="actual")
    host = install_session_media(actual, SessionStore(tmp_path), config={})
    original = Provider(host.capability)
    try:
        await actual.coordinator.mount("providers", original, name="selected-account")
        await mount_session_media(actual)
        wrapped = actual.coordinator.get("providers")["selected-account"]
        assert wrapped is not original
        assert wrapped is host.prepare_provider(original, alias="selected-account")
        assert actual.coordinator.get_capability("cli.media.prepare_provider") == host.prepare_provider
        assert actual.coordinator.get_capability("session.media") is None
        assert host.capability.enabled is False
        assert not (tmp_path / "actual/media").exists()
    finally:
        await actual.cleanup()
        assert host.closed


class Coordinator:
    def __init__(self, session_id, rows):
        self.session_id = session_id
        self.caps, self.providers, self.cleanups, self.mounts = {}, {}, [], []
        self.tools = {}

        async def messages():
            return rows
        self.context = SimpleNamespace(get_messages=messages)

    def get_capability(self, name):
        return self.caps.get(name)

    def register_capability(self, name, value):
        self.caps[name] = value

    def register_cleanup(self, callback):
        self.cleanups.append(callback)

    def get(self, name):
        return (self.context if name == "context" else self.providers if name == "providers"
                else self.tools if name == "tools" else None)

    async def mount(self, kind, provider, *, name):
        assert kind == "providers"
        self.providers[name] = provider
        self.mounts.append((name, provider))

    async def cleanup(self):
        for callback in reversed(self.cleanups):
            await callback()


def session(session_id, rows, *, policy=POLICY, loop="loop-basic"):
    config = {"session": {"orchestrator": loop}}
    if policy is not None:
        config["session_media"] = copy.deepcopy(policy)
    return SimpleNamespace(
        session_id=session_id, config=config, coordinator=Coordinator(session_id, rows)
    )


class Provider:
    name = "deliberately-selected"
    default_model = "deliberately-selected-model"
    config = {"priority": 0}

    def __init__(self, cap, *, support=True):
        self.cap, self.support, self.observed = cap, support, []

    def get_media_request_support(self, *, model, request_options):
        return {"v": 1, "positions": ["tool"] if self.support else []}

    def observe(self, request, purpose):
        self.cap.validate(request, purpose=purpose, request_options={})
        images = []
        texts = []
        for message in request.messages:
            for block in message.content if isinstance(message.content, list) else []:
                if isinstance(block, ImageBlock):
                    images.append(hashlib.sha256(base64.b64decode(block.source["data"])).hexdigest())
                else:
                    texts.append(block.text)
        self.observed.append((purpose, id(request), images, texts))

    async def request_budget(self, request, *, request_options=None):
        self.observe(request, "count")
        return {"estimated_input_tokens": 10, "input_limit_tokens": 100,
                "context_token_budget": 100, "measurement": {
                    "kind": "provider_count", "source": "fixture", "input_tokens": 10}}

    async def complete(self, request):
        self.observe(request, "transport")
        return "fixture response"


@pytest.mark.skipif(os.name != "posix", reason="Candidate store/ACK are POSIX-only")
@pytest.mark.parametrize("support", [False, True])
async def test_actual_mounted_selection_counts_and_sends_same_private_projection(
    tmp_path, support
):
    rows = []
    selected = session("root", rows)
    native = SessionStore(tmp_path)
    install_session_checkpoint(selected, native, session_id="root")
    host = install_session_media(selected, native)
    assert selected.coordinator.caps["experimental.media.v1"] is host.capability
    assert host.store.session_id == "root"
    try:
        data = io.BytesIO()
        Image.new("RGB", (3, 2), "orange").save(data, format="PNG")
        origin = {"kind": "tool", "session_id": "root", "id": "actual-call"}
        ref = (await host.capability.ingest(
            data.getvalue(), source_format="image-bytes", origin=origin, policy=None
        ))["value"]
        await host.capability.publish([ref], origin=origin, disposition="retain")
        rows.append({
            "role": "tool", "tool_call_id": "actual-call", "content": "Observation",
            "metadata": {"experimental.media.v1": {"refs": [ref]}},
        })
        await selected.coordinator.caps["session.durable_checkpoint"]()
        original = Provider(host.capability, support=support)
        selected.coordinator.providers["user-selected-account"] = original
        await mount_session_media(selected)
        provider = selected.coordinator.providers["user-selected-account"]
        assert provider is not original
        assert provider.name == original.name
        assert provider.default_model == original.default_model
        assert not hasattr(provider, "stream")
        assert not hasattr(provider, "refit_request")
        request = ChatRequest(messages=[Message(**row) for row in rows])
        before = request.model_dump()
        await provider.request_budget(request)
        await provider.complete(request)
        count, send = original.observed
        assert count[1] == send[1]  # Actual same private object, not only same refs.
        assert count[2] == send[2] == ([ref["sha256"]] if support else [])
        if not support:
            assert any("Image unavailable" in text for text in count[3])
        assert request.model_dump() == before
        assert "base64" not in str(native.load("root")[0])
    finally:
        await selected.coordinator.cleanup()
    assert host.closed
    assert not native._media_stores


@pytest.mark.skipif(os.name != "posix", reason="Candidate store is POSIX-only")
async def test_nested_and_resumed_binding_never_inherit_parent_capability(tmp_path):
    native = SessionStore(tmp_path)
    parent, child = session("parent", []), session("child", [])
    ph = install_session_media(parent, native)
    ch = install_session_media(child, native)
    try:
        assert ph.capability is not ch.capability
        assert ph.generation != ch.generation
        assert ph.store.root != ch.store.root
        assert not (tmp_path / "child" / "media" / "sha256").exists()
    finally:
        await parent.coordinator.cleanup()
        await child.coordinator.cleanup()
    # Resume constructs a fresh coordinator with the same durable owner ID.
    child = session("child", [])
    resumed = install_session_media(child, native)
    try:
        assert resumed.capability is not ch.capability
        assert resumed.generation != ch.generation
    finally:
        await resumed.close()


async def test_unconfigured_text_host_has_neutral_no_storage_projection(tmp_path):
    native = SessionStore(tmp_path)
    selected = session("text", [], policy=None)
    original = Provider(None)
    selected.coordinator.providers["chosen"] = original
    host = install_session_media(selected, native)
    try:
        assert not host.durable
        assert not host.capability.enabled
        original.cap = host.capability
        await mount_session_media(selected)
        provider = selected.coordinator.providers["chosen"]
        request = ChatRequest(messages=[Message(role="user", content="plain text")])
        await provider.complete(request)
        assert provider.name == original.name
        assert not (tmp_path / "text").exists()
        assert not native._media_stores
    finally:
        await host.close()


@pytest.mark.parametrize("policy", [
    {}, {"enabled": True}, {**POLICY, "max_bytes": True},
    {**POLICY, "enabled": 1}, {**POLICY, "source": "/arbitrary"},
])
async def test_invalid_explicit_policy_refuses_before_binding(policy):
    with pytest.raises(ValueError):
        media_configuration({"session_media": policy})


async def test_neutral_inline_unavailable_before_count_and_unowned_ref_refuses(tmp_path):
    from amplifier_foundation.session.media import MediaRefusal

    selected = session("text", [], policy=None)
    host = install_session_media(selected, SessionStore(tmp_path))
    provider = Provider(host.capability)
    wrapped = host.prepare_provider(provider)
    try:
        request = ChatRequest(messages=[Message(role="user", content=[
            ImageBlock(source={"type": "base64", "media_type": "image/png",
                               "data": "c2VlZGVkLWltYWdlLXNlbnRpbmVs"})
        ])])
        await wrapped.request_budget(request)
        await wrapped.complete(request)
        assert not provider.observed[0][2]
        assert any("Image unavailable" in text for text in provider.observed[0][3])
        assert provider.observed[0][1] == provider.observed[1][1]
        malformed = ChatRequest(messages=[Message(
            role="tool", content="unknown image",
            metadata={"experimental.media.v1": {"refs": [{"v": True}]}},
        )])
        with pytest.raises(MediaRefusal):
            await wrapped.complete(malformed)
        assert len(provider.observed) == 2
        assert not (tmp_path / "text").exists()
    finally:
        await host.close()


@pytest.mark.skipif(os.name != "posix", reason="Candidate store is POSIX-only")
async def test_screenshot_classification_does_not_restore_source_consent(tmp_path):
    from amplifier_foundation.session.media import MediaRefusal

    selected = session("root", [])
    native = SessionStore(tmp_path)
    host = install_session_media(selected, native)
    try:
        data = io.BytesIO()
        Image.new("RGB", (3, 2), "purple").save(data, format="PNG")
        ref = (await host.capability.ingest(
            data.getvalue(), source_format="image-bytes",
            origin={"kind": "tool", "session_id": "root", "id": "source-call"},
            policy=None,
        ))["value"]
        current = {"allowed": True}

        def validate(value, *, purpose):
            if not current["allowed"]:
                raise ValueError("synthetic stopped source")
            return True

        tool = SimpleNamespace(
            _owns_media_ref=lambda value: value == ref, _validate_media_ref=validate
        )
        selected.coordinator.tools["source-owner"] = tool
        assert host._computer_policy(ref, purpose="persist") is True
        assert host.store.get_origin_classification(ref) == "computer-use"
        current["allowed"] = False
        with pytest.raises(MediaRefusal) as error:
            host._source_policy(ref, purpose="model")
        assert error.value.code == "source_policy"
        selected.coordinator.tools.clear()
        with pytest.raises(MediaRefusal) as error:
            host._source_policy(ref, purpose="model")
        assert error.value.code == "source_policy"
    finally:
        await host.close()


@pytest.mark.skipif(os.name != "posix", reason="Candidate store is POSIX-only")
async def test_computer_overlay_frozen_before_count_witness_checked_before_send(tmp_path):
    selected = session("root", [])
    host = install_session_media(selected, SessionStore(tmp_path))
    calls = []
    current = {"allowed": True}

    def prepare(request):
        calls.append("prepare")
        request.messages[0].content = "private owner overlay"

        def witness():
            calls.append("witness")
            if not current["allowed"]:
                raise ValueError("synthetic source changed")
        return request, witness

    selected.coordinator.caps["computer_use.prepare_request"] = prepare
    original = Provider(host.capability)
    wrapped = host.prepare_provider(original)
    request = ChatRequest(messages=[Message(role="user", content="canonical")])
    try:
        await wrapped.request_budget(request)
        current["allowed"] = False
        with pytest.raises(ValueError, match="source changed"):
            await wrapped.complete(request)
        assert len(original.observed) == 1
        assert calls.count("prepare") == 1
        assert request.messages[0].content == "canonical"
    finally:
        await host.close()


@pytest.mark.skipif(os.name != "posix", reason="Candidate store is POSIX-only")
async def test_forked_screenshot_classification_does_not_restore_source_authority(tmp_path):
    from amplifier_foundation.session.media import MEDIA_NAMESPACE, MediaRefusal
    from amplifier_app_cli.session_media_fork import fork_native_session
    rows = []
    native = SessionStore(tmp_path)
    parent = session("parent", rows)
    host = install_session_media(parent, native)
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, "PNG")
    origin = {"kind": "tool", "session_id": "parent", "id": "actual-capture"}
    ref = (await host.capability.ingest(output.getvalue(), source_format="image-bytes",
                                       origin=origin, policy=None))["value"]
    parent.coordinator.tools["original"] = SimpleNamespace(
        _owns_media_ref=lambda value: value == ref,
        _validate_media_ref=lambda value, **kwargs: True,
    )
    host._computer_policy(ref, purpose="persist")
    await host.capability.publish([ref], origin=origin, disposition="retain")
    rows.append({"role": "user", "content": "Saved screenshot", "metadata": {MEDIA_NAMESPACE: {"refs": [ref]}}})
    native.save("parent", rows, {})
    try:
        result = fork_native_session(native, "parent", rows, {}, turn=1,
                                     new_session_id="child", fenced=True, source_media=host.store)
    finally:
        await host.close()
    child = session("child", result.messages)
    owned = install_session_media(child, native, restored_messages=result.messages)
    copied = result.messages[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]
    try:
        assert owned.store.get_origin_classification(copied) == "computer-use"
        with pytest.raises(MediaRefusal) as error:
            owned.store.validate_ref(copied)
        assert error.value.code == "source_policy"
        assert child.coordinator.tools == {}  # No parent tool/consent inherited.
    finally:
        await owned.close()


@pytest.mark.skipif(os.name != "posix", reason="Candidate store is POSIX-only")
@pytest.mark.parametrize("failure", [RuntimeError("fixture"), asyncio.CancelledError()])
async def test_public_spawn_post_initialize_setup_failure_releases_media_lock(
    tmp_path, monkeypatch, failure
):
    from amplifier_app_cli.session_spawner import spawn_sub_session
    from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore
    from amplifier_foundation.session.checkpoint import verified_checkpoint_refs

    native = SessionStore(tmp_path)
    child = session("child", [])
    child.coordinator.mount = AsyncMock()
    child.coordinator.cancellation = MagicMock()
    child.coordinator.collect_contributions = AsyncMock(return_value=[])
    child.initialize = AsyncMock()
    child.execute = AsyncMock()
    child.cleanup = AsyncMock(side_effect=child.coordinator.cleanup)
    parent = MagicMock()
    parent.session_id, parent.trace_id, parent.loader = "parent", "trace", None
    parent.config = {"session": {"orchestrator": "loop-basic"}, "session_media": POLICY}
    parent.coordinator.config = {}
    parent.coordinator.get = MagicMock(return_value=None)
    parent.coordinator.get_capability = MagicMock(return_value=None)
    parent.coordinator.cancellation = MagicMock()

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=native),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_spawner._expand_system_instruction",
              AsyncMock(side_effect=failure)),
        pytest.raises(type(failure)),
    ):
        await spawn_sub_session(
            agent_name="named", instruction="fixture", parent_session=parent,
            agent_configs={"named": {"instruction": "render before execute"}},
            sub_session_id="child",
        )
    child.initialize.assert_awaited_once()
    child.execute.assert_not_awaited()
    child.cleanup.assert_awaited_once()
    parent.coordinator.display_system.push_nesting.assert_called_once()
    parent.coordinator.display_system.pop_nesting.assert_called_once()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    assert not native._media_stores
    # A leaked writer descriptor would refuse a fresh independent coordinator.
    reopened = SessionMediaStore(
        tmp_path / "child" / "media", "child",
        policy=MediaPolicy(**{key: value for key, value in POLICY.items() if key != "enabled"}),
        checkpoint_verifier=verified_checkpoint_refs,
    )
    reopened.close()