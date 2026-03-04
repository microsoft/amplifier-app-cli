"""Tests for auto-init in non-interactive (non-TTY) contexts.

When check_first_run() returns True and stdin is not a TTY (Docker, CI,
shadow environments), the CLI should auto-configure from environment
variables instead of showing an interactive prompt that can't be answered.

See: upstream-fix-7-cli-auto-init.md
"""

import inspect
from unittest.mock import MagicMock, patch


class TestAutoInitFromEnv:
    """Test the auto_init_from_env function directly."""

    def test_function_exists(self):
        """auto_init_from_env should be importable from commands.init."""
        from amplifier_app_cli.commands.init import auto_init_from_env

        assert callable(auto_init_from_env)

    def test_returns_false_when_no_api_keys(self):
        """auto_init_from_env returns False when no API keys are in env."""
        from amplifier_app_cli.commands.init import auto_init_from_env

        with (
            patch("amplifier_app_cli.commands.init.install_known_providers"),
            patch(
                "amplifier_app_cli.commands.init.detect_provider_from_env",
                return_value=None,
            ),
        ):
            result = auto_init_from_env()
            assert result is False

    def test_warns_when_no_api_keys(self):
        """auto_init_from_env prints warning when no API keys found."""
        from amplifier_app_cli.commands.init import auto_init_from_env

        mock_console = MagicMock()
        with (
            patch("amplifier_app_cli.commands.init.install_known_providers"),
            patch(
                "amplifier_app_cli.commands.init.detect_provider_from_env",
                return_value=None,
            ),
        ):
            auto_init_from_env(mock_console)
            # Should print a warning message about missing API keys
            mock_console.print.assert_called()
            printed = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "No provider" in printed or "no provider" in printed

    def test_configures_when_api_key_present(self):
        """auto_init_from_env configures provider when API key detected."""
        from amplifier_app_cli.commands.init import auto_init_from_env

        mock_provider_config = {"api_key": "test-key"}
        with (
            patch("amplifier_app_cli.commands.init.install_known_providers"),
            patch(
                "amplifier_app_cli.commands.init.detect_provider_from_env",
                return_value="provider-anthropic",
            ),
            patch("amplifier_app_cli.commands.init.create_config_manager"),
            patch("amplifier_app_cli.commands.init.KeyManager"),
            patch("amplifier_app_cli.commands.init.ProviderManager") as MockProviderMgr,
            patch(
                "amplifier_app_cli.commands.init.configure_provider",
                return_value=mock_provider_config,
            ),
        ):
            mock_pm = MagicMock()
            MockProviderMgr.return_value = mock_pm

            result = auto_init_from_env()
            assert result is True
            mock_pm.use_provider.assert_called_once_with(
                "provider-anthropic",
                scope="global",
                config=mock_provider_config,
                source=None,
            )

    def test_returns_false_when_configure_fails(self):
        """auto_init_from_env returns False when configure_provider returns None."""
        from amplifier_app_cli.commands.init import auto_init_from_env

        with (
            patch("amplifier_app_cli.commands.init.install_known_providers"),
            patch(
                "amplifier_app_cli.commands.init.detect_provider_from_env",
                return_value="provider-anthropic",
            ),
            patch("amplifier_app_cli.commands.init.create_config_manager"),
            patch("amplifier_app_cli.commands.init.KeyManager"),
            patch("amplifier_app_cli.commands.init.ProviderManager"),
            patch(
                "amplifier_app_cli.commands.init.configure_provider", return_value=None
            ),
        ):
            result = auto_init_from_env()
            assert result is False

    def test_does_not_crash_on_exception(self):
        """auto_init_from_env should not raise -- best-effort only."""
        from amplifier_app_cli.commands.init import auto_init_from_env

        with patch(
            "amplifier_app_cli.commands.init.install_known_providers",
            side_effect=Exception("boom"),
        ):
            # Should not raise
            result = auto_init_from_env()
            assert result is False


class TestSessionRunnerNonTTY:
    """Test that session_runner uses auto_init_from_env for non-TTY."""

    def test_non_tty_calls_auto_init(self):
        """When stdin is not a TTY, session_runner should call auto_init_from_env."""
        from amplifier_app_cli.session_runner import create_initialized_session

        source = inspect.getsource(create_initialized_session)
        # The function should reference auto_init_from_env for non-TTY path
        assert "auto_init_from_env" in source, (
            "create_initialized_session should call auto_init_from_env for non-TTY contexts"
        )
        # And should check isatty
        assert "isatty" in source, (
            "create_initialized_session should check sys.stdin.isatty()"
        )

    def test_tty_still_calls_prompt(self):
        """When stdin IS a TTY, session_runner should still call prompt_first_run_init."""
        from amplifier_app_cli.session_runner import create_initialized_session

        source = inspect.getsource(create_initialized_session)
        assert "prompt_first_run_init" in source, (
            "create_initialized_session should still call prompt_first_run_init for TTY"
        )


class TestRunCommandNonTTY:
    """Test that the run command uses auto_init_from_env for non-TTY."""

    def test_run_command_has_tty_check(self):
        """The run command should check isatty before calling prompt_first_run_init."""
        from amplifier_app_cli.commands.run import register_run_command

        source = inspect.getsource(register_run_command)
        # The first-run check section should have auto_init_from_env logic
        assert "auto_init_from_env" in source, (
            "register_run_command should call auto_init_from_env for non-TTY contexts"
        )


class TestInitCmdNonTTY:
    """Test that init command auto-upgrades to non-interactive when no TTY."""

    def test_init_cmd_no_tty_auto_upgrades(self):
        """init command should set non_interactive = True when no TTY, not error out."""
        from amplifier_app_cli.commands.init import init_cmd

        source = inspect.getsource(init_cmd.callback)
        # Should set non_interactive = True when stdin is not a TTY
        assert "non_interactive = True" in source, (
            "init_cmd should set non_interactive = True when stdin is not a TTY"
        )
