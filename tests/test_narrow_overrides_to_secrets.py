"""Unit coverage for the resume credential refresh narrowing (fix A).

``narrow_overrides_to_secrets()`` is the guard that stops the resume-time
credential refresh from re-imposing every ``settings.yaml`` key on a resumed
sub-session's own persisted mount plan. The load-bearing casualty of the
unnarrowed merge was ``config.priority``: it overwrote the ``priority: 0``
that ``model_role``/``provider_preferences`` installed at spawn, so the
resumed leg silently re-resolved to the settings priority-0 provider
(model_performance-rc0: 39 of 66 delegate resumes changed model, 37 of them
cheap -> expensive; 0 of 179 root resumes affected).

End-to-end coverage through the real ``resume_sub_session()`` path lives in
``test_resume_preserves_provider_promotion.py``.

No API calls anywhere in this module.
"""

from __future__ import annotations

from amplifier_app_cli.runtime.config import _apply_provider_overrides
from amplifier_app_cli.runtime.config import narrow_overrides_to_secrets


# ---------------------------------------------------------------------------
# Fixtures modelled on the rc0 capture
#   20260901-rebaseline/runs/val-rb-oai-sol-xhigh-s1-01
#   .../0000000000000000-25443a97b60d4965_anchors-amp-dev-git-ops
# leg 1: luna priority 0 / sol priority 1 -> 13 x gpt-5.6-luna
# leg 2: luna priority 14 / sol priority 0 -> 25 x gpt-5.6-sol
# ---------------------------------------------------------------------------

PROMOTED = "provider-luna"  # the cheap tier the role promoted
SETTINGS_ZERO = "provider-sol"  # what settings puts at priority 0


def _persisted_child_providers() -> list[dict]:
    """The child's mount plan as spawn built it, then redaction persisted it."""
    return [
        {
            "module": PROMOTED,
            "config": {
                "api_key": "[REDACTED]",
                "default_model": "gpt-5.6-luna",
                "priority": 0,
                "reasoning_effort": "medium",
                # Present ONLY in the preference's own config -- never in
                # settings.yaml. These survived the wipe in the capture and
                # are what proved the child's config was merged, not rebuilt.
                "enable_response_chaining": "auto",
                "prompt_cache_retention": "in_memory",
            },
        },
        {
            "module": SETTINGS_ZERO,
            "config": {
                "api_key": "[REDACTED]",
                "default_model": "gpt-5.6-sol",
                "priority": 1,
            },
        },
    ]


def _live_settings_overrides() -> list[dict]:
    """Live settings.yaml: real keys, plus the priorities that did the damage.

    ``reasoning_effort`` differs from the child's persisted value on purpose:
    rc0 recorded that drift as INFERRED-NOT-CONFIRMED because the capture had
    the same effort on both sides. Differing values settle it here.
    """
    return [
        {
            "module": PROMOTED,
            "config": {
                "api_key": "sk-live-luna",
                "priority": 14,
                "reasoning_effort": "high",
            },
        },
        {
            "module": SETTINGS_ZERO,
            "config": {
                "api_key": "sk-live-sol",
                "priority": 0,
            },
        },
    ]


def _by_module(providers: list[dict], module: str) -> dict:
    return next(p for p in providers if p["module"] == module)


