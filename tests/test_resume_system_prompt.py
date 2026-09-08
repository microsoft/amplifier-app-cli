"""Tests for system-prompt re-injection on sub-session resume.

Bug: resumed sub-sessions ran with NO system prompt at all.

The spawn path (spawn_sub_session) registers the agent's system instruction
via a *factory* (context.set_system_prompt_factory) rather than a persisted
message. context-simple builds the system message into a per-request COPY
and never writes it into self.messages -- so it is never present in
transcript.jsonl. SessionStore._save_transcript also explicitly skips
system/developer role messages when persisting, so this holds even for a
context module using the add_message() fallback.

resume_sub_session only ever restored the transcript. It never re-derived
or re-registered the system instruction, so every resumed sub-session ran
every subsequent request with system_msgs == [] -- and a live API probe
confirmed omitting the system prompt on a chained request CLEARS the
provider's server-held prompt rather than preserving it (see PR for the
full evidence chain).

These tests exercise the actual resume_sub_session() code path using the
same capability-registration-integration style as
TestCapabilityRegistrationIntegration in test_session_spawner.py: a fully
mocked AmplifierSession/coordinator plus a small fake context standing in
for context-simple, so we can assert directly on the system-prompt
registration contract without needing a real provider or context module.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from amplifier_app_cli.session_spawner import resume_sub_session
from amplifier_app_cli.session_spawner import BASE_SYSTEM_PROMPT_BUILDER_CAPABILITY
from amplifier_app_cli.session_spawner import SYSTEM_PROMPT_SNAPSHOT_KEY
from amplifier_app_cli.session_spawner import SYSTEM_PROMPT_SNAPSHOT_SCHEMA
from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.anyio

SENTINEL_INSTRUCTION = "SENTINEL-INSTRUCTION-42: you are the test agent."


@pytest.fixture(scope="module")
def anyio_backend():
    """Configure anyio to use asyncio backend only."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _home(isolated_home):
    """Every test here reaches SessionStore() -> Path.home(). See conftest.isolated_home."""
    return isolated_home


class _FakeContextWithFactory:
    """Stands in for context-simple: supports the factory-based system prompt."""

    def __init__(self) -> None:
        self.factory = None
        self.messages: list[dict] = []

    async def set_system_prompt_factory(self, factory) -> None:
        self.factory = factory

    async def add_message(self, message: dict) -> None:
        self.messages.append(message)

    async def get_messages(self) -> list[dict]:
        return self.messages


