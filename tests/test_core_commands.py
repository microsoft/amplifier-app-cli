import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatResponse, TextBlock

from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.ui.core_commands import CoreCommandService


class _Context:
    def __init__(self) -> None:
        self.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    async def get_messages(self):
        return list(self.messages)

    async def clear(self):
        self.messages.clear()

    async def compact(self):
        return None


class _Provider:
    def __init__(self) -> None:
        self.default_model = "old-model"
        self.config = {"default_model": "old-model"}
        self.request = None

    async def complete(self, request):
        self.request = request
        return ChatResponse(content=[TextBlock(text="isolated answer")])

    async def list_models(self):
        return [SimpleNamespace(id="gpt-5.5"), SimpleNamespace(id="gpt-5.6")]


class _Coordinator:
    def __init__(self) -> None:
        self.context = _Context()
        self.provider = _Provider()
        self.orchestrator = SimpleNamespace(config={})
        self.session_state = {}
        self.config = {"agents": {}}
        self.spawn = None
        self.capabilities = {}

    def get(self, name):
        return {
            "context": self.context,
            "providers": {"openai": self.provider},
            "orchestrator": self.orchestrator,
        }.get(name)

    def get_capability(self, name):
        if name == "session.spawn":
            return self.spawn
        return self.capabilities.get(name)


def _service(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    coordinator = _Coordinator()
    session = SimpleNamespace(config={"session": {}}, coordinator=coordinator)
    store.save(
        "root-session",
        coordinator.context.messages,
        {"session_id": "root-session", "bundle": "foundation"},
    )
    return (
        CoreCommandService(
            session=session,
            coordinator=coordinator,
            session_id="root-session",
            bundle_name="foundation",
            cwd=tmp_path,
            store=store,
        ),
        coordinator,
        store,
    )


@pytest.mark.asyncio
async def test_model_and_effort_mutate_live_runtime_and_metadata(tmp_path):
    service, coordinator, store = _service(tmp_path)

    model = await service.execute("/model", "gpt-5.5")
    await service.execute("/effort", "high")

    assert model.transient is True
    assert coordinator.provider.default_model == "gpt-5.5"
    assert coordinator.provider.config["default_model"] == "gpt-5.5"
    assert coordinator.orchestrator.config["reasoning_effort"] == "high"
    metadata = store.get_metadata("root-session")
    assert metadata["model"] == "gpt-5.5"
    assert metadata["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_strength_maps_max_alias_to_provider_supported_xhigh(tmp_path):
    service, coordinator, _ = _service(tmp_path)

    result = await service.execute("/effort", "xhigh")
    alias = await service.execute("/strength", "max")

    assert result.text == "Reasoning effort: xhigh"
    assert coordinator.orchestrator.config["reasoning_effort"] == "xhigh"
    assert alias.text == "Reasoning effort: xhigh"


@pytest.mark.asyncio
async def test_model_query_lists_active_and_provider_advertised_models(tmp_path):
    service, _, _ = _service(tmp_path)

    result = await service.execute("/model", "")

    assert "Active model" in result.text
    assert "openai · old-model" in result.text
    assert "available · gpt-5.5, gpt-5.6" in result.text
    assert service.model_names == ("old-model", "gpt-5.5", "gpt-5.6")


@pytest.mark.asyncio
async def test_btw_calls_provider_with_only_the_side_question(tmp_path):
    service, coordinator, _ = _service(tmp_path)

    result = await service.execute("/btw", "what time is it?")

    assert result.text == "isolated answer"
    request = coordinator.provider.request
    assert len(request.messages) == 1
    assert request.messages[0].content == "what time is it?"
    assert request.metadata["context_messages"] == 0
    assert coordinator.context.messages[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_clear_supports_name_and_clears_real_context(tmp_path):
    service, coordinator, store = _service(tmp_path)

    result = await service.execute("/clear", "fresh work")

    assert "2 messages removed" in result.text
    assert coordinator.context.messages == []
    assert store.get_metadata("root-session")["name"] == "fresh work"


@pytest.mark.asyncio
async def test_branch_and_export_write_resumable_session_artifacts(tmp_path):
    service, _, store = _service(tmp_path)

    branch = await service.execute("/branch", "experiment")
    exported = await service.execute("/export", "markdown transcript.md")

    branch_id = branch.text.split(" · ")[1]
    resolved = store.find_session(branch_id)
    transcript, metadata = store.load(resolved)
    assert len(transcript) == 2
    assert metadata["parent_id"] == "root-session"
    export_path = Path(exported.text.removeprefix("Session exported: "))
    assert "## User" in export_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_init_never_overwrites_existing_project_memory(tmp_path):
    service, _, _ = _service(tmp_path)

    first = await service.execute("/init", "")
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    second = await service.execute("/init", "")

    assert "initialized" in first.text
    assert "already exists" in second.text
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_compact_reports_ephemeral_backend_without_claiming_change(tmp_path):
    service, _, _ = _service(tmp_path)

    result = await service.execute("/compact", "")

    assert "no persistent change" in result.text
    assert "ephemerally" in result.text


@pytest.mark.asyncio
async def test_compact_persists_provider_summary_when_backend_supports_replace(
    tmp_path,
):
    service, coordinator, _ = _service(tmp_path)
    coordinator.context.messages = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"turn {index}"}
        for index in range(10)
    ]

    async def set_messages(messages):
        coordinator.context.messages = list(messages)

    coordinator.context.set_messages = set_messages

    result = await service.execute("/compact", "database migration")

    assert "persistently" in result.text
    assert len(coordinator.context.messages) == 5
    assert coordinator.context.messages[0]["role"] == "system"
    assert "isolated answer" in coordinator.context.messages[0]["content"]


@pytest.mark.asyncio
async def test_fork_starts_real_self_session_with_full_parent_context(tmp_path):
    service, coordinator, _ = _service(tmp_path)
    spawned = asyncio.Event()
    received = {}

    async def spawn(**kwargs):
        received.update(kwargs)
        spawned.set()
        return {"output": "done", "session_id": kwargs["sub_session_id"]}

    coordinator.spawn = spawn

    result = await service.execute("/fork", "compare both designs")
    await asyncio.wait_for(spawned.wait(), timeout=1)
    await asyncio.sleep(0)

    assert result.transient is True
    assert received["agent_name"] == "self"
    assert received["self_delegation_depth"] == 1
    assert received["parent_messages"] == coordinator.context.messages
    assert "compare both designs" in received["instruction"]
    assert "hello" in received["instruction"]


@pytest.mark.asyncio
async def test_background_activates_completion_notification(tmp_path):
    service, coordinator, _ = _service(tmp_path)
    marked = []
    coordinator.capabilities["ui.background"] = lambda: marked.append(True) or True

    result = await service.execute("/background", "")

    assert result.transient is True
    assert "detached to a shell" in result.text
    assert marked == [True]


@pytest.mark.asyncio
async def test_resume_requests_in_place_session_switch(tmp_path):
    service, coordinator, store = _service(tmp_path)
    store.save(
        "other-session",
        [{"role": "user", "content": "prior"}],
        {"session_id": "other-session", "bundle": "foundation"},
    )
    requested = []
    coordinator.capabilities["ui.resume"] = requested.append

    result = await service.execute("/resume", "other")

    assert result.transient is True
    assert requested == ["other-session"]
    assert "Switching to session" in result.text
