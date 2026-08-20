"""Tests for the read-only `/provider test [name]` and `/provider models
[name]` diagnostic subcommands.

Unlike `/provider use`/`/provider auto`, these are NEVER gated on the
'conversation.provider_pin' capability -- they are useful precisely when
pinning is unavailable or refusing (see amplifier_app_cli.main's
_handle_provider_test/_handle_provider_models docstrings). They also always
query THIS SESSION'S mounted providers (coordinator.get("providers")), never
settings.yaml.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import _make_command_processor
from test_provider_command import _cp_with, _make_pin, _visible


def _make_model(
    id="model-1",
    display_name=None,
    context_window=100_000,
    max_output_tokens=4096,
    capabilities=None,
):
    """A ModelInfo-shaped stand-in (format_model_line only reads attributes)."""
    return SimpleNamespace(
        id=id,
        display_name=display_name or id,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        capabilities=capabilities if capabilities is not None else ["tools"],
    )


def _make_live_provider(models=None, error=None, sync=False, no_list_models=False):
    """Build a mock, already-instantiated (session-mounted) provider.

    - models: list of ModelInfo-shaped objects returned by list_models()
    - error: if set, list_models() raises this exception
    - sync: if True, list_models is a plain sync callable (not async)
    - no_list_models: if True, the provider has no list_models attribute at all
    """
    provider = MagicMock()
    if no_list_models:
        del provider.list_models
        return provider

    if sync:
        if error is not None:
            provider.list_models = MagicMock(side_effect=error)
        else:
            provider.list_models = MagicMock(return_value=models or [])
    else:
        if error is not None:
            provider.list_models = AsyncMock(side_effect=error)
        else:
            provider.list_models = AsyncMock(return_value=models or [])

    # A session-mounted provider must never be closed by a diagnostic --
    # assert on this via close.assert_not_called()/not_awaited() in tests
    # that care.
    provider.close = AsyncMock()
    return provider


# ============================================================
# /provider test
# ============================================================


class TestProviderTestNoName:
    @pytest.mark.asyncio
    async def test_no_providers_mounted(self):
        cp = _cp_with(pin=_make_pin(), providers={})
        result = await cp._handle_provider("test")
        assert "no providers mounted" in result

    @pytest.mark.asyncio
    async def test_tests_all_mounted_providers(self):
        providers = {
            "anthropic-fable": _make_live_provider(
                models=[_make_model("m1"), _make_model("m2")]
            ),
            "openai-fast": _make_live_provider(models=[_make_model("m3")]),
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = _visible(await cp._handle_provider("test"))
        assert "anthropic-fable" in result
        assert "openai-fast" in result
        assert "2 models available" in result
        assert "1 model available" in result
        assert "\u2713" in result  # success checkmark for both

    @pytest.mark.asyncio
    async def test_never_gated_on_missing_pin_capability(self):
        """This is the whole point of the feature: it must work when
        pinning is unavailable -- that's exactly when it's needed."""
        providers = {"anthropic-fable": _make_live_provider(models=[_make_model()])}
        cp = _cp_with(pin=None, providers=providers)
        result = _visible(await cp._handle_provider("test"))
        assert "not registered" not in result
        assert "\u2713" in result
        assert "anthropic-fable" in result

    @pytest.mark.asyncio
    async def test_reports_failure_without_raising(self):
        providers = {
            "broken-provider": _make_live_provider(error=RuntimeError("bad api key")),
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = _visible(await cp._handle_provider("test"))
        assert "\u2717" in result
        assert "broken-provider" in result
        assert "bad api key" in result

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        providers = {
            "good": _make_live_provider(models=[_make_model()]),
            "bad": _make_live_provider(error=ValueError("no credentials")),
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = _visible(await cp._handle_provider("test"))
        lines = result.splitlines()
        good_line = next(line for line in lines if line.split()[1] == "good")
        bad_line = next(line for line in lines if line.split()[1] == "bad")
        assert "\u2713" in good_line
        assert "\u2717" in bad_line
        assert "no credentials" in bad_line

    @pytest.mark.asyncio
    async def test_never_closes_mounted_provider(self):
        """The mounted instance must keep answering the conversation after
        the diagnostic runs -- closing it would break subsequent turns."""
        provider = _make_live_provider(models=[_make_model()])
        cp = _cp_with(
            pin=_make_pin(available=["anthropic-fable"]),
            providers={"anthropic-fable": provider},
        )
        await cp._handle_provider("test")
        provider.close.assert_not_called()
        provider.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_result_is_tagged_experimental(self):
        providers = {"anthropic-fable": _make_live_provider(models=[_make_model()])}
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = await cp._handle_provider("test")
        assert "(experimental)" in result

    @pytest.mark.asyncio
    async def test_sync_list_models_supported(self):
        providers = {
            "sync-provider": _make_live_provider(models=[_make_model()], sync=True)
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = _visible(await cp._handle_provider("test"))
        assert "\u2713" in result
        assert "1 model available" in result


class TestProviderTestWithName:
    @pytest.mark.asyncio
    async def test_tests_only_named_provider(self):
        providers = {
            "anthropic-fable": _make_live_provider(models=[_make_model()]),
            "openai-fast": _make_live_provider(
                error=RuntimeError("should not be called")
            ),
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = _visible(await cp._handle_provider("test anthropic-fable"))
        assert "anthropic-fable" in result
        assert "openai-fast" not in result

    @pytest.mark.asyncio
    async def test_unknown_name_names_whats_available(self):
        providers = {
            "anthropic-fable": _make_live_provider(models=[_make_model()]),
            "openai-fast": _make_live_provider(models=[_make_model()]),
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = await cp._handle_provider("test nonexistent")
        assert "\u2717" in result
        assert "nonexistent" in result
        assert "not mounted" in result
        assert "anthropic-fable" in result
        assert "openai-fast" in result

    @pytest.mark.asyncio
    async def test_unknown_name_error_is_not_tagged_experimental(self):
        """Matches the existing /provider use convention: the refusal is
        the whole message, tagging it would dilute it."""
        providers = {"anthropic-fable": _make_live_provider(models=[_make_model()])}
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = await cp._handle_provider("test nonexistent")
        assert "(experimental)" not in result

    @pytest.mark.asyncio
    async def test_unknown_name_when_nothing_mounted_says_none(self):
        cp = _cp_with(pin=_make_pin(), providers={})
        result = await cp._handle_provider("test nonexistent")
        assert "(none)" in result

    @pytest.mark.asyncio
    async def test_unknown_name_never_gated_on_missing_pin(self):
        providers = {"anthropic-fable": _make_live_provider(models=[_make_model()])}
        cp = _cp_with(pin=None, providers=providers)
        result = await cp._handle_provider("test nonexistent")
        assert "not mounted" in result
        assert "not registered" not in result


# ============================================================
# /provider models
# ============================================================


class TestProviderModelsNoName:
    @pytest.mark.asyncio
    async def test_no_providers_mounted(self):
        cp = _cp_with(pin=_make_pin(), providers={})
        result = await cp._handle_provider("models")
        assert "no providers mounted" in result

    @pytest.mark.asyncio
    async def test_uses_pinned_provider_when_pinned(self):
        providers = {
            "anthropic-fable": _make_live_provider(
                models=[
                    _make_model(
                        "claude-x", context_window=200_000, max_output_tokens=8192
                    )
                ]
            ),
            "openai-fast": _make_live_provider(models=[_make_model("gpt-x")]),
        }
        pin = _make_pin(available=list(providers), current="anthropic-fable")
        cp = _cp_with(pin=pin, providers=providers)
        result = _visible(await cp._handle_provider("models"))
        assert "claude-x" in result
        assert "gpt-x" not in result
        assert "context=200,000" in result

    @pytest.mark.asyncio
    async def test_uses_priority_winner_when_unpinned(self):
        providers = {
            "low-priority": _make_live_provider(models=[_make_model("low-model")]),
            "high-priority": _make_live_provider(models=[_make_model("high-model")]),
        }
        providers["low-priority"].priority = 5
        providers["high-priority"].priority = 1
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = _visible(await cp._handle_provider("models"))
        assert "high-model" in result
        assert "low-model" not in result

    @pytest.mark.asyncio
    async def test_never_gated_on_missing_pin_capability(self):
        providers = {
            "anthropic-fable": _make_live_provider(models=[_make_model("claude-x")])
        }
        cp = _cp_with(pin=None, providers=providers)
        result = _visible(await cp._handle_provider("models"))
        assert "not registered" not in result
        assert "claude-x" in result

    @pytest.mark.asyncio
    async def test_empty_model_list_reported_not_swallowed(self):
        providers = {"anthropic-fable": _make_live_provider(models=[])}
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = await cp._handle_provider("models")
        assert "no models reported" in result

    @pytest.mark.asyncio
    async def test_never_closes_mounted_provider(self):
        provider = _make_live_provider(models=[_make_model()])
        cp = _cp_with(
            pin=_make_pin(available=["anthropic-fable"]),
            providers={"anthropic-fable": provider},
        )
        await cp._handle_provider("models")
        provider.close.assert_not_called()
        provider.close.assert_not_awaited()


class TestProviderModelsWithName:
    @pytest.mark.asyncio
    async def test_lists_models_for_named_provider(self):
        providers = {
            "anthropic-fable": _make_live_provider(
                models=[
                    _make_model(
                        "claude-x",
                        context_window=200_000,
                        max_output_tokens=8192,
                        capabilities=["tools", "vision"],
                    )
                ]
            ),
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = _visible(await cp._handle_provider("models anthropic-fable"))
        assert "claude-x" in result
        assert "context=200,000" in result
        assert "max_out=8,192" in result
        assert "tools, vision" in result

    @pytest.mark.asyncio
    async def test_unknown_name_names_whats_available(self):
        providers = {
            "anthropic-fable": _make_live_provider(models=[_make_model()]),
            "openai-fast": _make_live_provider(models=[_make_model()]),
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = await cp._handle_provider("models nonexistent")
        assert "\u2717" in result
        assert "nonexistent" in result
        assert "not mounted" in result
        assert "anthropic-fable" in result
        assert "openai-fast" in result

    @pytest.mark.asyncio
    async def test_unknown_name_error_is_not_tagged_experimental(self):
        providers = {"anthropic-fable": _make_live_provider(models=[_make_model()])}
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = await cp._handle_provider("models nonexistent")
        assert "(experimental)" not in result

    @pytest.mark.asyncio
    async def test_unknown_name_when_nothing_mounted_says_none(self):
        cp = _cp_with(pin=_make_pin(), providers={})
        result = await cp._handle_provider("models nonexistent")
        assert "(none)" in result

    @pytest.mark.asyncio
    async def test_reports_failure_without_raising(self):
        providers = {
            "broken": _make_live_provider(error=RuntimeError("connection refused"))
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = await cp._handle_provider("models broken")
        assert "\u2717" in result
        assert "connection refused" in result

    @pytest.mark.asyncio
    async def test_never_gated_on_missing_pin(self):
        providers = {
            "anthropic-fable": _make_live_provider(models=[_make_model("claude-x")])
        }
        cp = _cp_with(pin=None, providers=providers)
        result = _visible(await cp._handle_provider("models anthropic-fable"))
        assert "not registered" not in result
        assert "claude-x" in result

    @pytest.mark.asyncio
    async def test_sync_list_models_supported(self):
        providers = {
            "sync-provider": _make_live_provider(models=[_make_model("m1")], sync=True),
        }
        cp = _cp_with(pin=_make_pin(available=list(providers)), providers=providers)
        result = _visible(await cp._handle_provider("models sync-provider"))
        assert "m1" in result


# ============================================================
# Registration / discoverability
# ============================================================


class TestRegistrationAndUsage:
    @pytest.mark.asyncio
    async def test_help_output_mentions_test_and_models(self):
        cp = _make_command_processor()
        help_text = cp._format_help()
        provider_line = next(
            line for line in help_text.splitlines() if line.startswith("  /provider")
        )
        assert "/provider test" in provider_line
        assert "/provider models" in provider_line

    def test_commands_dict_description_mentions_test_and_models(self):
        from amplifier_app_cli.main import CommandProcessor

        description = CommandProcessor.COMMANDS["/provider"]["description"]
        assert "/provider test" in description
        assert "/provider models" in description

    @pytest.mark.asyncio
    async def test_unknown_subcommand_usage_mentions_test_and_models(self):
        cp = _cp_with(pin=_make_pin(), providers={})
        result = await cp._handle_provider("frobnicate")
        assert "/provider test" in result
        assert "/provider models" in result