class _FakeContextAddMessageOnly:
    """Stands in for a context module with NO factory support (fallback path).

    Deliberately has no set_system_prompt_factory attribute at all, so
    hasattr(context, "set_system_prompt_factory") is False, exactly like the
    hasattr guard in resume_sub_session / spawn_sub_session.
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def add_message(self, message: dict) -> None:
        self.messages.append(message)

    async def get_messages(self) -> list[dict]:
        return self.messages


def _base_metadata(session_id: str, **overrides) -> dict:
    metadata = {
        "session_id": session_id,
        "parent_id": "parent-123",
        "agent_name": "test-agent",
        "config": {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"}
        },
        "working_dir": "/test/project",
        "self_delegation_depth": 0,
    }
    metadata.update(overrides)
    return metadata


async def _resume_with_fake_context(
    fake_context, session_id: str, instruction: str = "follow-up"
) -> dict:
    """Run resume_sub_session() against a fully mocked AmplifierSession.

    `fake_context` is wired in as the resumed session's "context" capability
    (mirrors coordinator.get("context") in the production code).
    """

    def mock_get(name):
        if name == "context":
            return fake_context
        return None

    mock_coordinator = MagicMock()
    registered_capabilities = {}
    mock_coordinator.register_capability = MagicMock(
        side_effect=lambda name, value: registered_capabilities.setdefault(name, value)
    )
    # mention_resolver intentionally returns None: the mention-expansion
    # branch (both for the follow-up instruction AND the system instruction)
    # is exercised elsewhere (test_session_spawner.py); returning None here
    # keeps these tests focused on the factory/add_message wiring itself.
    mock_coordinator.get_capability = MagicMock(return_value=None)
    mock_coordinator.get = MagicMock(side_effect=mock_get)
    mock_coordinator.mount = AsyncMock()

    mock_session = MagicMock()
    mock_session.coordinator = mock_coordinator
    mock_session.initialize = AsyncMock()
    mock_session.execute = AsyncMock(return_value="response")
    mock_session.cleanup = AsyncMock()

    with patch(
        "amplifier_app_cli.session_spawner.AmplifierSession",
        return_value=mock_session,
    ):
        with patch("amplifier_app_cli.ui.CLIApprovalSystem"):
            with patch("amplifier_app_cli.ui.CLIDisplaySystem"):
                with patch("amplifier_app_cli.paths.create_foundation_resolver"):
                    await resume_sub_session(session_id, instruction)
    return registered_capabilities


class TestResumeSystemPromptReinjection:
    """resume_sub_session must re-register the agent's system prompt.

    Regression coverage for the silent system-prompt-loss bug: a resumed
    sub-session ran every subsequent request with system_msgs == [] because
    only the transcript (never the system instruction) was restored.
    """

    async def test_resume_reregisters_system_prompt_via_factory(
        self, tmp_path, monkeypatch
    ):
        """Factory-capable context: resume must call set_system_prompt_factory
        with a factory that reproduces the original agent instruction.

        FAILS BEFORE THE FIX: resume_sub_session never calls
        set_system_prompt_factory at all, so fake_context.factory stays None.
        """

        store = SessionStore()
        session_id = "test-resume-system-prompt-factory"
        # Transcript deliberately has ZERO system-role messages -- this is
        # exactly what SessionStore._save_transcript always produces (it
        # skips system/developer messages), and what every real resumed
        # session actually persists.
        transcript = [{"role": "user", "content": "hi"}]
        metadata = _base_metadata(
            session_id,
            agent_overlay={"instruction": SENTINEL_INSTRUCTION},
        )
        store.save(session_id, transcript, metadata)

        fake_context = _FakeContextWithFactory()
        await _resume_with_fake_context(fake_context, session_id)

        assert fake_context.factory is not None, (
            "resume_sub_session must call set_system_prompt_factory() so "
            "the resumed session has a system prompt on every subsequent "
            "request -- this is the fix for the silent system-prompt-loss "
            "bug (resumed sub-agents ran with no system prompt)."
        )
        produced = await fake_context.factory()
        assert SENTINEL_INSTRUCTION in produced

    async def test_resume_adds_system_message_when_no_factory_support(
        self, tmp_path, monkeypatch
    ):
        """Fallback path: a context without factory support gets an
        add_message() system-role message instead (mirrors the spawn path's
        own hasattr-gated fallback).
        """

        store = SessionStore()
        session_id = "test-resume-system-prompt-fallback"
        transcript = [{"role": "user", "content": "hi"}]
        metadata = _base_metadata(
            session_id,
            agent_overlay={"instruction": SENTINEL_INSTRUCTION},
        )
        store.save(session_id, transcript, metadata)

        fake_context = _FakeContextAddMessageOnly()
        await _resume_with_fake_context(fake_context, session_id)

        system_messages = [
            m for m in fake_context.messages if m.get("role") == "system"
        ]
        assert system_messages, (
            "resume_sub_session must add a system-role message when the "
            "context module has no set_system_prompt_factory support."
        )
        assert SENTINEL_INSTRUCTION in system_messages[0]["content"]

        # The system message must land before the restored transcript
        # history, mirroring how a live conversation is structured (system
        # message first, then user/assistant turns).
        assert fake_context.messages[0]["role"] == "system"

    async def test_resume_falls_back_to_merged_config_agents_map(
        self, tmp_path, monkeypatch
    ):
        """When agent_overlay carries no instruction (e.g. an empty
        inherit-as-is overlay, or a session saved before agent_overlay
        existed), fall back to config.agents[<agent_name>].instruction.
        """

        store = SessionStore()
        session_id = "test-resume-system-prompt-config-fallback"
        transcript: list[dict] = []
        metadata = _base_metadata(session_id, agent_overlay={})
        metadata["config"]["agents"] = {
            "test-agent": {"instruction": SENTINEL_INSTRUCTION}
        }
        store.save(session_id, transcript, metadata)

        fake_context = _FakeContextWithFactory()
        await _resume_with_fake_context(fake_context, session_id)

        assert fake_context.factory is not None
        produced = await fake_context.factory()
        assert SENTINEL_INSTRUCTION in produced

    async def test_resume_snapshot_wins_over_legacy_named_instruction(
        self, tmp_path, monkeypatch
    ):
        """A resolved snapshot preserves self identity without re-expanding."""
        store = SessionStore()
        session_id = "test-resume-frozen-base-snapshot"
        metadata = _base_metadata(
            session_id,
            agent_name="self",
            agent_overlay={"instruction": "LEGACY_NAMED_PERSONA"},
            **{
                SYSTEM_PROMPT_SNAPSHOT_KEY: {
                    "schema": SYSTEM_PROMPT_SNAPSHOT_SCHEMA,
                    "content": "ROOT_BASE_SNAPSHOT",
                }
            },
        )
        store.save(session_id, [], metadata)

        fake_context = _FakeContextWithFactory()
        registered_capabilities = await _resume_with_fake_context(
            fake_context, session_id
        )

        assert fake_context.factory is not None
        assert await fake_context.factory() == "ROOT_BASE_SNAPSHOT"
        nested_factory = registered_capabilities[
            BASE_SYSTEM_PROMPT_BUILDER_CAPABILITY
        ](MagicMock())
        assert await nested_factory() == "ROOT_BASE_SNAPSHOT"

    @pytest.mark.parametrize("has_parent", [False, True])
    @pytest.mark.parametrize(
        "snapshot",
        [
            None,
            {"schema": SYSTEM_PROMPT_SNAPSHOT_SCHEMA + 1, "content": "OLD_ROOT"},
        ],
    )
    async def test_historic_self_without_valid_snapshot_refuses_before_child_creation(
        self, tmp_path, monkeypatch, has_parent, snapshot
    ):
        """A current root must never supply identity to a historic self child."""
        store = SessionStore()
        session_id = "test-resume-historic-self-without-snapshot"
        metadata = _base_metadata(session_id, agent_name="self", agent_overlay={})
        if snapshot is not None:
            metadata[SYSTEM_PROMPT_SNAPSHOT_KEY] = snapshot
        store.save(session_id, [{"role": "user", "content": "saved turn"}], metadata)

        child = MagicMock()
        child.initialize = AsyncMock()
        child.execute = AsyncMock()
        child.cleanup = AsyncMock()
        parent = MagicMock() if has_parent else None

        with (
            patch(
                "amplifier_app_cli.session_spawner.AmplifierSession",
                return_value=child,
            ) as session_class,
            pytest.raises(RuntimeError, match="Start a new self delegation"),
        ):
            await resume_sub_session(
                session_id, "follow-up", parent_session=parent
            )

        session_class.assert_not_called()
        child.initialize.assert_not_awaited()
        child.execute.assert_not_awaited()
        child.cleanup.assert_not_awaited()

    async def test_resume_with_no_recoverable_instruction_warns_but_succeeds(
        self, tmp_path, monkeypatch, caplog
    ):
        """No instruction anywhere in metadata -> loud warning, resume still
        succeeds. Today this failure mode is completely silent; the fix
        must surface it rather than leaving the resumed session mute.
        """

        store = SessionStore()
        session_id = "test-resume-system-prompt-missing"
        transcript: list[dict] = []
        metadata = _base_metadata(session_id)  # no agent_overlay at all
        store.save(session_id, transcript, metadata)

        fake_context = _FakeContextWithFactory()
        with caplog.at_level(logging.WARNING):
            await _resume_with_fake_context(fake_context, session_id)

        assert fake_context.factory is None
        assert any(
            "system" in record.getMessage().lower()
            and "no" in record.getMessage().lower()
            for record in caplog.records
        ), "Missing system instruction on resume must be logged loudly, not silently swallowed."
