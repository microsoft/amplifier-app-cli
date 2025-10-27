"""
Tests for collections.schema module.

Test CollectionMetadata parsing from pyproject.toml files.
"""

import tomllib
from pathlib import Path
from textwrap import dedent

import pytest
from amplifier_app_cli.collections.schema import CollectionMetadata


def test_from_pyproject_minimal(tmp_path: Path):
    """Test parsing minimal valid pyproject.toml."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        dedent("""
        [project]
        name = "test-collection"
        version = "1.0.0"
    """)
    )

    metadata = CollectionMetadata.from_pyproject(pyproject)

    assert metadata.name == "test-collection"
    assert metadata.version == "1.0.0"
    assert metadata.description == ""
    assert metadata.author == ""
    assert metadata.capabilities == []
    assert metadata.requires == {}


def test_from_pyproject_full(tmp_path: Path):
    """Test parsing complete pyproject.toml with all fields."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        dedent("""
        [project]
        name = "memory-solution"
        version = "2.0.0"
        description = "Memory management expertise"

        [project.urls]
        homepage = "https://docs.example.com"
        repository = "https://github.com/user/memory-solution"

        [tool.amplifier.collection]
        author = "expert-developer"
        capabilities = [
            "Memory analysis",
            "Automated optimization"
        ]
        requires = {foundation = "^1.0.0", toolkit = "^1.2.0"}
    """)
    )

    metadata = CollectionMetadata.from_pyproject(pyproject)

    assert metadata.name == "memory-solution"
    assert metadata.version == "2.0.0"
    assert metadata.description == "Memory management expertise"
    assert metadata.author == "expert-developer"
    assert metadata.capabilities == ["Memory analysis", "Automated optimization"]
    assert metadata.requires == {"foundation": "^1.0.0", "toolkit": "^1.2.0"}
    assert metadata.homepage == "https://docs.example.com"
    assert metadata.repository == "https://github.com/user/memory-solution"


def test_from_pyproject_file_not_found():
    """Test error when pyproject.toml doesn't exist."""
    with pytest.raises(FileNotFoundError, match="pyproject.toml not found"):
        CollectionMetadata.from_pyproject(Path("/nonexistent/pyproject.toml"))


def test_from_pyproject_missing_project_section(tmp_path: Path):
    """Test error when [project] section missing."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        dedent("""
        [tool.amplifier.collection]
        author = "test"
    """)
    )

    with pytest.raises(KeyError, match="\\[project\\] section missing"):
        CollectionMetadata.from_pyproject(pyproject)


def test_from_pyproject_missing_name(tmp_path: Path):
    """Test error when required name field missing."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        dedent("""
        [project]
        version = "1.0.0"
    """)
    )

    with pytest.raises(KeyError, match="name"):
        CollectionMetadata.from_pyproject(pyproject)


def test_from_pyproject_invalid_toml(tmp_path: Path):
    """Test error on invalid TOML syntax."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("invalid toml [[[")

    with pytest.raises(tomllib.TOMLDecodeError):
        CollectionMetadata.from_pyproject(pyproject)
