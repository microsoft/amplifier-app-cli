"""Tests for write-side provider persistence in session metadata.

Wave 2 of the cross-provider resume hardening set (amplifier-support#208):
this repo's metadata-writing code -- both the interactive `_save_session()`
closure inside `interactive_chat()` and the single-shot save block inside
`execute_single()` -- must persist the active `"provider"` identity
alongside the existing `"model"` field. Without this, the resume-time
mismatch check (session_runner._warn_on_resume_provider_mismatch) has
nothing new to compare against for sessions saved going forward.

Both write sites derive `"provider"` from `get_effective_config_summary()`
(`.provider_module`) -- the same function the read-side check uses to
compute the ACTIVE provider at resume -- so write-side and read-side always
agree on what "provider identity" means and how it's shaped (module id form,
e.g. "provider-anthropic").
"""

from __future__ import annotations

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
    mock_session.coordinator.cancellation = MagicMock()
    mock_session.coordinator.cancellation.is_cancelled = False
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
    async def test_metadata_includes_provider_matching_config(self, tmp_path: Path):
        """The saved metadata's 'provider' matches the resolved config's provider_module."""
        from amplifier_app_cli.main import execute_single

        hooks = _make_mock_hooks()
        session = _make_mock_session(hooks)
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
        assert saved_metadata.get("provider") == "provider-anthropic"
        # Sanity: 'model' is still written too (pre-existing field, untouched by this PR).
        assert "model" in saved_metadata

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
        assert saved_metadata["name"] == "my-named-session"
        assert saved_metadata["description"] == "a test session"
