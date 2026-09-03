"""Resumed sub-sessions must keep their spawn-time provider promotion.

THE DEFECT (model_performance-rc0, confirmed on wire evidence)
--------------------------------------------------------------
A sub-session spawned with ``model_role``/``provider_preferences`` carries
``priority: 0`` on the promoted provider in its persisted mount plan.
``resume_sub_session`` re-applied live ``settings.yaml`` provider overrides on
top of that plan to restore redacted credentials -- but the merge it used
(``merge_module_items`` -> ``deep_merge``, "overlay winning conflicts")
re-imposed EVERY settings key, ``config.priority`` included.  The promotion
was overwritten and the resumed leg silently re-resolved to whatever sits at
settings priority 0.

Measured over a 2,078-session archive: 66 delegate sessions contain a
``session:resume``; 39 (59%) changed model across the boundary; 37/39
cheap -> expensive; every one reported ``basis: "priority"`` on BOTH sides
(a wipe, not a fallback).  0 of 179 root-session resumes were affected --
a root plan has no promotion to lose.

The wire fingerprint that identified the merge as the culprit: across the
boundary the promoted provider's config was identical in every key EXCEPT
those that ``settings.yaml`` declares.  Keys present only in the preference's
own config (``enable_response_chaining``, ``prompt_cache_retention``)
survived; ``priority`` -- declared in settings -- did not.  That is
``deep_merge(persisted, settings_override)`` semantics and nothing else.

WHAT THIS MODULE COVERS
-----------------------
End-to-end behaviour of the real ``resume_sub_session()`` code path, for both
fixes:

A. The drop site -- the resume credential refresh is narrowed to secret-bearing
   keys, so it can no longer clobber ``priority`` (or any other per-candidate
   config key). Isolated by
   ``test_promotion_survives_with_no_recoverable_preferences``, which leaves no
   preference anywhere for fix B to rebuild from.
B. The threading -- ``provider_preferences``/``model_role`` now reach the resume
   path, so the promotion is REBUILT each leg rather than merely surviving.

This module deliberately imports NO symbol introduced by either fix, so a
fail-before run against the pre-fix tree produces real assertion failures
rather than a collection error. Unit coverage of the narrowing helper itself
lives in ``test_narrow_overrides_to_secrets.py``.

No API calls anywhere in this module.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from amplifier_app_cli.session_spawner import resume_sub_session
from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    """Configure anyio to use asyncio backend only."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _home(isolated_home):
    """Every test here reaches SessionStore() -> Path.home(). See conftest.isolated_home."""
    return isolated_home


# ---------------------------------------------------------------------------
# Fixtures modelled on the rc0 capture
#   20260901-rebaseline/runs/val-rb-oai-sol-xhigh-s1-01
#   .../0000000000000000-25443a97b60d4965_anchors-amp-dev-git-ops
# leg 1: luna priority 0 / sol priority 1 -> 13 x gpt-5.6-luna
# leg 2: luna priority 14 / sol priority 0 -> 25 x gpt-5.6-sol
# ---------------------------------------------------------------------------

PROMOTED = "provider-luna"  # the cheap tier the role promoted
SETTINGS_ZERO = "provider-sol"  # what settings puts at priority 0


def _persisted_child_providers() -> list[dict]:
    """The child's mount plan as spawn built it, then redaction persisted it.

    ``priority: 0`` on luna is the promotion; sol was demoted to 1.
    ``api_key`` is the sentinel redact_secrets() writes to disk.
    """
    return [
        {
            "module": PROMOTED,
            "config": {
                "api_key": "[REDACTED]",
                "default_model": "gpt-5.6-luna",
                "priority": 0,
                "reasoning_effort": "medium",
                # Present ONLY in the preference's own config -- never in
                # settings.yaml. These survived the wipe in the capture and
                # are what proved the child's config was merged, not rebuilt.
                "enable_response_chaining": "auto",
                "prompt_cache_retention": "in_memory",
            },
        },
        {
            "module": SETTINGS_ZERO,
            "config": {
                "api_key": "[REDACTED]",
                "default_model": "gpt-5.6-sol",
                "priority": 1,
            },
        },
    ]


