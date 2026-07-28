"""Regression tests for provider source resolution and install idempotency.

These cover two related failure modes:

1. Install time resolved provider sources from a smaller set of settings keys
   than run time did, so a provider pinned via `amplifier provider add --source`
   or an `overrides.<module_id>.source` entry was installed from @main and then
   run from the pinned source (or silently replaced).

2. `install_known_providers()` reinstalled every known provider unconditionally,
   so a repair path triggered for one missing provider overwrote the other
   providers that were already installed and working.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

from amplifier_app_cli.provider_sources import DEFAULT_PROVIDER_SOURCES
from amplifier_app_cli.provider_sources import get_effective_provider_sources
from amplifier_app_cli.provider_sources import install_known_providers
from amplifier_app_cli.provider_sources import is_provider_module_installed

PINNED = "git+https://github.com/acme/amplifier-module-provider-openai@abc1234"
PINNED_ALT = "git+https://github.com/acme/amplifier-module-provider-openai@feature"


def _settings(
    *,
    module_sources: dict | None = None,
    merged: dict | None = None,
    source_overrides: dict | None = None,
    provider_overrides: list | None = None,
) -> MagicMock:
    """Build a stub AppSettings exposing only the accessors we read."""
    settings = MagicMock()
    settings.get_module_sources.return_value = module_sources or {}
    settings.get_merged_settings.return_value = merged or {}
    settings.get_source_overrides.return_value = source_overrides or {}
    settings.get_provider_overrides.return_value = provider_overrides or []
    return settings


class TestEffectiveProviderSources:
    def test_defaults_when_no_settings(self):
        assert get_effective_provider_sources(None) == DEFAULT_PROVIDER_SOURCES

    def test_config_providers_source_is_honoured(self):
        """`amplifier provider add --source` writes config.providers[].source."""
        settings = _settings(
            provider_overrides=[{"module": "provider-openai", "source": PINNED}]
        )

        sources = get_effective_provider_sources(settings)

        assert sources["provider-openai"] == PINNED

    def test_overrides_block_source_is_honoured(self):
        """settings.yaml `overrides.<module_id>.source` must reach install time."""
        settings = _settings(source_overrides={"provider-openai": PINNED})

        sources = get_effective_provider_sources(settings)

        assert sources["provider-openai"] == PINNED

    def test_config_providers_wins_over_overrides_block(self):
        """Precedence must match runtime/config.py's combined_sources ordering."""
        settings = _settings(
            module_sources={"provider-openai": "git+https://example.com/a@main"},
            source_overrides={"provider-openai": PINNED_ALT},
            provider_overrides=[{"module": "provider-openai", "source": PINNED}],
        )

        sources = get_effective_provider_sources(settings)

        assert sources["provider-openai"] == PINNED

    def test_overrides_block_wins_over_sources_modules(self):
        settings = _settings(
            module_sources={"provider-openai": "git+https://example.com/a@main"},
            source_overrides={"provider-openai": PINNED},
        )

        sources = get_effective_provider_sources(settings)

        assert sources["provider-openai"] == PINNED

    def test_unknown_provider_from_config_providers_is_added(self):
        settings = _settings(
            provider_overrides=[{"module": "provider-acme", "source": PINNED}]
        )

        sources = get_effective_provider_sources(settings)

        assert sources["provider-acme"] == PINNED

    def test_malformed_entries_do_not_break_resolution(self):
        settings = _settings(
            provider_overrides=[
                "not-a-dict",
                {"module": "provider-openai"},  # no source
                {"source": PINNED},  # no module
            ]
        )

        sources = get_effective_provider_sources(settings)

        assert sources == DEFAULT_PROVIDER_SOURCES


class TestInstallKnownProvidersIdempotency:
    @staticmethod
    def _patched(installed_ids: set[str], calls: list[str]):
        """Patch the install path so nothing is actually installed."""

        def fake_source_from_uri(uri: str):
            src = MagicMock()
            src.resolve.return_value = uri
            return src

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd[4])  # ["uv", "pip", "install", "-e", <path>, ...]
            return MagicMock(returncode=0, stderr="")

        return (
            patch(
                "amplifier_app_cli.provider_sources.is_provider_module_installed",
                side_effect=lambda mid: mid in installed_ids,
            ),
            patch(
                "amplifier_app_cli.provider_sources.source_from_uri",
                side_effect=fake_source_from_uri,
            ),
            patch(
                "amplifier_app_cli.provider_sources.subprocess.run",
                side_effect=fake_run,
            ),
        )

    def test_already_installed_providers_are_not_reinstalled(self):
        """The whole point: do not overwrite a provider that already works."""
        sources = {
            "provider-anthropic": "git+https://example.com/anthropic@main",
            "provider-openai": PINNED,
        }
        calls: list[str] = []
        p1, p2, p3 = self._patched({"provider-openai"}, calls)

        with (
            patch(
                "amplifier_app_cli.provider_sources.get_effective_provider_sources",
                return_value=sources,
            ),
            p1,
            p2,
            p3,
        ):
            result = install_known_providers(console=None, verbose=False)

        assert calls == ["git+https://example.com/anthropic@main"]
        # Already-present providers are still reported as available.
        assert set(result) == {"provider-anthropic", "provider-openai"}

    def test_force_reinstalls_everything(self):
        sources = {"provider-openai": PINNED}
        calls: list[str] = []
        p1, p2, p3 = self._patched({"provider-openai"}, calls)

        with (
            patch(
                "amplifier_app_cli.provider_sources.get_effective_provider_sources",
                return_value=sources,
            ),
            p1,
            p2,
            p3,
        ):
            result = install_known_providers(console=None, verbose=False, force=True)

        assert calls == [PINNED]
        assert result == ["provider-openai"]


