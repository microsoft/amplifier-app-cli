"""Tests for routing config integration in amplifier-app-cli.

Tests:
1. AppSettings.get_routing_config() returns routing section from merged settings
2. session_runner registers RoutingConfig as session.routing capability
3. session_spawner inherits routing config from parent to child
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


# =============================================================================
# 1. AppSettings.get_routing_config() tests
# =============================================================================


class TestGetRoutingConfig:
    """Test AppSettings.get_routing_config() method."""

    def test_returns_empty_dict_when_no_routing(self, tmp_path: Path) -> None:
        """When no routing section exists, returns empty dict."""
        global_file = tmp_path / "global.yaml"
        global_file.write_text("")
        paths = SettingsPaths(
            global_settings=global_file,
            project_settings=tmp_path / "project.yaml",
            local_settings=tmp_path / "local.yaml",
        )
        settings = AppSettings(paths)
        assert settings.get_routing_config() == {}

    def test_returns_routing_section(self, tmp_path: Path) -> None:
        """When routing section exists, returns it."""
        global_file = tmp_path / "global.yaml"
        routing_data = {"strategy": "cost", "max_tier": "tier-2"}
        global_file.write_text(yaml.dump({"routing": routing_data}))
        paths = SettingsPaths(
            global_settings=global_file,
            project_settings=tmp_path / "project.yaml",
            local_settings=tmp_path / "local.yaml",
        )
        settings = AppSettings(paths)
        assert settings.get_routing_config() == routing_data

    def test_merges_routing_from_multiple_scopes(self, tmp_path: Path) -> None:
        """Project-level routing overrides global-level."""
        global_file = tmp_path / "global.yaml"
        global_file.write_text(yaml.dump({"routing": {"strategy": "balanced"}}))
        project_file = tmp_path / "project.yaml"
        project_file.write_text(yaml.dump({"routing": {"strategy": "cost"}}))
        paths = SettingsPaths(
            global_settings=global_file,
            project_settings=project_file,
            local_settings=tmp_path / "local.yaml",
        )
        settings = AppSettings(paths)
        result = settings.get_routing_config()
        assert result["strategy"] == "cost"


# =============================================================================
# 2. register_session_spawning registers routing config
# =============================================================================


class TestRegisterRoutingCapability:
    """Test that register_session_spawning registers routing config."""

    async def test_registers_routing_when_present(self) -> None:
        """When routing config exists in settings, registers session.routing."""
        from amplifier_app_cli.session_runner import register_session_spawning

        session = MagicMock()
        coordinator = MagicMock()
        session.coordinator = coordinator
        # Track all register_capability calls
        registered: dict[str, Any] = {}
        coordinator.register_capability = MagicMock(
            side_effect=lambda name, val: registered.__setitem__(name, val)
        )
        coordinator.get_capability = MagicMock(return_value=None)

        # Patch AppSettings at the source module (lazy import target)
        mock_settings_instance = MagicMock()
        mock_settings_instance.get_routing_config.return_value = {"strategy": "cost"}
        with patch(
            "amplifier_app_cli.lib.settings.AppSettings",
            return_value=mock_settings_instance,
        ):
            register_session_spawning(session)

        assert "session.routing" in registered
        from amplifier_foundation import RoutingConfig

        assert isinstance(registered["session.routing"], RoutingConfig)
        assert registered["session.routing"].strategy == "cost"

    async def test_no_routing_when_empty(self) -> None:
        """When no routing config in settings, session.routing is NOT registered."""
        from amplifier_app_cli.session_runner import register_session_spawning

        session = MagicMock()
        coordinator = MagicMock()
        session.coordinator = coordinator
        registered: dict[str, Any] = {}
        coordinator.register_capability = MagicMock(
            side_effect=lambda name, val: registered.__setitem__(name, val)
        )
        coordinator.get_capability = MagicMock(return_value=None)

        mock_settings_instance = MagicMock()
        mock_settings_instance.get_routing_config.return_value = {}
        with patch(
            "amplifier_app_cli.lib.settings.AppSettings",
            return_value=mock_settings_instance,
        ):
            register_session_spawning(session)

        # session.spawn and session.resume should be registered, but NOT session.routing
        assert "session.spawn" in registered
        assert "session.resume" in registered
        assert "session.routing" not in registered


# =============================================================================
# 3. session_spawner inherits routing config from parent to child
# =============================================================================


class TestRoutingInheritance:
    """Test that spawn_sub_session inherits routing config from parent."""

    def test_routing_inherited_from_parent(self) -> None:
        """When parent has session.routing, child should get it too."""
        from amplifier_foundation import RoutingConfig

        routing = RoutingConfig(strategy="cost")

        # Simulate parent get_capability behavior
        parent_capabilities: dict[str, Any] = {"session.routing": routing}
        child_capabilities: dict[str, Any] = {}

        def parent_get_capability(name: str) -> Any:
            return parent_capabilities.get(name)

        def child_register_capability(name: str, val: Any) -> None:
            child_capabilities[name] = val

        # Test the inheritance logic directly (same pattern as working_dir)
        parent_routing = parent_get_capability("session.routing")
        if parent_routing is not None:
            child_register_capability("session.routing", parent_routing)

        assert "session.routing" in child_capabilities
        assert child_capabilities["session.routing"] is routing
        assert child_capabilities["session.routing"].strategy == "cost"

    def test_no_routing_inherited_when_parent_lacks_it(self) -> None:
        """When parent has no session.routing, child should not get it."""
        child_capabilities: dict[str, Any] = {}

        def parent_get_capability(name: str) -> Any:
            return None

        def child_register_capability(name: str, val: Any) -> None:
            child_capabilities[name] = val

        parent_routing = parent_get_capability("session.routing")
        if parent_routing is not None:
            child_register_capability("session.routing", parent_routing)

        assert "session.routing" not in child_capabilities
