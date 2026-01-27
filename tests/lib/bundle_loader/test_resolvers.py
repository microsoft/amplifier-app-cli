"""Tests for module resolvers.

These tests verify the source_hint parameter behavior in resolver classes,
ensuring proper module resolution through the multi-layer fallback strategy.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from amplifier_app_cli.lib.bundle_loader.resolvers import (
    AppModuleResolver,
    FoundationFileSource,
    FoundationGitSource,
    FoundationPackageSource,
    FoundationSettingsResolver,
    ModuleResolutionError,
)


class TestFoundationFileSource:
    """Tests for FoundationFileSource."""

    def test_resolve_valid_directory(self, tmp_path: Path):
        """Valid directory with Python files resolves successfully."""
        # Create a valid module directory
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("# module")

        source = FoundationFileSource(module_dir)
        result = source.resolve()

        assert result == module_dir

    def test_resolve_missing_directory_raises(self, tmp_path: Path):
        """Missing directory raises ModuleResolutionError."""
        source = FoundationFileSource(tmp_path / "nonexistent")

        with pytest.raises(ModuleResolutionError, match="not found"):
            source.resolve()

    def test_resolve_file_not_directory_raises(self, tmp_path: Path):
        """File (not directory) raises ModuleResolutionError."""
        file_path = tmp_path / "not_a_dir.py"
        file_path.write_text("# not a directory")

        source = FoundationFileSource(file_path)

        with pytest.raises(ModuleResolutionError, match="not a directory"):
            source.resolve()

    def test_resolve_empty_directory_raises(self, tmp_path: Path):
        """Directory without Python files raises ModuleResolutionError."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        source = FoundationFileSource(empty_dir)

        with pytest.raises(ModuleResolutionError, match="valid Python module"):
            source.resolve()

    def test_file_uri_prefix_stripped(self, tmp_path: Path):
        """file:// URI prefix is properly stripped."""
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("# module")

        source = FoundationFileSource(f"file://{module_dir}")

        assert source.path == module_dir


class TestFoundationPackageSource:
    """Tests for FoundationPackageSource."""

    def test_resolve_installed_package(self):
        """Installed package resolves successfully."""
        # pytest is guaranteed to be installed in test environment
        source = FoundationPackageSource("pytest")
        result = source.resolve()

        assert result.exists()

    def test_resolve_missing_package_raises(self):
        """Missing package raises ModuleResolutionError."""
        source = FoundationPackageSource("definitely-not-a-real-package-xyz123")

        with pytest.raises(ModuleResolutionError, match="not installed"):
            source.resolve()


