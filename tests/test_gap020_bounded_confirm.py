"""Regression tests for GAP-020: the first-run confirm prompt must terminate.

This fix had **zero** test coverage. It was found by auditing every GAP claimed
in source comments against the GAPs referenced in tests -- the same audit that
would have caught GAP-021, whose untested fix turned out to silently corrupt
user input on every platform.

`rich.prompt.Confirm.ask()` is a bare `while True:` with no bound. On invalid
(non-y/n) input it re-prompts forever, with nothing indicating a limit exists.
That sits at a first-run gate a brand-new user hits before anything else works,
so anyone who doesn't type exactly "y" or "n" -- a stray keypress, a pasted
line, an automation sending the wrong thing -- is stuck with no signal that it
will ever end.

The contract these tests pin is exactly the one the fix exists to add: after
`max_attempts` invalid responses it **stops**, loudly, and falls through to the
same "setup skipped" state an explicit "n" produces. Every test here is bounded
by `pytest-timeout`-free construction -- a hang shows up as a failed assertion
on call count, not as a wedged suite.
"""

from __future__ import annotations

from unittest.mock import patch

from amplifier_app_cli.commands.init import _bounded_confirm
from rich.console import Console


class _ScriptedInput:
    """Feeds a fixed script of responses, then refuses to be asked again.

    If the loop is unbounded it will ask past the end of the script; raising
    there converts an infinite hang into an immediate, legible failure rather
    than a suite that never finishes.
    """

    def __init__(self, responses: list[str], hard_limit: int = 50) -> None:
        self.responses = responses
        self.calls = 0
        self.hard_limit = hard_limit

    def __call__(self, *_args: object, **_kwargs: object) -> str:
        self.calls += 1
        if self.calls > self.hard_limit:
            raise AssertionError(
                f"_bounded_confirm asked for input {self.calls} times -- the "
                "loop is unbounded. This is the GAP-020 hang."
            )
        idx = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[idx]


def test_invalid_input_terminates_after_max_attempts() -> None:
    """Three invalid answers must end the prompt, not re-ask forever."""
    console = Console(quiet=True)
    scripted = _ScriptedInput(["banana", "banana", "banana"])

    with patch("rich.prompt.PromptBase.get_input", scripted):
        result = _bounded_confirm(console, "Proceed?", default=True, max_attempts=3)

    assert scripted.calls == 3, (
        f"expected exactly 3 prompts, got {scripted.calls}. Fewer means the "
        "bound is too tight; more means it is not being honoured."
    )
    assert result is False, (
        "after exhausting attempts the result must be the conservative "
        "'skip setup' answer, matching an explicit 'n'"
    )


def test_max_attempts_is_actually_honoured() -> None:
    """The bound must track max_attempts, not be hardcoded."""
    console = Console(quiet=True)
    for limit in (1, 2, 5):
        scripted = _ScriptedInput(["nonsense"])
        with patch("rich.prompt.PromptBase.get_input", scripted):
            _bounded_confirm(console, "Proceed?", default=True, max_attempts=limit)
        assert scripted.calls == limit, (
            f"max_attempts={limit} produced {scripted.calls} prompts"
        )


def test_valid_answer_short_circuits_immediately() -> None:
    """A good answer must not consume the retry budget.

    Guards against a "fix" that bounds the loop by always running it to
    exhaustion.
    """
    console = Console(quiet=True)

    for answer, expected in (("y", True), ("n", False)):
        scripted = _ScriptedInput([answer])
        with patch("rich.prompt.PromptBase.get_input", scripted):
            result = _bounded_confirm(
                console, "Proceed?", default=False, max_attempts=3
            )
        assert result is expected, f"answer {answer!r} produced {result!r}"
        assert scripted.calls == 1, (
            f"a valid answer took {scripted.calls} prompts; should take 1"
        )


def test_recovery_after_invalid_input() -> None:
    """An invalid answer followed by a valid one must accept the valid one."""
    console = Console(quiet=True)
    scripted = _ScriptedInput(["what", "y"])

    with patch("rich.prompt.PromptBase.get_input", scripted):
        result = _bounded_confirm(console, "Proceed?", default=False, max_attempts=3)

    assert result is True
    assert scripted.calls == 2


def test_empty_input_returns_the_default() -> None:
    """Bare Enter means "accept the default", not "invalid"."""
    console = Console(quiet=True)

    for default in (True, False):
        scripted = _ScriptedInput([""])
        with patch("rich.prompt.PromptBase.get_input", scripted):
            result = _bounded_confirm(
                console, "Proceed?", default=default, max_attempts=3
            )
        assert result is default
        assert scripted.calls == 1
