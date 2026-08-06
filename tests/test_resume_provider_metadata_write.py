"""Tests for write-side provider persistence in session metadata.

Wave 2 of the cross-provider resume hardening set (amplifier-support#208):
this repo's metadata-writing code -- both the interactive `_save_session()`
closure inside `interactive_chat()` and the single-shot save block inside
`execute_single()` -- must persist the active `"provider"` identity
alongside the existing `"model"` field. Without this, the resume-time
mismatch check (session_runner._warn_on_resume_provider_mismatch) has
nothing new to compare against for sessions saved going forward.

Both write sites derive `"provider"` and `"model"` as one pair from
`get_effective_config_summary()` -- the same function the read-side check uses
at resume -- so metadata stores the provider module id and bare model value
that the comparison expects (e.g. `"provider-anthropic"` and `"claude-x"`).
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MODULE = "amplifier_app_cli.main"


def _make_mock_hooks() -> MagicMock:
    """Return a mock hooks registry whose emit() is an inert AsyncMock."""
    mock = MagicMock()
    mock.emit = AsyncMock(return_value=None)
    return mock


def _make_mock_session(hooks: MagicMock, providers: dict | None = None) -> MagicMock:
    """Return a minimal mock AmplifierSession suitable for execute_single()."""
    mock_ctx = MagicMock()
    mock_ctx.get_messages = AsyncMock(return_value=[{"role": "user", "content": "Hi"}])

    def _coordinator_get(key: str):
        if key == "hooks":
            return hooks
        if key == "context":
            return mock_ctx
        if key == "providers":
            return providers if providers is not None else {}
        return None

    mock_session = MagicMock()
    mock_session.session_id = "test-session-id"
    mock_session.execute = AsyncMock(return_value="Hello!")
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.get = _coordinator_get
    mock_session.coordinator.get_capability.return_value = None
    mock_session.coordinator.session_state = {}
    mock_session.coordinator.cancellation = MagicMock()
    mock_session.coordinator.cancellation.is_cancelled = False
    mock_session.coordinator.cancellation.is_immediate = False
    return mock_session


def _make_mock_initialized(session: MagicMock) -> MagicMock:
    """Return a minimal InitializedSession mock wrapping *session*."""
    mock = MagicMock()
    mock.session = session
    mock.session_id = "test-session-id"
    mock.cleanup = AsyncMock()
    return mock


class TestExecuteSingleWritesProvider:
    """execute_single()'s final metadata-save block persists 'provider'."""

    @pytest.mark.asyncio
    async def test_metadata_writes_bare_effective_pair_and_resumes_silently(
        self, tmp_path: Path
    ):
        """Single-shot metadata round-trips through resume without a false mismatch."""
        from amplifier_app_cli.main import execute_single
        from amplifier_app_cli.session_runner import (
            SessionConfig,
            _warn_on_resume_provider_mismatch,
        )

        hooks = _make_mock_hooks()
        runtime_provider = MagicMock()
        runtime_provider.model = "claude-x"
        session = _make_mock_session(hooks, providers={"anthropic": runtime_provider})
        initialized = _make_mock_initialized(session)

        config = {
            "providers": [
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-x", "priority": 0},
                }
            ]
        }

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=lambda _s, p: p),
            ),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await execute_single(
                prompt="Hi",
                config=config,
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="bundle:test",
            )

        store_instance.save.assert_called_once()
        _saved_id, _saved_messages, saved_metadata = store_instance.save.call_args[0]
        assert saved_metadata["provider"] == "provider-anthropic"
        assert saved_metadata["model"] == "claude-x"
        assert saved_metadata["model"] != "anthropic/claude-x"

        # Feed the write-side result directly to the read-side resume check. The
        # same effective config must be silent: no warning and no confirmation.
        resume_config = SessionConfig(
            config=config,
            search_paths=[],
            verbose=False,
            bundle_name="bundle:test",
            initial_transcript=[{"role": "user", "content": "Hi"}],
        )
        resume_console = MagicMock()
        resume_session = MagicMock()
        with (
            patch("amplifier_app_cli.session_runner.SessionStore") as ResumeStore,
            patch("amplifier_app_cli.session_runner.click.confirm") as mock_confirm,
        ):
            ResumeStore.return_value.get_metadata.return_value = saved_metadata
            await _warn_on_resume_provider_mismatch(
                resume_config,
                "test-session-id",
                resume_console,
                resume_session,
            )

        resume_console.print.assert_not_called()
        mock_confirm.assert_not_called()

    @pytest.mark.asyncio
    async def test_metadata_preserves_existing_fields_alongside_provider(
        self, tmp_path: Path
    ):
        """Existing metadata fields (name, description, ...) survive the save,
        with 'provider' added alongside them -- not replacing them."""
        from amplifier_app_cli.main import execute_single

        hooks = _make_mock_hooks()
        session = _make_mock_session(hooks)
        initialized = _make_mock_initialized(session)

        config = {
            "providers": [
                {
                    "module": "provider-openai",
                    "config": {"default_model": "gpt-x", "priority": 0},
                }
            ]
        }

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=lambda _s, p: p),
            ),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {
                "name": "my-named-session",
                "description": "a test session",
            }
            store_instance.save.return_value = None

            await execute_single(
                prompt="Hi",
                config=config,
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="bundle:test",
            )

        _saved_id, _saved_messages, saved_metadata = store_instance.save.call_args[0]
        assert saved_metadata["provider"] == "provider-openai"
        assert saved_metadata["model"] == "gpt-x"
        assert saved_metadata["name"] == "my-named-session"
        assert saved_metadata["description"] == "a test session"


