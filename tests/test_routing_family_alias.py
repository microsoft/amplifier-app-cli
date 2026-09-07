"""`routing show` must count an aliased backend as configured.

THE ALIAS-BLIND USER. A ChatGPT-subscription-only user (`openai-chatgpt`
   module, no `openai` module) is routed fine at runtime -- the hook's
   ``PROVIDER_FAMILY_ALIASES`` maps ``provider: openai`` onto that mount -- but
   `routing show` computed "is this candidate configured?" with a bare
   set-membership test that never consulted the alias table, and told exactly
   that user "✗ not configured" for every openai role. Measured in a DTU,
   2026-09-07.

(A second guard used to live here: a "displayed but not mounted" warning for
hosts whose active bundle never mounted hooks-routing. It is gone because the
condition is now unreachable -- runtime/config.py composes the routing
behavior on every session, see `_build_routing_behaviors` and
tests/test_runtime_routing_composition.py.)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from amplifier_app_cli.commands import routing as routing_cmd
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


def _settings_with_providers(tmp_path: Path, providers: list[dict]) -> AppSettings:
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    paths.global_settings.parent.mkdir(parents=True, exist_ok=True)
    paths.global_settings.write_text(
        yaml.safe_dump({"config": {"providers": providers}}), encoding="utf-8"
    )
    return AppSettings(paths=paths)


# ---------------------------------------------------------------------------
# 1. Family aliases in "is this provider configured?"
# ---------------------------------------------------------------------------


class TestConfiguredProviderTypesHonourFamilyAliases:
    ALIASES = {"openai": ("openai-chatgpt",)}

    def test_chatgpt_alone_makes_openai_configured(self, tmp_path: Path) -> None:
        """The user this was built for: subscription backend only."""
        settings = _settings_with_providers(
            tmp_path,
            [{"module": "provider-openai-chatgpt", "source": "git+x", "id": "chatgpt"}],
        )
        with patch.object(
            routing_cmd, "_provider_family_aliases", return_value=self.ALIASES
        ):
            types = routing_cmd._get_configured_provider_types(settings)
        assert "openai-chatgpt" in types  # the module type, as before
        assert "chatgpt" in types  # the instance id, as before
        assert "openai" in types, (
            "openai-chatgpt satisfies `provider: openai` at routing time, so the "
            "display must count openai as configured too"
        )

    def test_alias_is_directional(self, tmp_path: Path) -> None:
        """A plain `openai` mount does NOT make `openai-chatgpt` look configured.

        Mirrors the resolver: naming the subscription backend explicitly means
        it, and the display must not claim a backend the user doesn't have.
        """
        settings = _settings_with_providers(
            tmp_path, [{"module": "provider-openai", "source": "git+x"}]
        )
        with patch.object(
            routing_cmd, "_provider_family_aliases", return_value=self.ALIASES
        ):
            types = routing_cmd._get_configured_provider_types(settings)
        assert "openai" in types
        assert "openai-chatgpt" not in types

    def test_no_aliases_is_byte_identical_to_before(self, tmp_path: Path) -> None:
        """With an empty table (hook not importable) the set is exactly the old one."""
        settings = _settings_with_providers(
            tmp_path,
            [
                {"module": "provider-anthropic", "source": "git+x", "id": "opus"},
                {"module": "provider-openai-chatgpt", "source": "git+x"},
            ],
        )
        with patch.object(routing_cmd, "_provider_family_aliases", return_value={}):
            types = routing_cmd._get_configured_provider_types(settings)
        assert types == {"anthropic", "opus", "openai-chatgpt"}

    def test_alias_table_comes_from_the_hook_not_a_copy(self) -> None:
        """Single source of truth: the CLI reads the resolver's table verbatim.

        If the hook is importable in this environment the two must be equal;
        if it is not, the CLI must degrade to `{}` rather than raise. Either
        way there is no second copy of the table in this repo to drift.
        """
        try:
            from amplifier_module_hooks_routing.resolver import PROVIDER_FAMILY_ALIASES
        except Exception:
            assert routing_cmd._provider_family_aliases() == {}
        else:
            assert routing_cmd._provider_family_aliases() == dict(
                PROVIDER_FAMILY_ALIASES
            )