class TestRefreshNarrowedToSecrets:
    """The resume credential refresh must restore secrets and nothing else."""

    def test_promotion_survives_the_credential_refresh(self):
        """FAILS BEFORE FIX A.

        Before: settings ``priority: 14`` deep-merged over the child's
        ``priority: 0`` and the promotion was gone.
        """
        refreshed = _apply_provider_overrides(
            _persisted_child_providers(),
            narrow_overrides_to_secrets(_live_settings_overrides()),
        )

        promoted = _by_module(refreshed, PROMOTED)
        assert promoted["config"]["priority"] == 0, (
            "The child's spawn-time promotion (priority 0) must survive the "
            "resume credential refresh. Settings priority is not a secret and "
            "must not be re-imposed on a persisted child mount plan."
        )
        assert _by_module(refreshed, SETTINGS_ZERO)["config"]["priority"] == 1, (
            "The spawn-time demotion of the settings priority-0 provider must "
            "survive too -- otherwise both providers tie at 0."
        )

    def test_credentials_are_still_refreshed(self):
        """The reason the refresh exists at all must keep working."""
        refreshed = _apply_provider_overrides(
            _persisted_child_providers(),
            narrow_overrides_to_secrets(_live_settings_overrides()),
        )

        assert _by_module(refreshed, PROMOTED)["config"]["api_key"] == "sk-live-luna"
        assert (
            _by_module(refreshed, SETTINGS_ZERO)["config"]["api_key"] == "sk-live-sol"
        )

    def test_per_candidate_config_keys_survive(self):
        """rc0 section 4.6: ``reasoning_effort`` was exposed to the same wipe.

        The capture could not observe it (preference and settings both said
        "high" for the same provider id), so rc0 recorded it
        INFERRED-NOT-CONFIRMED. Differing values settle it: the child's own
        effort must win on its own plan.
        """
        refreshed = _apply_provider_overrides(
            _persisted_child_providers(),
            narrow_overrides_to_secrets(_live_settings_overrides()),
        )

        assert _by_module(refreshed, PROMOTED)["config"]["reasoning_effort"] == "medium"

    def test_preference_only_keys_are_untouched(self):
        """Keys absent from settings survived even before the fix; still do."""
        refreshed = _apply_provider_overrides(
            _persisted_child_providers(),
            narrow_overrides_to_secrets(_live_settings_overrides()),
        )

        promoted_config = _by_module(refreshed, PROMOTED)["config"]
        assert promoted_config["enable_response_chaining"] == "auto"
        assert promoted_config["prompt_cache_retention"] == "in_memory"
        assert promoted_config["default_model"] == "gpt-5.6-luna"


class TestNarrowOverridesToSecrets:
    """Unit coverage for the narrowing helper itself."""

    def test_non_secret_keys_are_dropped(self):
        narrowed = narrow_overrides_to_secrets(
            [{"module": "provider-x", "config": {"api_key": "k", "priority": 3}}]
        )
        assert narrowed == [{"module": "provider-x", "config": {"api_key": "k"}}]

    def test_entries_without_secrets_are_dropped_entirely(self):
        assert (
            narrow_overrides_to_secrets(
                [{"module": "provider-x", "config": {"priority": 3}}]
            )
            == []
        )

    def test_identity_keys_are_preserved(self):
        """``id`` must survive or the override stops matching its target."""
        narrowed = narrow_overrides_to_secrets(
            [
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-sonnet",
                    "config": {"api_key": "k", "priority": 9},
                }
            ]
        )
        assert narrowed[0]["id"] == "anthropic-sonnet"
        assert narrowed[0]["config"] == {"api_key": "k"}

    def test_non_identity_top_level_keys_are_dropped(self):
        """A settings override must not rewrite ``source`` at resume time."""
        narrowed = narrow_overrides_to_secrets(
            [
                {
                    "module": "provider-x",
                    "source": "git+https://example.invalid/other",
                    "config": {"api_key": "k"},
                }
            ]
        )
        assert "source" not in narrowed[0]

    def test_nested_secrets_are_kept_with_their_path(self):
        narrowed = narrow_overrides_to_secrets(
            [
                {
                    "module": "hooks-x",
                    "config": {
                        "enabled": True,
                        "auth": {"token": "t", "retries": 3},
                    },
                }
            ]
        )
        assert narrowed[0]["config"] == {"auth": {"token": "t", "retries": 3}}
        assert "enabled" not in narrowed[0]["config"]

    def test_lists_holding_secrets_are_kept_whole(self):
        """deep_merge REPLACES lists; a partially-pruned list would truncate."""
        destinations = [{"url": "https://x.invalid", "api_key": "k"}]
        narrowed = narrow_overrides_to_secrets(
            [{"module": "hooks-x", "config": {"destinations": destinations}}]
        )
        assert narrowed[0]["config"]["destinations"] == destinations

    def test_lists_without_secrets_are_dropped(self):
        assert (
            narrow_overrides_to_secrets(
                [{"module": "hooks-x", "config": {"targets": [{"url": "u"}]}}]
            )
            == []
        )

    def test_malformed_entries_are_skipped_not_raised(self):
        assert (
            narrow_overrides_to_secrets(
                [None, "provider-x", {"config": {"api_key": "k"}}, {"module": "m"}]
            )
            == []
        )

    def test_empty_input_is_empty_output(self):
        assert narrow_overrides_to_secrets([]) == []
