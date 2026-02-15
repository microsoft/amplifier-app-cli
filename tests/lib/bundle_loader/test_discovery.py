"""Tests for AppBundleDiscovery.

Regression tests for https://github.com/microsoft-amplifier/amplifier-support/issues/62
"""

import json
from datetime import datetime
from unittest.mock import patch


from amplifier_foundation.registry import BundleRegistry, BundleState


class TestGetBundleCategoriesNestedURIs:
    """Verify get_bundle_categories reads nested bundle URIs from in-memory registry."""

    @patch(
        "amplifier_app_cli.lib.bundle_loader.discovery.AppBundleDiscovery._load_user_registry"
    )
    @patch(
        "amplifier_app_cli.lib.bundle_loader.discovery.AppBundleDiscovery._register_well_known_bundles"
    )
    def test_nested_bundles_reflect_in_memory_state(
        self, mock_well_known, mock_user_registry, tmp_path
    ):
        """Nested bundle URIs come from in-memory registry, not disk."""
        from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery

        registry = BundleRegistry(home=tmp_path / "home")
        discovery = AppBundleDiscovery(search_paths=[], registry=registry)

        # Inject a nested bundle entry into the in-memory registry
        registry._registry["behavior-sessions"] = BundleState(
            uri="file:///local/path/behaviors/sessions.yaml",
            name="behavior-sessions",
            is_root=False,
            root_name="foundation",
            loaded_at=datetime.now(),
        )

        with patch("amplifier_app_cli.lib.settings.AppSettings") as mock_settings_cls:
            mock_settings_cls.return_value.get_added_bundles.return_value = {}
            categories = discovery.get_bundle_categories()

        nested = categories["nested_bundles"]
        nested_names = [b["name"] for b in nested]
        assert "behavior-sessions" in nested_names

        entry = next(b for b in nested if b["name"] == "behavior-sessions")
        assert entry["uri"] == "file:///local/path/behaviors/sessions.yaml"
        assert entry["root"] == "foundation"

    @patch(
        "amplifier_app_cli.lib.bundle_loader.discovery.AppBundleDiscovery._load_user_registry"
    )
    @patch(
        "amplifier_app_cli.lib.bundle_loader.discovery.AppBundleDiscovery._register_well_known_bundles"
    )
    def test_stale_persisted_uri_not_used_for_nested_bundles(
        self, mock_well_known, mock_user_registry, tmp_path
    ):
        """In-memory URI takes precedence over stale persisted data on disk."""
        home = tmp_path / "home"

        # Write stale registry.json with old git+ URI
        home.mkdir(parents=True)
        stale_data = {
            "version": 1,
            "bundles": {
                "behavior-agents": {
                    "uri": "git+https://github.com/example/old@main#subdirectory=behaviors/agents",
                    "name": "behavior-agents",
                    "version": "1.0.0",
                    "loaded_at": None,
                    "checked_at": None,
                    "local_path": None,
                    "is_root": False,
                    "root_name": "foundation",
                    "explicitly_requested": False,
                    "app_bundle": False,
                }
            },
        }
        (home / "registry.json").write_text(json.dumps(stale_data, indent=2))

        from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery

        # Registry loads persisted state, then we update in-memory
        registry = BundleRegistry(home=home)
        discovery = AppBundleDiscovery(search_paths=[], registry=registry)

        # Simulate runtime override: update the URI in-memory
        registry._registry[
            "behavior-agents"
        ].uri = "file:///local/override/behaviors/agents.yaml"

        with patch(
            "amplifier_app_cli.lib.settings.AppSettings"
        ) as mock_settings_cls:
            mock_settings_cls.return_value.get_added_bundles.return_value = {}
            categories = discovery.get_bundle_categories()

        nested = categories["nested_bundles"]
        entry = next(b for b in nested if b["name"] == "behavior-agents")

        # Must reflect the in-memory URI, not the stale git+ from disk
        assert entry["uri"] == "file:///local/override/behaviors/agents.yaml"
        assert "git+" not in entry["uri"]

    @patch(
        "amplifier_app_cli.lib.bundle_loader.discovery.AppBundleDiscovery._load_user_registry"
    )
    @patch(
        "amplifier_app_cli.lib.bundle_loader.discovery.AppBundleDiscovery._register_well_known_bundles"
    )
    def test_dependencies_reflect_in_memory_state(
        self, mock_well_known, mock_user_registry, tmp_path
    ):
        """Dependency URIs also come from in-memory registry."""
        from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery

        registry = BundleRegistry(home=tmp_path / "home")
        discovery = AppBundleDiscovery(search_paths=[], registry=registry)

        # Inject a dependency entry (is_root=True, not explicitly requested)
        registry._registry["lsp-python"] = BundleState(
            uri="git+https://github.com/microsoft/amplifier-bundle-lsp-python@main",
            name="lsp-python",
            is_root=True,
            explicitly_requested=False,
            included_by=["foundation"],
            loaded_at=datetime.now(),
        )

        with patch(
            "amplifier_app_cli.lib.settings.AppSettings"
        ) as mock_settings_cls:
            mock_settings_cls.return_value.get_added_bundles.return_value = {}
            categories = discovery.get_bundle_categories()

        deps = categories["dependencies"]
        dep_names = [b["name"] for b in deps]
        assert "lsp-python" in dep_names

        entry = next(b for b in deps if b["name"] == "lsp-python")
        assert entry["included_by"] == "foundation"
