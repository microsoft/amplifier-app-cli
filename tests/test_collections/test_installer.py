"""
Tests for collection installer.

Note: These tests use mocked GitSource to avoid actual git downloads.
Per IMPLEMENTATION_PHILOSOPHY: Test behavior, not implementation.
"""

from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from amplifier_app_cli.collections.installer import CollectionInstallError
from amplifier_app_cli.collections.installer import install_collection
from amplifier_app_cli.collections.installer import install_scenario_tools
from amplifier_app_cli.collections.installer import is_collection_installed
from amplifier_app_cli.collections.installer import uninstall_collection
from amplifier_app_cli.collections.installer import uninstall_scenario_tools


@pytest.fixture
def mock_collection(tmp_path):
    """Create a mock collection directory with metadata."""
    collection_path = tmp_path / "mock-collection"
    collection_path.mkdir()

    # Create pyproject.toml
    (collection_path / "pyproject.toml").write_text("""
[project]
name = "test-collection"
version = "1.0.0"
description = "Test collection"

[tool.amplifier.collection]
author = "Test Author"
capabilities = ["testing"]
""")

    # Create some resource files
    profiles_dir = collection_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "test.md").write_text("# Test Profile")

    return collection_path


def test_install_collection_success(tmp_path, mock_collection):
    """Test successful collection installation."""
    target_dir = tmp_path / "collections"

    # Mock GitSource to return our mock collection
    with patch("amplifier_app_cli.collections.installer.GitSource") as mock_git_source:
        mock_source = MagicMock()
        mock_source.resolve.return_value = mock_collection
        mock_git_source.from_uri.return_value = mock_source

        # Install
        path, metadata = install_collection(
            "git+https://example.com/test-collection@main",
            target_dir=target_dir,
        )

        # Verify installation
        assert path == target_dir / "test-collection"
        assert path.exists()
        assert (path / "pyproject.toml").exists()
        assert (path / "profiles" / "test.md").exists()

        assert metadata.name == "test-collection"
        assert metadata.version == "1.0.0"


def test_install_collection_no_pyproject(tmp_path):
    """Test installation fails if no pyproject.toml."""
    target_dir = tmp_path / "collections"

    # Mock collection without pyproject.toml
    invalid_collection = tmp_path / "invalid"
    invalid_collection.mkdir()

    with patch("amplifier_app_cli.collections.installer.GitSource") as mock_git_source:
        mock_source = MagicMock()
        mock_source.resolve.return_value = invalid_collection
        mock_git_source.from_uri.return_value = mock_source

        # Should fail
        with pytest.raises(CollectionInstallError, match="No pyproject.toml"):
            install_collection(
                "git+https://example.com/invalid@main",
                target_dir=target_dir,
            )


def test_install_collection_replaces_existing(tmp_path, mock_collection):
    """Test installing over existing collection."""
    target_dir = tmp_path / "collections"

    # Create existing installation
    existing = target_dir / "test-collection"
    existing.mkdir(parents=True)
    (existing / "old-file.txt").write_text("old")

    # Mock GitSource
    with patch("amplifier_app_cli.collections.installer.GitSource") as mock_git_source:
        mock_source = MagicMock()
        mock_source.resolve.return_value = mock_collection
        mock_git_source.from_uri.return_value = mock_source

        # Install (should replace)
        path, metadata = install_collection(
            "git+https://example.com/test-collection@main",
            target_dir=target_dir,
        )

        # Old file should be gone
        assert not (path / "old-file.txt").exists()

        # New files should exist
        assert (path / "pyproject.toml").exists()
        assert (path / "profiles" / "test.md").exists()


def test_install_collection_local_flag(tmp_path, mock_collection):
    """Test local flag installs to .amplifier/collections."""
    # Change to tmp_path as CWD
    import os

    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)

        with patch("amplifier_app_cli.collections.installer.GitSource") as mock_git_source:
            mock_source = MagicMock()
            mock_source.resolve.return_value = mock_collection
            mock_git_source.from_uri.return_value = mock_source

            # Install with local=True
            path, metadata = install_collection(
                "git+https://example.com/test-collection@main",
                local=True,
            )

            # Should install to .amplifier/collections
            assert path == tmp_path / ".amplifier" / "collections" / "test-collection"
            assert path.exists()

    finally:
        os.chdir(original_cwd)


def test_uninstall_collection_success(tmp_path):
    """Test successful collection uninstallation."""
    target_dir = tmp_path / "collections"

    # Create installed collection
    collection_path = target_dir / "test-collection"
    collection_path.mkdir(parents=True)
    (collection_path / "pyproject.toml").write_text('[project]\nname = "test"\nversion = "1.0.0"\n')

    # Uninstall
    uninstall_collection("test-collection", target_dir=target_dir)

    # Should be gone
    assert not collection_path.exists()


