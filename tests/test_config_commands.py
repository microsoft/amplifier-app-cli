"""Tests for config command helpers in CommandProcessor."""

from amplifier_app_cli.main import CommandProcessor


class TestRedactValue:
    """Tests for CommandProcessor._redact_value static method."""

    def test_redacts_api_key(self):
        """Sensitive key 'api_key' with long value should be redacted."""
        result = CommandProcessor._redact_value(
            "api_key", "sk-ant-1234567890abcdef1234567890"
        )
        assert result == "sk-a...redacted"

    def test_redacts_token(self):
        """Key containing 'token' with long value should be redacted."""
        result = CommandProcessor._redact_value(
            "access_token", "ghp_1234567890abcdefghijklmnop"
        )
        assert result == "ghp_...redacted"

    def test_redacts_secret(self):
        """Key containing 'secret' with long value should be redacted."""
        result = CommandProcessor._redact_value(
            "client_secret", "abcdefghijklmnopqrstuvwxyz"
        )
        assert result == "abcd...redacted"

    def test_redacts_password(self):
        """Key containing 'password' with long value should be redacted."""
        result = CommandProcessor._redact_value(
            "db_password", "supersecretpassword12345"
        )
        assert result == "supe...redacted"

    def test_does_not_redact_short_value(self):
        """Sensitive key with value <= 20 chars should NOT be redacted."""
        result = CommandProcessor._redact_value("api_key", "short")
        assert result == "short"

    def test_does_not_redact_non_sensitive_key(self):
        """Non-sensitive key with long value should NOT be redacted."""
        long_value = "a_very_long_value_that_exceeds_twenty_characters"
        result = CommandProcessor._redact_value("model", long_value)
        assert result == long_value

    def test_handles_non_string_value(self):
        """Non-string value (e.g. integer) should be returned as-is."""
        result = CommandProcessor._redact_value("api_key", 12345)
        assert result == 12345

    def test_redacts_key_containing_keyword(self):
        """Key containing a sensitive keyword (e.g. 'encryption_key') should be redacted."""
        long_value = "a_very_long_encryption_value_here_12345"
        result = CommandProcessor._redact_value("encryption_key", long_value)
        assert result == "a_ve...redacted"
