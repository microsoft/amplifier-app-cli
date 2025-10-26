"""
Collection metadata schema - Parse pyproject.toml files.

Per KERNEL_PHILOSOPHY: This is APP LAYER - parsing format is policy, not mechanism.
Per AGENTS.md: Ruthless simplicity - use standard library (tomllib), minimal fields.
"""

import tomllib
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field


class CollectionMetadata(BaseModel):
    """
    Collection metadata from pyproject.toml.

    Parses standard [project] section + custom [tool.amplifier.collection] section.

    Convention over configuration: Minimal metadata, directory structure defines resources.
    """

    # From [project] section (standard Python packaging)
    name: str
    version: str
    description: str = ""

    # From [tool.amplifier.collection] section (our convention)
    author: str = ""
    capabilities: list[str] = Field(default_factory=list)
    requires: dict[str, str] = Field(default_factory=dict)

    # Optional URLs from [project.urls]
    homepage: str | None = None
    repository: str | None = None

    @classmethod
    def from_pyproject(cls, pyproject_path: Path) -> "CollectionMetadata":
        """
        Load collection metadata from pyproject.toml.

        Args:
            pyproject_path: Path to pyproject.toml file

        Returns:
            CollectionMetadata instance

        Raises:
            FileNotFoundError: If pyproject.toml doesn't exist
            KeyError: If required fields missing
            tomllib.TOMLDecodeError: If invalid TOML
        """
        if not pyproject_path.exists():
            raise FileNotFoundError(f"pyproject.toml not found: {pyproject_path}")

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        # Extract [project] section (required)
        project = data.get("project", {})
        if not project:
            raise KeyError(f"[project] section missing in {pyproject_path}")

        # Extract [tool.amplifier.collection] section (optional)
        collection = data.get("tool", {}).get("amplifier", {}).get("collection", {})

        # Extract [project.urls] section (optional)
        urls = project.get("urls", {})

        return cls(
            # Required from [project]
            name=project["name"],
            version=project["version"],
            description=project.get("description", ""),
            # Optional from [tool.amplifier.collection]
            author=collection.get("author", ""),
            capabilities=collection.get("capabilities", []),
            requires=collection.get("requires", {}),
            # Optional from [project.urls]
            homepage=urls.get("homepage"),
            repository=urls.get("repository"),
        )