class TestFoundationSettingsResolver:
    """Tests for FoundationSettingsResolver source_hint behavior."""

    def test_resolve_with_source_hint_file_path(self, tmp_path: Path):
        """source_hint with file path returns FoundationFileSource."""
        # Create a valid module directory
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("# module")

        resolver = FoundationSettingsResolver()
        result = resolver.resolve("my-module", source_hint=str(module_dir))

        assert isinstance(result, FoundationFileSource)
        assert result.path == module_dir

    def test_resolve_with_source_hint_git_uri(self):
        """source_hint with git+ URI returns FoundationGitSource."""
        resolver = FoundationSettingsResolver()
        git_uri = (
            "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main"
        )

        result = resolver.resolve("provider-anthropic", source_hint=git_uri)

        assert isinstance(result, FoundationGitSource)
        assert result.uri == git_uri

    def test_resolve_with_source_hint_package_name(self):
        """source_hint with package name returns FoundationPackageSource."""
        resolver = FoundationSettingsResolver()

        result = resolver.resolve("some-module", source_hint="pytest")

        assert isinstance(result, FoundationPackageSource)
        assert result.package_name == "pytest"

    def test_resolve_with_layer_returns_source_hint_layer(self, tmp_path: Path):
        """resolve_with_layer returns 'source_hint' as layer when hint is used."""
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("# module")

        resolver = FoundationSettingsResolver()
        source, layer = resolver.resolve_with_layer(
            "my-module", source_hint=str(module_dir)
        )

        assert layer == "source_hint"
        assert isinstance(source, FoundationFileSource)

    def test_resolve_env_var_takes_precedence(self, tmp_path: Path, monkeypatch):
        """Environment variable takes precedence over source_hint."""
        # Create two different module directories
        env_module = tmp_path / "env_module"
        env_module.mkdir()
        (env_module / "__init__.py").write_text("# env module")

        hint_module = tmp_path / "hint_module"
        hint_module.mkdir()
        (hint_module / "__init__.py").write_text("# hint module")

        # Set environment variable
        monkeypatch.setenv("AMPLIFIER_MODULE_MY_MODULE", str(env_module))

        resolver = FoundationSettingsResolver()
        source, layer = resolver.resolve_with_layer(
            "my-module", source_hint=str(hint_module)
        )

        assert layer == "env"
        assert isinstance(source, FoundationFileSource)
        assert source.path == env_module

    def test_resolve_workspace_takes_precedence_over_hint(self, tmp_path: Path):
        """Workspace convention takes precedence over source_hint."""
        # Create workspace with module
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        workspace_module = workspace / "my-module"
        workspace_module.mkdir()
        (workspace_module / "__init__.py").write_text("# workspace module")

        # Create hint module
        hint_module = tmp_path / "hint_module"
        hint_module.mkdir()
        (hint_module / "__init__.py").write_text("# hint module")

        resolver = FoundationSettingsResolver(workspace_dir=workspace)
        source, layer = resolver.resolve_with_layer(
            "my-module", source_hint=str(hint_module)
        )

        assert layer == "workspace"
        assert isinstance(source, FoundationFileSource)
        assert source.path == workspace_module

    def test_resolve_settings_takes_precedence_over_hint(self, tmp_path: Path):
        """Settings provider takes precedence over source_hint."""
        # Create settings module
        settings_module = tmp_path / "settings_module"
        settings_module.mkdir()
        (settings_module / "__init__.py").write_text("# settings module")

        # Create hint module
        hint_module = tmp_path / "hint_module"
        hint_module.mkdir()
        (hint_module / "__init__.py").write_text("# hint module")

        # Mock settings provider
        settings_provider = MagicMock()
        settings_provider.get_module_sources.return_value = {
            "my-module": str(settings_module)
        }

        resolver = FoundationSettingsResolver(settings_provider=settings_provider)
        source, layer = resolver.resolve_with_layer(
            "my-module", source_hint=str(hint_module)
        )

        assert layer == "settings"
        assert isinstance(source, FoundationFileSource)
        assert source.path == settings_module

    def test_resolve_falls_back_to_package_without_hint(self):
        """Without source_hint, falls back to package resolution."""
        resolver = FoundationSettingsResolver()

        # pytest is installed, so it should resolve
        source, layer = resolver.resolve_with_layer("pytest")

        assert layer == "package"
        assert isinstance(source, FoundationPackageSource)

    def test_resolve_dict_source_git(self):
        """Dict format source with type=git works correctly."""
        resolver = FoundationSettingsResolver()
        dict_source = {
            "type": "git",
            "url": "https://github.com/microsoft/amplifier-module-test",
            "ref": "v1.0.0",
        }

        # Use settings provider to inject dict source
        settings_provider = MagicMock()
        settings_provider.get_module_sources.return_value = {"test-module": dict_source}

        resolver = FoundationSettingsResolver(settings_provider=settings_provider)
        source, layer = resolver.resolve_with_layer("test-module")

        assert layer == "settings"
        assert isinstance(source, FoundationGitSource)
        assert "v1.0.0" in source.uri

    def test_resolve_dict_source_file(self, tmp_path: Path):
        """Dict format source with type=file works correctly."""
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("# module")

        dict_source = {"type": "file", "path": str(module_dir)}

        settings_provider = MagicMock()
        settings_provider.get_module_sources.return_value = {"test-module": dict_source}

        resolver = FoundationSettingsResolver(settings_provider=settings_provider)
        source, layer = resolver.resolve_with_layer("test-module")

        assert layer == "settings"
        assert isinstance(source, FoundationFileSource)

    def test_resolve_dict_source_package(self):
        """Dict format source with type=package works correctly."""
        dict_source = {"type": "package", "name": "pytest"}

        settings_provider = MagicMock()
        settings_provider.get_module_sources.return_value = {"test-module": dict_source}

        resolver = FoundationSettingsResolver(settings_provider=settings_provider)
        source, layer = resolver.resolve_with_layer("test-module")

        assert layer == "settings"
        assert isinstance(source, FoundationPackageSource)
        assert source.package_name == "pytest"


