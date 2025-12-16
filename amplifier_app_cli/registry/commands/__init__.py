"""Registry commands for module discovery."""

from .info import info_command
from .registry import registry_command
from .search import search_command

__all__ = ["registry_command", "search_command", "info_command"]