def test_uninstall_collection_not_found(tmp_path):
    """Test uninstalling non-existent collection fails."""
    target_dir = tmp_path / "collections"
    target_dir.mkdir()

    with pytest.raises(CollectionInstallError, match="not found"):
        uninstall_collection("nonexistent", target_dir=target_dir)


def test_is_collection_installed(tmp_path):
    """Test checking if collection is installed."""
    target_dir = tmp_path / "collections"

    # Not installed initially
    assert not is_collection_installed("test-collection", target_dir=target_dir)

    # Create installed collection
    collection_path = target_dir / "test-collection"
    collection_path.mkdir(parents=True)
    (collection_path / "pyproject.toml").write_text('[project]\nname = "test"\nversion = "1.0.0"\n')

    # Now installed
    assert is_collection_installed("test-collection", target_dir=target_dir)


def test_is_collection_installed_no_pyproject(tmp_path):
    """Test collection without pyproject.toml is not considered installed."""
    target_dir = tmp_path / "collections"

    # Create directory without pyproject.toml
    collection_path = target_dir / "invalid"
    collection_path.mkdir(parents=True)

    # Not considered installed
    assert not is_collection_installed("invalid", target_dir=target_dir)


# Scenario Tools Tests


def test_install_scenario_tools_no_tools(tmp_path):
    """Test installing scenario tools when none exist."""
    collection_path = tmp_path / "no-tools"
    collection_path.mkdir()

    # No scenario-tools directory
    tools = install_scenario_tools(collection_path)

    assert tools == []


def test_install_scenario_tools_success(tmp_path):
    """Test successful scenario tools installation."""
    collection_path = tmp_path / "with-tools"
    collection_path.mkdir()

    # Create scenario-tools directory
    tools_dir = collection_path / "scenario-tools"
    tools_dir.mkdir()

    # Create a valid tool
    tool_path = tools_dir / "analyzer"
    tool_path.mkdir()
    (tool_path / "pyproject.toml").write_text('[project]\nname = "analyzer"\nversion = "1.0.0"\n')

    # Mock subprocess.run to simulate uv tool install
    with patch("amplifier_app_cli.collections.installer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Installed successfully")

        tools = install_scenario_tools(collection_path)

        # Verify uv tool install was called
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "uv"
        assert call_args[1] == "tool"
        assert call_args[2] == "install"

        assert tools == ["analyzer"]


def test_install_scenario_tools_failure_continues(tmp_path, caplog):
    """Test that failed tool installation doesn't stop the process."""
    collection_path = tmp_path / "mixed-tools"
    collection_path.mkdir()

    # Create scenario-tools directory with 2 tools
    tools_dir = collection_path / "scenario-tools"
    tools_dir.mkdir()

    tool1 = tools_dir / "tool1"
    tool1.mkdir()
    (tool1 / "pyproject.toml").write_text('[project]\nname = "tool1"\nversion = "1.0.0"\n')

    tool2 = tools_dir / "tool2"
    tool2.mkdir()
    (tool2 / "pyproject.toml").write_text('[project]\nname = "tool2"\nversion = "1.0.0"\n')

    # Mock subprocess - first fails, second succeeds
    with patch("amplifier_app_cli.collections.installer.subprocess.run") as mock_run:

        def side_effect(*args, **kwargs):
            cmd = args[0]
            tool_path = cmd[3]
            if "tool1" in tool_path:
                # First tool fails
                import subprocess

                raise subprocess.CalledProcessError(1, cmd, stderr="Installation failed")
            # Second tool succeeds
            return MagicMock(returncode=0, stdout="Success")

        mock_run.side_effect = side_effect

        tools = install_scenario_tools(collection_path)

        # Only tool2 should be installed
        assert tools == ["tool2"]


def test_uninstall_scenario_tools_success(tmp_path):
    """Test successful scenario tools uninstallation."""
    collection_path = tmp_path / "with-tools"
    collection_path.mkdir()

    # Create scenario-tools directory
    tools_dir = collection_path / "scenario-tools"
    tools_dir.mkdir()

    # Create a valid tool
    tool_path = tools_dir / "analyzer"
    tool_path.mkdir()
    (tool_path / "pyproject.toml").write_text('[project]\nname = "analyzer"\nversion = "1.0.0"\n')

    # Mock subprocess.run to simulate uv tool uninstall
    with patch("amplifier_app_cli.collections.installer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Uninstalled successfully")

        tools = uninstall_scenario_tools(collection_path)

        # Verify uv tool uninstall was called
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "uv"
        assert call_args[1] == "tool"
        assert call_args[2] == "uninstall"
        assert call_args[3] == "analyzer"  # Package name from pyproject.toml

        assert tools == ["analyzer"]


def test_uninstall_scenario_tools_no_tools(tmp_path):
    """Test uninstalling scenario tools when none exist."""
    collection_path = tmp_path / "no-tools"
    collection_path.mkdir()

    # No scenario-tools directory
    tools = uninstall_scenario_tools(collection_path)

    assert tools == []