class TestAppModuleResolver:
    """Tests for AppModuleResolver source_hint behavior."""

    def test_resolve_with_source_hint_passes_to_bundle(self, tmp_path: Path):
        """source_hint is passed to bundle resolver."""
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("# module")

        # Mock bundle resolver that uses hint
        bundle_resolver = MagicMock()
        bundle_resolver.resolve.return_value = FoundationFileSource(module_dir)

        app_resolver = AppModuleResolver(bundle_resolver=bundle_resolver)
        result = app_resolver.resolve("my-module", source_hint=str(module_dir))

        bundle_resolver.resolve.assert_called_once_with("my-module", str(module_dir))
        assert isinstance(result, FoundationFileSource)

    def test_resolve_falls_back_to_settings_resolver(self, tmp_path: Path):
        """Falls back to settings resolver when bundle doesn't have module."""
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__init__.py").write_text("# module")

        # Mock bundle resolver that raises ModuleNotFoundError
        bundle_resolver = MagicMock()
        bundle_resolver.resolve.side_effect = ModuleNotFoundError("not in bundle")

        # Mock settings resolver that succeeds
        settings_resolver = MagicMock()
        settings_resolver.resolve.return_value = FoundationFileSource(module_dir)

        app_resolver = AppModuleResolver(
            bundle_resolver=bundle_resolver,
            settings_resolver=settings_resolver,
        )
        result = app_resolver.resolve("my-module", source_hint=str(module_dir))

        assert isinstance(result, FoundationFileSource)
        settings_resolver.resolve.assert_called_once()

    def test_resolve_raises_when_both_fail(self):
        """Raises ModuleNotFoundError when both resolvers fail."""
        # Mock bundle resolver that raises
        bundle_resolver = MagicMock()
        bundle_resolver.resolve.side_effect = ModuleNotFoundError("not in bundle")
        bundle_resolver._paths = {}

        # Mock settings resolver that also fails
        settings_resolver = MagicMock()
        settings_resolver.resolve.side_effect = ModuleResolutionError("not in settings")

        app_resolver = AppModuleResolver(
            bundle_resolver=bundle_resolver,
            settings_resolver=settings_resolver,
        )

        with pytest.raises(ModuleNotFoundError, match="not found"):
            app_resolver.resolve("nonexistent-module")

    def test_get_module_source_checks_bundle_first(self, tmp_path: Path):
        """get_module_source checks bundle paths first."""
        bundle_resolver = MagicMock()
        bundle_resolver._paths = {"my-module": tmp_path / "bundle_module"}

        app_resolver = AppModuleResolver(bundle_resolver=bundle_resolver)
        result = app_resolver.get_module_source("my-module")

        assert result == str(tmp_path / "bundle_module")

    def test_get_module_source_falls_back_to_settings(self):
        """get_module_source falls back to settings resolver."""
        bundle_resolver = MagicMock()
        bundle_resolver._paths = {}

        settings_resolver = MagicMock()
        settings_resolver.get_module_source.return_value = "/path/to/module"

        app_resolver = AppModuleResolver(
            bundle_resolver=bundle_resolver,
            settings_resolver=settings_resolver,
        )
        result = app_resolver.get_module_source("my-module")

        assert result == "/path/to/module"
