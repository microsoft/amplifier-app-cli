"""Regression test for the first-run tip message.

The tip message should NOT claim that setting API keys skips setup,
because check_first_run() only checks ~/.amplifier/settings.yaml,
not environment variables. Instead it should recommend 'amplifier provider add'.

See: upstream-fix-6-cli-init-message.md
"""

import inspect

import amplifier_app_cli.commands.init as init_module


def test_init_tip_message_does_not_say_skip():
    """The tip message should NOT claim that setting API keys skips init."""
    source = inspect.getsource(init_module.prompt_first_run_init)
    assert "to skip this" not in source, (
        "Tip message still says 'to skip this' — misleading"
    )


def test_init_tip_message_recommends_provider_add():
    """The tip message should recommend 'amplifier provider add'."""
    source = inspect.getsource(init_module.prompt_first_run_init)
    assert "amplifier provider add" in source, (
        "Tip should mention 'amplifier provider add'"
    )
