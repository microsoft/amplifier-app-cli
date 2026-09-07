"""`routing show` must tell the truth to two users it used to mislead.

1. THE ALIAS-BLIND USER. A ChatGPT-subscription-only user (`openai-chatgpt`
   module, no `openai` module) is routed fine at runtime -- the hook's
   ``PROVIDER_FAMILY_ALIASES`` maps ``provider: openai`` onto that mount -- but
   `routing show` computed "is this candidate configured?" with a bare
   set-membership test that never consulted the alias table, and told exactly
   that user "✗ not configured" for every openai role. Measured in a DTU,
   2026-09-07.

2. THE HOOK-LESS USER. `routing show` reads matrix files straight from the
   bundle cache, so it renders a matrix as "★ active" on a host whose ACTIVE
   BUNDLE never mounts `hooks-routing` -- and then nothing applies the matrix,
   and every sub-agent silently inherits the parent's provider. Measured on a
   project pinned to `anchors-amp-dev` (a lean base without routing-matrix):
   `routing show` said "balanced ... active" for every role while
   `delegate:agent_spawned` carried `provider_preferences: null`.

Both fixes live in `commands/routing.py`; this file pins them.
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


# ---------------------------------------------------------------------------
# 2. "Displayed but not mounted" warning
# ---------------------------------------------------------------------------


class TestNotMountedWarning:
    def _settings(self, tmp_path: Path, active: str) -> AppSettings:
        paths = SettingsPaths(
            global_settings=tmp_path / "global" / "settings.yaml",
            project_settings=tmp_path / "project" / "settings.yaml",
            local_settings=tmp_path / "local" / "settings.local.yaml",
        )
        paths.global_settings.parent.mkdir(parents=True, exist_ok=True)
        paths.global_settings.write_text(
            yaml.safe_dump({"bundle": {"active": active}}), encoding="utf-8"
        )
        return AppSettings(paths=paths)

    def test_warns_when_hook_is_not_composed(self, tmp_path: Path, capsys) -> None:
        settings = self._settings(tmp_path, "anchors-amp-dev")
        with (
            patch.object(routing_cmd, "_routing_hook_is_composed", return_value=False),
            patch.object(routing_cmd.console, "print") as out,
        ):
            routing_cmd._print_not_mounted_warning(settings)
        text = " ".join(str(c.args[0]) for c in out.call_args_list)
        assert "Not applied in your sessions" in text
        assert "anchors-amp-dev" in text, (
            "must name the bundle that is missing the hook"
        )
        assert "hooks-routing" in text
        assert routing_cmd.ROUTING_BEHAVIOR_URI in text, (
            "must hand the user the exact `bundle add` command, not a description of one"
        )
        assert "--app" in text

    def test_silent_when_hook_is_composed(self, tmp_path: Path) -> None:
        settings = self._settings(tmp_path, "foundation")
        with (
            patch.object(routing_cmd, "_routing_hook_is_composed", return_value=True),
            patch.object(routing_cmd.console, "print") as out,
        ):
            routing_cmd._print_not_mounted_warning(settings)
        out.assert_not_called()

    def test_silent_when_it_cannot_tell(self, tmp_path: Path) -> None:
        """A diagnostic that cannot run must never turn into a false alarm."""
        settings = self._settings(tmp_path, "whatever")
        with (
            patch.object(routing_cmd, "_routing_hook_is_composed", return_value=None),
            patch.object(routing_cmd.console, "print") as out,
        ):
            routing_cmd._print_not_mounted_warning(settings)
        out.assert_not_called()

    def test_detection_failure_degrades_to_none_not_an_exception(
        self, tmp_path: Path
    ) -> None:
        """`routing show` must keep working on a host where composition blows up."""
        settings = self._settings(tmp_path, "anchors-amp-dev")
        with patch(
            "amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery",
            side_effect=RuntimeError("no registry here"),
        ):
            assert routing_cmd._routing_hook_is_composed(settings) is None

    def test_behavior_uri_is_the_one_the_bundle_ships(self) -> None:
        """The remediation must point at the real behaviors file, on main."""
        uri = routing_cmd.ROUTING_BEHAVIOR_URI
        assert uri.startswith(
            "git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main"
        )
        assert uri.endswith("#subdirectory=behaviors/routing.yaml")

    def test_no_active_bundle_means_no_composition_and_no_warning(
        self, tmp_path: Path
    ) -> None:
        """No explicit active bundle -> the CLI's built-in default (foundation,
        which includes routing-matrix). Nothing to warn about, and the
        composition must not even run -- that is what keeps this diagnostic out
        of every fixture that never chose a bundle."""
        paths = SettingsPaths(
            global_settings=tmp_path / "g.yaml",
            project_settings=tmp_path / "p.yaml",
            local_settings=tmp_path / "l.yaml",
        )
        settings = AppSettings(paths=paths)
        assert settings.get_active_bundle() is None
        with patch(
            "amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery",
            side_effect=AssertionError("composition must not run"),
        ):
            assert routing_cmd._routing_hook_is_composed(settings) is None
