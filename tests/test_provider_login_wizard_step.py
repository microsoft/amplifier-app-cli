"""Tests for the wizard login step: _maybe_login_provider(),
_run_provider_login(), and _safely_fetch_models_from_instance() in
provider_config_utils.py, plus their integration into configure_provider().

Duck-types auth_status()/login() via hasattr so this PR merges safely
independent of the parallel provider-module PR adding those methods (and
the "auth:oauth-device-code" capability) to provider-openai-chatgpt.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_app_cli.provider_config_utils import (
    _maybe_login_provider,
    _run_provider_login,
    _safely_fetch_models_from_instance,
    configure_provider,
)


def _info_with_auth_capability(display_name: str = "OpenAI ChatGPT") -> dict:
    return {
        "display_name": display_name,
        "capabilities": ["streaming", "tools", "auth:oauth-device-code"],
        "config_fields": [],
    }


def _info_without_auth_capability() -> dict:
    return {
        "display_name": "Anthropic",
        "capabilities": ["streaming", "tools"],
        "config_fields": [],
    }


class TestMaybeLoginProviderCapabilityGate:
    """No "auth:*" capability -> no instantiation attempt at all."""

    def test_returns_none_when_no_auth_capability(self):
        with patch(
            "amplifier_app_cli.provider_config_utils.load_provider_class"
        ) as mock_load:
            result = _maybe_login_provider(
                "anthropic", _info_without_auth_capability(), {}
            )

        assert result is None
        mock_load.assert_not_called()

    def test_returns_none_when_provider_class_unloadable(self):
        with patch(
            "amplifier_app_cli.provider_config_utils.load_provider_class",
            return_value=None,
        ):
            result = _maybe_login_provider(
                "openai-chatgpt", _info_with_auth_capability(), {}
            )
        assert result is None

    def test_returns_none_when_instantiation_fails(self):
        with (
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=None,
            ),
        ):
            result = _maybe_login_provider(
                "openai-chatgpt", _info_with_auth_capability(), {}
            )
        assert result is None


class TestMaybeLoginProviderDuckTyping:
    """Provider declares "auth:*" but doesn't implement auth_status/login
    -- must be treated like "no login flow", instance still reusable."""

    def test_returns_instance_without_prompting_when_methods_missing(self):
        provider = MagicMock(spec=[])  # no auth_status/login attributes

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=provider,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask"
            ) as mock_confirm,
        ):
            result = _maybe_login_provider(
                "openai-chatgpt", _info_with_auth_capability(), {}
            )

        assert result is provider
        mock_confirm.assert_not_called()


class TestMaybeLoginProviderAlreadyAuthenticated:
    def test_no_prompt_when_already_authenticated(self):
        provider = MagicMock()
        provider.auth_status.return_value = "authenticated"

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=provider,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask"
            ) as mock_confirm,
        ):
            result = _maybe_login_provider(
                "openai-chatgpt", _info_with_auth_capability(), {}
            )

        assert result is provider
        mock_confirm.assert_not_called()

    def test_broken_auth_status_treated_as_no_login(self):
        """A raising auth_status() must never crash the wizard."""
        provider = MagicMock()
        provider.auth_status.side_effect = RuntimeError("network down")

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=provider,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask"
            ) as mock_confirm,
        ):
            result = _maybe_login_provider(
                "openai-chatgpt", _info_with_auth_capability(), {}
            )

        assert result is provider
        mock_confirm.assert_not_called()


class TestMaybeLoginProviderPromptFlow:
    """auth_status() != "authenticated" -> the actual prompt/login flow."""

    def test_decline_prints_skip_message_and_never_calls_login(self):
        provider = MagicMock()
        provider.auth_status.return_value = "unauthenticated"

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=provider,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask",
                return_value=False,
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            result = _maybe_login_provider(
                "openai-chatgpt", _info_with_auth_capability(), {}
            )

        assert result is provider
        provider.login.assert_not_called()
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Skipping login" in printed
        assert "amplifier provider login openai-chatgpt" in printed

    def test_accept_and_successful_login_no_skip_message(self):
        provider = MagicMock()
        provider.auth_status.return_value = "expired"
        provider.login = AsyncMock(return_value=True)

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=provider,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask",
                return_value=True,
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            result = _maybe_login_provider(
                "openai-chatgpt", _info_with_auth_capability(), {}
            )

        assert result is provider
        provider.login.assert_awaited_once()
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Skipping login" not in printed

    def test_accept_but_failed_login_prints_skip_message(self):
        provider = MagicMock()
        provider.auth_status.return_value = "unauthenticated"
        provider.login = AsyncMock(return_value=False)

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=provider,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask",
                return_value=True,
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            result = _maybe_login_provider(
                "openai-chatgpt", _info_with_auth_capability(), {}
            )

        assert result is provider
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Skipping login" in printed


class TestRunProviderLogin:
    def test_sync_login_success(self):
        provider = MagicMock()
        provider.login = MagicMock(return_value=True)
        # Ensure iscoroutinefunction sees this as sync
        assert _run_provider_login(provider) is True

    def test_sync_login_failure(self):
        provider = MagicMock()
        provider.login = MagicMock(return_value=False)
        assert _run_provider_login(provider) is False

    def test_async_login_success_calls_print_fn(self):
        async def fake_login(print_fn=None):
            if print_fn:
                print_fn("Go to https://example.com/device and enter ABCD-EFGH")
            return True

        provider = MagicMock()
        provider.login = fake_login

        with patch("amplifier_app_cli.provider_config_utils.console") as mock_console:
            result = _run_provider_login(provider)

        assert result is True
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "https://example.com/device" in printed
        assert "ABCD-EFGH" in printed

    def test_login_exception_returns_false_no_traceback(self):
        provider = MagicMock()
        provider.login = MagicMock(side_effect=RuntimeError("device flow expired"))

        with patch("amplifier_app_cli.provider_config_utils.console") as mock_console:
            result = _run_provider_login(provider)

        assert result is False
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Login failed" in printed
        assert "Traceback" not in printed

    def test_keyboard_interrupt_propagates(self):
        provider = MagicMock()
        provider.login = MagicMock(side_effect=KeyboardInterrupt())

        try:
            _run_provider_login(provider)
            raised = False
        except KeyboardInterrupt:
            raised = True
        assert raised, "KeyboardInterrupt must propagate, not be swallowed"


class TestSafelyFetchModelsFromInstance:
    def test_success_returns_models(self):
        models = [MagicMock(id="gpt-5")]
        with patch(
            "amplifier_app_cli.provider_config_utils.list_models_for_instance",
            return_value=models,
        ):
            result = _safely_fetch_models_from_instance("openai-chatgpt", MagicMock())
        assert result == models

    def test_connection_error_returns_empty_list_silently(self):
        with (
            patch(
                "amplifier_app_cli.provider_config_utils.list_models_for_instance",
                side_effect=ConnectionError("down"),
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            result = _safely_fetch_models_from_instance("openai-chatgpt", MagicMock())
        assert result == []
        mock_console.print.assert_not_called()

    def test_generic_exception_returns_empty_list_with_warning_not_traceback(self):
        with (
            patch(
                "amplifier_app_cli.provider_config_utils.list_models_for_instance",
                side_effect=RuntimeError("AuthenticationError: bad token"),
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            result = _safely_fetch_models_from_instance("openai-chatgpt", MagicMock())
        assert result == []
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Could not fetch models" in printed
        assert "Traceback" not in printed


class TestConfigureProviderLoginStepIntegration:
    """End-to-end through configure_provider(): the login step must never
    break a provider with no auth capability (regression guard), and must
    drive the prompt + reuse the fetched models for a provider that has one."""

    def _api_key_info(self):
        return {
            "display_name": "Test Provider",
            "capabilities": ["streaming"],
            "config_fields": [
                {
                    "id": "api_key",
                    "display_name": "API Key",
                    "field_type": "text",
                    "prompt": "Enter your API key",
                    "required": True,
                }
            ],
        }

    def test_provider_without_auth_capability_unaffected(self):
        """Regression guard: a normal (non-OAuth) provider's configure_provider()
        flow is byte-for-byte unaffected by the new login step."""
        mock_key_manager = MagicMock()

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=self._api_key_info(),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="dummy-api-key",
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._prompt_model_selection",
                return_value="model-x",
            ) as mock_select,
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class"
            ) as mock_load,
            patch("amplifier_app_cli.provider_config_utils.console"),
        ):
            result = configure_provider("test-provider", mock_key_manager)

        assert result is not None
        assert result["default_model"] == "model-x"
        mock_load.assert_not_called()
        # models kwarg must be None (unaffected) when there's no auth capability
        _, kwargs = mock_select.call_args
        assert kwargs.get("models") is None

    def test_provider_with_auth_capability_prompts_and_reuses_prefetched_models(
        self,
    ):
        oauth_info = _info_with_auth_capability()
        oauth_info["config_fields"] = []
        provider_instance = MagicMock()
        provider_instance.auth_status.return_value = "unauthenticated"
        provider_instance.login = AsyncMock(return_value=True)
        prefetched = [MagicMock(id="gpt-5-chatgpt")]

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=oauth_info,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=provider_instance,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.list_models_for_instance",
                return_value=prefetched,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._prompt_model_selection",
                return_value="gpt-5-chatgpt",
            ) as mock_select,
            patch("amplifier_app_cli.provider_config_utils.console"),
        ):
            result = configure_provider("openai-chatgpt", MagicMock())

        assert result is not None
        assert result["default_model"] == "gpt-5-chatgpt"
        provider_instance.login.assert_awaited_once()
        _, kwargs = mock_select.call_args
        assert kwargs.get("models") == prefetched, (
            "The prefetched models from the login-time instance must be "
            "passed straight into _prompt_model_selection(), not refetched"
        )

    def test_provider_with_auth_capability_decline_still_completes(self):
        oauth_info = _info_with_auth_capability()
        oauth_info["config_fields"] = []
        provider_instance = MagicMock()
        provider_instance.auth_status.return_value = "unauthenticated"

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=oauth_info,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._try_instantiate_provider",
                return_value=provider_instance,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask",
                return_value=False,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._prompt_model_selection",
                return_value="",
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            result = configure_provider("openai-chatgpt", MagicMock())

        assert result is not None, "Declining login must not abort the wizard"
        provider_instance.login.assert_not_called()
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Skipping login" in printed