class TestCheckFirstRunRepairsAllKnownProviders:
    """A missing provider module must trigger a repair of ALL known providers.

    `amplifier update`/`amplifier reset` wipe the whole tool venv, not just the
    currently active provider's module. A user with multiple provider instances
    configured (e.g. anthropic + openai + chat-completions) needs every one of
    them reinstalled, not just the active one -- see
    tests/test_check_first_run_installs_all_providers.py for the full regression
    coverage. This is safe because install_known_providers(force=False) skips
    any provider that is already installed (TestInstallKnownProvidersIdempotency
    above), so it never overwrites a working/pinned install.
    """

    def test_install_known_providers_is_used_for_repair(self):
        from amplifier_app_cli.commands import init as init_cmd

        provider = MagicMock()
        provider.module_id = "provider-openai"
        provider_mgr = MagicMock()
        provider_mgr.get_current_provider.return_value = provider

        # Stateful: installing a provider makes it importable. A constant-False
        # installed-check paired with a repair that reports success would encode
        # a world where installation never works.
        present: set[str] = set()

        def fake_install(*args, **kwargs):
            present.update(["provider-anthropic", "provider-openai"])
            return ["provider-anthropic", "provider-openai"]

        with (
            patch.object(init_cmd, "create_config_manager"),
            patch.object(init_cmd, "ProviderManager", return_value=provider_mgr),
            patch.object(
                init_cmd,
                "_is_provider_module_installed",
                side_effect=lambda m: m in present,
            ),
            patch.object(
                init_cmd, "install_known_providers", side_effect=fake_install
            ) as mock_install_all,
        ):
            needs_init = init_cmd.check_first_run()

        assert needs_init is False
        mock_install_all.assert_called_once()


class TestDanglingEditableInstallIsNotSkipped:
    """A registered entry point is not proof that a provider still works.

    Providers are installed editable (``uv pip install -e <cache_path>``), so
    ``amplifier reset --remove cache`` deletes the target directory while the
    ``.dist-info`` -- and therefore the entry point -- survives in site-packages.
    Treating that as "installed" would skip the provider and leave the user with
    an import failure, breaking the documented cache-wipe recovery path.
    """

    @staticmethod
    def _entry_point(name: str, module: str) -> MagicMock:
        ep = MagicMock()
        ep.name = name
        ep.module = module
        return ep

    def test_entry_point_whose_module_resolves_counts_as_installed(self):
        ep = self._entry_point("provider-openai", "amplifier_module_provider_openai")
        with (
            patch(
                "amplifier_app_cli.provider_sources.importlib.metadata.entry_points",
                return_value=[ep],
            ),
            patch(
                "amplifier_app_cli.provider_sources.importlib.util.find_spec",
                return_value=object(),
            ),
        ):
            assert is_provider_module_installed("provider-openai") is True

    def test_entry_point_with_unresolvable_module_is_not_installed(self):
        """The regression: cache wiped, dist-info left behind."""
        ep = self._entry_point("provider-openai", "amplifier_module_provider_openai")
        with (
            patch(
                "amplifier_app_cli.provider_sources.importlib.metadata.entry_points",
                return_value=[ep],
            ),
            patch(
                "amplifier_app_cli.provider_sources.importlib.util.find_spec",
                return_value=None,
            ),
        ):
            assert is_provider_module_installed("provider-openai") is False

    def test_find_spec_raising_is_treated_as_not_installed(self):
        """A missing parent package makes find_spec raise; that is not installed."""
        ep = self._entry_point("provider-openai", "amplifier_module_provider_openai")
        with (
            patch(
                "amplifier_app_cli.provider_sources.importlib.metadata.entry_points",
                return_value=[ep],
            ),
            patch(
                "amplifier_app_cli.provider_sources.importlib.util.find_spec",
                side_effect=ModuleNotFoundError("no parent"),
            ),
        ):
            assert is_provider_module_installed("provider-openai") is False

    def test_dangling_provider_is_reinstalled_by_install_known_providers(self):
        """End-to-end: a dangling provider must be repaired, not skipped."""
        ep = self._entry_point("provider-openai", "amplifier_module_provider_openai")
        sources = {"provider-openai": PINNED}
        calls: list[str] = []

        def fake_source_from_uri(uri: str):
            src = MagicMock()
            src.resolve.return_value = uri
            return src

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd[4])
            return MagicMock(returncode=0, stderr="")

        with (
            patch(
                "amplifier_app_cli.provider_sources.importlib.metadata.entry_points",
                return_value=[ep],
            ),
            patch(
                "amplifier_app_cli.provider_sources.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "amplifier_app_cli.provider_sources.source_from_uri",
                side_effect=fake_source_from_uri,
            ),
            patch(
                "amplifier_app_cli.provider_sources.subprocess.run",
                side_effect=fake_run,
            ),
            patch(
                "amplifier_app_cli.provider_sources.get_effective_provider_sources",
                return_value=sources,
            ),
        ):
            install_known_providers(config_manager=None, console=None, verbose=False)

        assert calls == [PINNED], "dangling provider should have been reinstalled"
