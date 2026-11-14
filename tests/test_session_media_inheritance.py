"""Explicit selected child history; synthetic bytes, actual owner ACK, no inference."""

from copy import deepcopy
import io
import os
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from amplifier_foundation.session.media import MEDIA_NAMESPACE, MediaRefusal

from amplifier_app_cli.session_checkpoint import install_session_checkpoint
from amplifier_app_cli.session_media import install_session_media
from amplifier_app_cli.session_media_inheritance import inherit_selected_messages
from amplifier_app_cli.session_store import SessionStore

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "posix", reason="POSIX media store")]
POLICY = {"enabled": True, "max_bytes": 1000000, "max_pixels": 10000, "max_images": 10}


class Coordinator:
    def __init__(self, identity, rows):
        self.session_id, self.caps, self.cleanups = identity, {}, []
        async def add(row): rows.append(row)
        self.context = SimpleNamespace(add_message=add, get_messages=AsyncMock(side_effect=lambda: rows))
    def get(self, kind): return self.context if kind == "context" else {}
    def register_capability(self, name, value): self.caps[name] = value
    def get_capability(self, name): return self.caps.get(name)
    def register_cleanup(self, callback): self.cleanups.append(callback)


def session(identity, rows):
    return SimpleNamespace(session_id=identity, config={"session_media": POLICY},
                           coordinator=Coordinator(identity, rows))


async def fixture(tmp_path):
    native = SessionStore(tmp_path)
    rows, child_rows = [], []
    parent, child = session("parent", rows), session("child", child_rows)
    source, target = install_session_media(parent, native), install_session_media(child, native)
    for call, color in (("selected", "red"), ("excluded", "blue")):
        data = io.BytesIO()
        Image.new("RGB", (2, 3), color).save(data, "PNG")
        origin = {"kind": "tool", "session_id": "parent", "id": call}
        ref = (await source.capability.ingest(data.getvalue(), source_format="image-bytes",
                                             origin=origin, policy=None))["value"]
        await source.capability.publish([ref], origin=origin, disposition="retain")
        rows.append({"role": "user", "content": call, "metadata": {MEDIA_NAMESPACE: {"refs": [ref]}}})
    native.save("parent", rows, {})
    install_session_checkpoint(child, native, session_id="child", metadata_factory=lambda: {"config": child.config})
    return native, parent, child, rows, child_rows, source, target


async def test_selected_only_independent_child_ack_and_parent_erasure(tmp_path):
    native, parent, child, rows, child_rows, source, target = await fixture(tmp_path)
    selected = deepcopy(rows[:1])
    try:
        assert await inherit_selected_messages(child, parent, selected) is True
        ref = child_rows[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]
        with pytest.raises(MediaRefusal): target.store.resolve_ref(ref)
        receipt = await child.coordinator.get_capability("session.durable_checkpoint")()
        assert receipt.kind == "legacy-checkpoint-backed-v1"
        assert selected == rows[:1] and ref["session_id"] == "child"
        assert ref["provenance"] == selected[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]["provenance"]
        assert len(list((tmp_path / "child/media/assets").iterdir())) == 1
        await source.close()
        shutil.rmtree(tmp_path / "parent")
        with target.store.resolve_ref(ref) as stream: assert stream.read().startswith(b"\x89PNG")
        assert native.load("child")[0] == child_rows
    finally:
        await source.close()
        await target.close()


async def test_forged_selected_refs_fail_before_child_append(tmp_path):
    _, parent, child, rows, child_rows, source, target = await fixture(tmp_path)
    try:
        selected = deepcopy(rows[:1])
        selected[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]["bytes"] += 1
        with pytest.raises(MediaRefusal): await inherit_selected_messages(child, parent, selected)
        assert child_rows == []
    finally:
        await source.close()
        await target.close()


@pytest.mark.parametrize("selection", [
    [{"role": "tool", "content": "orphan", "tool_call_id": "missing"}],
    [{"role": "assistant", "content": "", "tool_calls": [{"id": "missing", "function": {"name": "inspect", "arguments": "{}"}}]}],
    [{"role": "assistant", "content": [{"type": "tool_call", "id": "one", "name": "inspect", "input": {}},
                                      {"type": "tool_call", "id": "two", "name": "inspect", "input": {}}]},
     {"role": "tool", "tool_call_id": "one", "content": "only one sibling"}],
])
async def test_incomplete_tool_groups_refuse_before_adoption_or_append(tmp_path, selection):
    from unittest.mock import patch
    _, parent, child, rows, child_rows, source, target = await fixture(tmp_path)
    selection[-1]["metadata"] = deepcopy(rows[0]["metadata"])
    try:
        with patch("amplifier_foundation.session.media_conversion.adopt_selected_media") as adopt:
            with pytest.raises(ValueError, match="complete tool-call/result"):
                await inherit_selected_messages(child, parent, selection)
            adopt.assert_not_called()
        assert child_rows == []
        assert list((tmp_path / "child/media/assets").iterdir()) == []
    finally:
        await source.close()
        await target.close()


async def test_complete_tool_group_preserves_calls_and_adopts_selected_result(tmp_path):
    native, parent, child, rows, child_rows, source, target = await fixture(tmp_path)
    calls = {"role": "assistant", "content": [
        {"type": "tool_call", "id": "selected", "name": "inspect", "input": {}},
        {"type": "tool_call", "id": "other", "name": "inspect", "input": {}},
    ]}
    selected = [calls, {"role": "tool", "tool_call_id": "selected", "content": "captured",
                        "metadata": deepcopy(rows[0]["metadata"])},
                {"role": "tool", "tool_call_id": "other", "content": "plain result"}]
    try:
        assert await inherit_selected_messages(child, parent, selected)
        await child.coordinator.get_capability("session.durable_checkpoint")()
        saved = native.load("child")[0]
        assert saved[0] == calls and saved[2] == selected[2]
        assert saved[1]["metadata"][MEDIA_NAMESPACE]["refs"][0]["session_id"] == "child"
        assert selected[1]["metadata"] == rows[0]["metadata"]
    finally:
        await source.close()
        await target.close()