class TestInteractiveChatWritesProviderPair:
    """interactive_chat() persists provider and model from one effective summary."""

    @pytest.mark.asyncio
    async def test_metadata_uses_effective_pair_not_first_provider(
        self, tmp_path: Path
    ):
        """The interactive write site cannot combine two different providers."""
        from amplifier_app_cli.main import interactive_chat

        hooks = _make_mock_hooks()
        session = _make_mock_session(hooks)
        initialized = _make_mock_initialized(session)
        prompt_session = MagicMock()
        prompt_session.prompt_async = AsyncMock(side_effect=EOFError)
        steering_manager = MagicMock()
        steering_manager.run = AsyncMock(return_value=None)

        # The first list entry deliberately disagrees with the effective summary.
        # This catches the old implementation that read model from providers[0]
        # while reading provider from the priority-resolved summary.
        config = {
            "providers": [
                {
                    "module": "provider-openai",
                    "config": {"default_model": "wrong-first-model", "priority": 10},
                },
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-effective", "priority": 0},
                },
            ]
        }
        effective_summary = MagicMock()
        effective_summary.provider_module = "provider-anthropic"
        effective_summary.model = "claude-effective"

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(
                f"{_MODULE}.get_effective_config_summary",
                return_value=effective_summary,
            ) as mock_summary,
            patch(f"{_MODULE}._create_prompt_session", return_value=prompt_session),
            patch(
                f"{_MODULE}.patch_stdout",
                side_effect=lambda *args, **kwargs: nullcontext(),
            ),
            patch(f"{_MODULE}.signal.signal", return_value=MagicMock()),
            patch(f"{_MODULE}.close_dedicated_tty_input"),
            patch(
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=lambda _s, p: p),
            ),
            patch("amplifier_app_cli.incremental_save.register_incremental_save"),
            patch("amplifier_app_cli.goal_progress_hook.register_goal_progress_hook"),
            patch(
                "amplifier_app_cli.steering_input.SteeringInputManager",
                return_value=steering_manager,
            ),
            patch("amplifier_app_cli.ui.render_message"),
            patch(
                "amplifier_foundation.session.diagnose_transcript",
                return_value={"status": "ok"},
            ),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {"name": "keep-me"}

            await interactive_chat(
                config=config,
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="bundle:test",
                initial_prompt="Hi",
                initial_transcript=[],
            )

        store_instance.save.assert_called_once()
        _saved_id, _saved_messages, saved_metadata = store_instance.save.call_args[0]
        assert saved_metadata["provider"] == "provider-anthropic"
        assert saved_metadata["model"] == "claude-effective"
        assert saved_metadata["name"] == "keep-me"
        mock_summary.assert_called_once_with(config, "bundle:test")
