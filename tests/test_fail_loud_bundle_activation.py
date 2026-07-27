"""Regression coverage for issue #343 (silently degraded bundles).

A module that failed to download was dropped from the session with only a
DEBUG log. The user got a session missing tools their bundle declared, and
read the result as "the model ignored my instructions" rather than "a
download failed" -- the failure surfaced far from its cause.

Foundation supplies the mechanism (``prepare(strict=...)``). Choosing to turn
it on is app-layer policy, and these tests pin that policy.
"""

from __future__ import annotations

import pytest

from amplifier_app_cli.lib.bundle_loader.prepare import _ALLOW_PARTIAL_ENV
from amplifier_app_cli.lib.bundle_loader.prepare import _activation_is_strict


class TestActivationStrictnessPolicy343:
    def test_strict_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no env var set, a failed module aborts startup."""
        monkeypatch.delenv(_ALLOW_PARTIAL_ENV, raising=False)
        assert _activation_is_strict() is True

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " true "])
    def test_escape_hatch_opts_out(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """The escape hatch exists so a hard failure can't strand a user.

        A proxy blocking one module host, or a repo that went private, would
        otherwise make the CLI unusable rather than merely degraded.
        """
        monkeypatch.setenv(_ALLOW_PARTIAL_ENV, value)
        assert _activation_is_strict() is False

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "banana"])
    def test_non_truthy_values_stay_strict(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """Anything that isn't a clear opt-in stays strict -- fail safe."""
        monkeypatch.setenv(_ALLOW_PARTIAL_ENV, value)
        assert _activation_is_strict() is True


class TestPreparePassesStrict343:
    """The policy must actually reach ``prepare()`` -- not just exist."""

    def test_all_prepare_call_sites_pass_strict(self) -> None:
        """Guards against a new prepare() call site silently defaulting to lax.

        foundation's default is strict=False, so an unwired call site is a
        silent regression back to the #343 behaviour rather than a test error.
        """
        import inspect
        import re

        from amplifier_app_cli.lib.bundle_loader import prepare as prepare_mod

        source = inspect.getsource(prepare_mod)
        # Real call sites only -- prose mentions of prepare() in docstrings and
        # comments are not wiring and must not be counted.
        prepare_calls = len(re.findall(r"await\s+\w+\.prepare\(", source))
        wired_calls = source.count("strict=_activation_is_strict()")

        assert prepare_calls > 0, "sanity: expected prepare() call sites"
        assert wired_calls == prepare_calls, (
            f"{prepare_calls} prepare() call sites but only {wired_calls} pass "
            "strict= -- an unwired site silently reverts to issue #343 behaviour"
        )