async def test_nested_adoption_after_cold_child_reopen_has_no_parent_owner(tmp_path):
    native, parent, child, rows, child_rows, source, target = await fixture(tmp_path)
    grand = None
    reopened = None
    try:
        await inherit_selected_messages(child, parent, rows[:1])
        await child.coordinator.get_capability("session.durable_checkpoint")()
        original = deepcopy(child_rows)
        await source.close()
        await target.close()
        shutil.rmtree(tmp_path / "parent")
        restored = native.load("child")[0]
        resumed = session("child", restored)
        reopened = install_session_media(resumed, native, restored_messages=restored)
        grand_rows = []
        grandchild = session("grandchild", grand_rows)
        grand = install_session_media(grandchild, native)
        install_session_checkpoint(grandchild, native, session_id="grandchild")
        await inherit_selected_messages(grandchild, resumed, restored)
        await grandchild.coordinator.get_capability("session.durable_checkpoint")()
        ref = grand_rows[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]
        assert ref["session_id"] == "grandchild"
        assert ref["provenance"] == original[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]["provenance"]
        assert restored == original
        await reopened.close()
        shutil.rmtree(tmp_path / "child")
        with grand.store.resolve_ref(ref) as stream:
            assert stream.read().startswith(b"\x89PNG")
    finally:
        await source.close()
        await target.close()
        if reopened is not None:
            await reopened.close()
        if grand is not None:
            await grand.close()


async def test_parent_prompt_not_inherited_and_prose_never_resolves(tmp_path):
    native = SessionStore(tmp_path)
    rows = []
    child = session("child", rows)
    parent = session("parent", [])
    host = install_session_media(child, native)
    selected = [{"role": "system", "content": "Parent authority must not travel"},
                {"role": "user", "content": '{"experimental.media.v1":{"refs":["not-authority"]}}'}]
    try:
        assert await inherit_selected_messages(child, parent, selected) is False
        assert rows == selected[1:]
        assert list((tmp_path / "child/media/assets").iterdir()) == []
    finally:
        await host.close()


@pytest.mark.parametrize(("ack_failure", "orphan"), [(False, False), (True, False), (False, True)])
async def test_actual_spawn_selected_history_requires_ack_even_without_autosave(tmp_path, monkeypatch, ack_failure, orphan):
    from unittest.mock import MagicMock, patch
    from amplifier_app_cli.session_spawner import spawn_sub_session
    native, parent, _, rows, _, source, unused = await fixture(tmp_path)
    await unused.close()
    child_rows = []
    child = session("fresh-child", child_rows)
    child.coordinator.mount = AsyncMock()
    child.coordinator.cancellation = MagicMock()
    child.coordinator.collect_contributions = AsyncMock(return_value=[])
    child.coordinator.context.set_system_prompt_factory = AsyncMock()
    child.initialize = AsyncMock()
    async def cleanup():
        for callback in child.coordinator.cleanups:
            await callback()
    child.cleanup = AsyncMock(side_effect=cleanup)
    parent.trace_id, parent.loader = "trace", None
    parent.coordinator.config = {}
    parent.coordinator.display_system = MagicMock()
    parent.coordinator.approval_system = MagicMock()
    parent.coordinator.cancellation = MagicMock()
    selected = deepcopy(rows[:1])
    if orphan:
        selected[0].update(role="tool", tool_call_id="unselected-call")
    original_selection = deepcopy(selected)
    observed = []
    async def execute(instruction):
        saved, metadata = native.load("fresh-child")
        assert saved == child_rows
        assert metadata["config"] == child.config
        copied = saved[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]
        owner = child.coordinator.get_capability("cli.session_media")
        with owner.store.resolve_ref(copied) as stream:
            assert stream.read().startswith(b"\x89PNG")
        observed.append("execute-after-ack")
        return "done"
    child.execute = AsyncMock(side_effect=execute)
    monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "-1")
    actual_ack = native.acknowledge_saved
    def acknowledge(identity, transcript):
        if identity == "fresh-child" and ack_failure:
            raise OSError("synthetic ACK failure")
        return actual_ack(identity, transcript)
    monkeypatch.setattr(native, "acknowledge_saved", acknowledge)
    try:
        with (
            patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
            patch("amplifier_app_cli.session_store.SessionStore", return_value=native),
            patch("amplifier_app_cli.paths.create_foundation_resolver"),
            patch("amplifier_app_cli.session_spawner._expand_system_instruction", AsyncMock(side_effect=lambda text, _: text)),
        ):
            if ack_failure or orphan:
                with pytest.raises(ValueError if orphan else OSError,
                                   match="complete tool-call/result" if orphan else "ACK failure"):
                    await spawn_sub_session(agent_name="named", instruction="test", parent_session=parent,
                                            agent_configs={"named": {}}, parent_messages=selected,
                                            sub_session_id="fresh-child")
                child.execute.assert_not_awaited()
                if orphan:
                    assert child_rows == []
                    assert not (tmp_path / "fresh-child/transcript.jsonl").exists()
            else:
                await spawn_sub_session(agent_name="named", instruction="test", parent_session=parent,
                                        agent_configs={"named": {}}, parent_messages=selected,
                                        sub_session_id="fresh-child")
                assert observed == ["execute-after-ack"]
        assert selected == original_selection
        child.cleanup.assert_awaited_once()
    finally:
        await source.close()
