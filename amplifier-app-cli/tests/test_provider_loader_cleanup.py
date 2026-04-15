"""Tests for event loop cleanup in get_provider_models().

Verifies that the _list_and_cleanup() wrapper properly calls close() on
providers that support it, and handles all edge cases gracefully.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================
# Helpers
# ============================================================


def _make_async_provider(models, close_side_effect=None, has_close=True):
    """Create a mock provider with an async list_models and optional close().

    Args:
        models: List to return from list_models().
        close_side_effect: If set, close() raises this exception.
        has_close: If False, provider has no close() method at all.
    """
    provider = MagicMock()
    provider.list_models = AsyncMock(return_value=models)

    if has_close:
        provider.close = AsyncMock(side_effect=close_side_effect)
    else:
        # Remove close so hasattr(provider, 'close') returns False
        del provider.close

    return provider


# ============================================================
# Task 1: _list_and_cleanup() wrapper behavior
# ============================================================


class TestGetProviderModelsCleanup:
    """Tests for the cleanup wrapper in get_provider_models()."""

    def test_close_called_after_successful_list_models(self):
        """When provider has close(), it should be called after list_models() succeeds."""
        from amplifier_app_cli.provider_loader import get_provider_models

        mock_models = [MagicMock(id="model-1"), MagicMock(id="model-2")]
        provider = _make_async_provider(mock_models)

        with (
            patch(
                "amplifier_app_cli.provider_loader.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_loader._try_instantiate_provider",
                return_value=provider,
            ),
        ):
            result = get_provider_models("test-provider")

        assert result == mock_models, f"Expected {mock_models}, got {result}"
        assert provider.close.await_count == 1, "close() should be called exactly once"

    def test_close_called_when_list_models_raises(self):
        """When provider has close(), it should be called even if list_models() raises."""
        from amplifier_app_cli.provider_loader import get_provider_models

        provider = _make_async_provider(models=[])
        provider.list_models = AsyncMock(side_effect=RuntimeError("API down"))

        with (
            patch(
                "amplifier_app_cli.provider_loader.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_loader._try_instantiate_provider",
                return_value=provider,
            ),
        ):
            with pytest.raises(RuntimeError, match="API down"):
                get_provider_models("test-provider")

        assert provider.close.await_count == 1, (
            "close() should be called even when list_models() raises"
        )

    def test_no_error_when_provider_has_no_close(self):
        """When provider has no close() method, no error should occur."""
        from amplifier_app_cli.provider_loader import get_provider_models

        mock_models = [MagicMock(id="model-1")]
        provider = _make_async_provider(mock_models, has_close=False)

        with (
            patch(
                "amplifier_app_cli.provider_loader.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_loader._try_instantiate_provider",
                return_value=provider,
            ),
        ):
            result = get_provider_models("test-provider")

        assert result == mock_models, f"Expected {mock_models}, got {result}"
        assert not hasattr(provider, "close"), "Provider should not have close()"

    def test_close_failure_does_not_mask_list_models_result(self):
        """When close() raises, the list_models() result should still be returned."""
        from amplifier_app_cli.provider_loader import get_provider_models

        mock_models = [MagicMock(id="model-1")]
        provider = _make_async_provider(
            mock_models, close_side_effect=RuntimeError("close failed")
        )

        with (
            patch(
                "amplifier_app_cli.provider_loader.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_loader._try_instantiate_provider",
                return_value=provider,
            ),
        ):
            result = get_provider_models("test-provider")

        assert result == mock_models, (
            f"close() failure should not mask result. Expected {mock_models}, got {result}"
        )

    def test_list_models_exception_propagates_over_close_exception(self):
        """When both list_models() and close() raise, list_models() exception wins."""
        from amplifier_app_cli.provider_loader import get_provider_models

        provider = _make_async_provider(models=[])
        provider.list_models = AsyncMock(side_effect=ValueError("Auth token expired"))
        provider.close = AsyncMock(side_effect=RuntimeError("close also failed"))

        with (
            patch(
                "amplifier_app_cli.provider_loader.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_loader._try_instantiate_provider",
                return_value=provider,
            ),
        ):
            with pytest.raises(ValueError, match="Auth token expired"):
                get_provider_models("test-provider")

    def test_sync_list_models_skips_cleanup(self):
        """When list_models is synchronous, the cleanup wrapper is not used."""
        from amplifier_app_cli.provider_loader import get_provider_models

        mock_models = [MagicMock(id="sync-model")]
        provider = MagicMock()
        provider.list_models = MagicMock(return_value=mock_models)  # sync, not async
        provider.close = AsyncMock()  # has close but it's irrelevant for sync path

        with (
            patch(
                "amplifier_app_cli.provider_loader.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_loader._try_instantiate_provider",
                return_value=provider,
            ),
        ):
            result = get_provider_models("test-provider")

        assert result == mock_models, f"Expected {mock_models}, got {result}"
        # close() should NOT have been called (sync path doesn't use the wrapper)
        provider.close.assert_not_awaited()