def _live_settings_overrides() -> list[dict]:
    """Live settings.yaml: real keys, plus the priorities that did the damage.

    ``reasoning_effort`` differs from the child's persisted value on purpose:
    rc0 recorded that drift as INFERRED-NOT-CONFIRMED because the capture had
    the same effort on both sides. Differing values settle it here.
    """
    return [
        {
            "module": PROMOTED,
            "config": {
                "api_key": "sk-live-luna",
                "priority": 14,
                "reasoning_effort": "high",
            },
        },
        {
            "module": SETTINGS_ZERO,
            "config": {
                "api_key": "sk-live-sol",
                "priority": 0,
            },
        },
    ]


def _by_module(providers: list[dict], module: str) -> dict:
    return next(p for p in providers if p["module"] == module)


# ---------------------------------------------------------------------------
# Fix B -- threading role/preferences through resume
# ---------------------------------------------------------------------------


def _base_metadata(session_id: str, **overrides) -> dict:
    metadata = {
        "session_id": session_id,
        "parent_id": "parent-123",
        "agent_name": "git-ops",
        "config": {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            "providers": _persisted_child_providers(),
        },
        "working_dir": "/test/project",
        "self_delegation_depth": 0,
    }
    metadata.update(overrides)
    return metadata


class _FakeContext:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def set_system_prompt_factory(self, factory) -> None:
        return None

    async def add_message(self, message: dict) -> None:
        self.messages.append(message)

    async def get_messages(self) -> list[dict]:
        return self.messages


