"""Data models for mention loading."""

from pathlib import Path

from pydantic import BaseModel


class ContextFile(BaseModel):
    """Represents a loaded context file with its content and source paths.

    Attributes:
        content: The file content
        paths: All paths where this content was found (deduplicated by content hash)
        hash: Content hash (SHA-256) for deduplication
    """

    content: str
    paths: list[Path]
    hash: str