class _RecordingHooks:
    """Minimal stand-in for the hook registry: records every emit()."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []
        self.registered: list[str] = []

    async def emit(self, event: str, data: dict) -> None:
        self.emitted.append((event, data))

    def register(self, event: str, handler, priority: int = 0, name: str = ""):
        self.registered.append(event)
        return lambda: None


async def _run_resume(
    session_id: str,
    *,
    provider_overrides: list[dict] | None = None,
    **resume_kwargs,
) -> tuple[dict, _RecordingHooks]:
    """Drive the real resume_sub_session() and capture the mounted config.

    Returns (config handed to AmplifierSession, hooks that recorded emits).
    """
    captured: dict = {}
    hooks = _RecordingHooks()
    fake_context = _FakeContext()

    def mock_get(name):
        if name == "context":
            return fake_context
        if name == "hooks":
            return hooks
        return None

    mock_coordinator = MagicMock()
    mock_coordinator.register_capability = MagicMock()
    mock_coordinator.get_capability = MagicMock(return_value=None)
    mock_coordinator.get = MagicMock(side_effect=mock_get)
    mock_coordinator.mount = AsyncMock()

    mock_session = MagicMock()
    mock_session.coordinator = mock_coordinator
    mock_session.initialize = AsyncMock()
    mock_session.execute = AsyncMock(return_value="response")
    mock_session.cleanup = AsyncMock()

    def _capture(*args, **kwargs):
        captured["config"] = kwargs.get("config")
        return mock_session

    mock_settings = MagicMock()
    mock_settings.get_provider_overrides = MagicMock(
        return_value=provider_overrides if provider_overrides is not None else []
    )
    mock_settings.get_config_overrides = MagicMock(return_value={})
    mock_settings.get_notification_hook_overrides = MagicMock(return_value=[])

    with (
        patch(
            "amplifier_app_cli.session_spawner.AmplifierSession", side_effect=_capture
        ),
        patch("amplifier_app_cli.lib.settings.AppSettings", return_value=mock_settings),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
    ):
        await resume_sub_session(session_id, "follow-up", **resume_kwargs)

    return captured["config"], hooks


class TestResumeRebuildsPromotion:
    """The resume path must re-apply the promotion, not merely preserve it."""

    async def test_resumed_leg_keeps_its_model_role_promotion(
        self, tmp_path, monkeypatch
    ):
        """THE headline regression test. FAILS BEFORE THE FIX.

        Before: the settings merge wiped ``priority: 0`` and nothing on the
        resume path could rebuild it -- ``apply_provider_preferences_with_
        resolution`` was reachable only from spawn. The resumed leg resolved
        to the settings priority-0 provider (sol), exactly as captured.
        """
        store = SessionStore()
        session_id = "test-resume-keeps-promotion"
        metadata = _base_metadata(
            session_id,
            agent_overlay={
                "model_role": ["fast", "general"],
                "provider_preferences": [
                    {"provider": "luna", "model": "gpt-5.6-luna"},
                ],
            },
        )
        store.save(session_id, [{"role": "user", "content": "hi"}], metadata)

        config, _ = await _run_resume(
            session_id, provider_overrides=_live_settings_overrides()
        )

        promoted = _by_module(config["providers"], PROMOTED)
        assert promoted["config"]["priority"] == 0, (
            "A resumed delegate must resolve to the SAME provider its spawn "
            "leg did. Landing on the settings priority-0 provider is the rc0 "
            "defect: 39/66 delegate resumes changed model, 37 cheap->expensive."
        )
        assert promoted["config"]["default_model"] == "gpt-5.6-luna"

    async def test_promotion_survives_with_no_recoverable_preferences(
        self, tmp_path, monkeypatch
    ):
        """Isolates FIX A through the production path. FAILS BEFORE FIX A.

        No preferences exist anywhere -- not threaded, not in the agent
        overlay, not in the mount plan -- so fix B cannot rebuild anything.
        The persisted promotion must survive the credential refresh on its
        own, and the credential must still be refreshed.
        """
        store = SessionStore()
        session_id = "test-resume-fix-a-isolated"
        store.save(session_id, [], _base_metadata(session_id))

        config, _ = await _run_resume(
            session_id, provider_overrides=_live_settings_overrides()
        )

        promoted = _by_module(config["providers"], PROMOTED)
        assert promoted["config"]["priority"] == 0, (
            "The persisted promotion must survive the resume credential "
            "refresh even when no preference can be recovered to rebuild it."
        )
        assert _by_module(config["providers"], SETTINGS_ZERO)["config"]["priority"] == 1
        assert promoted["config"]["reasoning_effort"] == "medium"
        # The refresh must still do the job it exists for.
        assert promoted["config"]["api_key"] == "sk-live-luna"

    async def test_explicit_preferences_argument_is_honoured(
        self, tmp_path, monkeypatch
    ):
        """The threaded argument wins over anything persisted.

        This is the hop the caller (tool-delegate's resume path) gains: it
        can now pass the same preferences it passes at spawn.
        """
        store = SessionStore()
        session_id = "test-resume-explicit-prefs"
        store.save(session_id, [], _base_metadata(session_id))

        from amplifier_foundation.spawn_utils import ProviderPreference

        config, _ = await _run_resume(
            session_id,
            provider_overrides=_live_settings_overrides(),
            provider_preferences=[
                ProviderPreference(provider="luna", model="gpt-5.6-luna")
            ],
        )

        assert _by_module(config["providers"], PROMOTED)["config"]["priority"] == 0

    async def test_preferences_recovered_from_persisted_mount_plan(
        self, tmp_path, monkeypatch
    ):
        """Sessions saved with no agent_overlay still recover their promotion.

        merge_configs copies the agent overlay's top-level keys into the
        merged mount plan, so ``provider_preferences`` is present there too --
        which is what the rc0 capture observed ("still luna ... simply never
        consulted again").
        """
        store = SessionStore()
        session_id = "test-resume-prefs-from-config"
        metadata = _base_metadata(session_id)
        metadata["config"]["provider_preferences"] = [
            {"provider": "luna", "model": "gpt-5.6-luna"}
        ]
        store.save(session_id, [], metadata)

        config, _ = await _run_resume(
            session_id, provider_overrides=_live_settings_overrides()
        )

        assert _by_module(config["providers"], PROMOTED)["config"]["priority"] == 0

    async def test_preference_config_is_reasserted_on_resume(
        self, tmp_path, monkeypatch
    ):
        """Per-candidate keys carried by the preference are re-applied.

        Settles rc0 section 4.6 from the other direction: the preference's own
        ``reasoning_effort`` -- not settings' -- governs the resumed leg.
        """
        store = SessionStore()
        session_id = "test-resume-pref-config"
        metadata = _base_metadata(
            session_id,
            agent_overlay={
                "provider_preferences": [
                    {
                        "provider": "luna",
                        "model": "gpt-5.6-luna",
                        "config": {"reasoning_effort": "low"},
                    }
                ]
            },
        )
        store.save(session_id, [], metadata)

        config, _ = await _run_resume(
            session_id, provider_overrides=_live_settings_overrides()
        )

        promoted = _by_module(config["providers"], PROMOTED)
        assert promoted["config"]["reasoning_effort"] == "low"

    async def test_model_role_is_written_into_the_resumed_config(
        self, tmp_path, monkeypatch
    ):
        """A threaded model_role reaches the resumed session's config.

        The resumed leg's routing hook resolves roles from config; without
        this the role the delegate was spawned with never reaches it.
        """
        store = SessionStore()
        session_id = "test-resume-model-role"
        store.save(session_id, [], _base_metadata(session_id))

        config, _ = await _run_resume(session_id, model_role="fast")

        assert config["model_role"] == ["fast"]

    async def test_unhonourable_promotion_emits_a_fallback_event(
        self, tmp_path, monkeypatch, caplog
    ):
        """Acceptance criterion: name the cause, do not silently re-resolve.

        When the pinned provider is not in the mount plan at all, the resumed
        leg still has to run on something -- but it must SAY so, naming the
        cause and the provider/model it actually landed on.
        """
        store = SessionStore()
        session_id = "test-resume-fallback-event"
        metadata = _base_metadata(
            session_id,
            agent_overlay={
                "provider_preferences": [
                    {"provider": "nonexistent", "model": "no-such-model"}
                ]
            },
        )
        store.save(session_id, [], metadata)

        with caplog.at_level(logging.WARNING):
            _, hooks = await _run_resume(session_id)

        fallbacks = [d for name, d in hooks.emitted if name == "provider:fallback"]
        assert fallbacks, (
            "An unhonourable promotion on resume must emit a named fallback "
            "event rather than silently re-resolving by settings priority."
        )
        payload = fallbacks[0]
        assert payload["reason"] == "preferred_provider_not_mounted"
        assert payload["requested"] == [
            {"provider": "nonexistent", "model": "no-such-model"}
        ]
        # It must name what the leg actually landed on.
        assert payload["provider"] == PROMOTED
        assert payload["model"] == "gpt-5.6-luna"

    async def test_no_preferences_leaves_the_plan_byte_identical(
        self, tmp_path, monkeypatch
    ):
        """Default behaviour is unchanged when nothing was ever promoted.

        This is the negative control that mirrors rc0's own: 0 of 179 root
        resumes were affected, because a plan with no promotion has nothing
        to preserve and nothing to rebuild.
        """
        store = SessionStore()
        session_id = "test-resume-no-prefs"
        metadata = _base_metadata(session_id)
        store.save(session_id, [], metadata)

        config, hooks = await _run_resume(session_id)

        assert config["providers"] == _persisted_child_providers()
        assert not [n for n, _ in hooks.emitted if n == "provider:fallback"]
