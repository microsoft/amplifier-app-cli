"""Module resolution implementation - app layer policy.

This package provides implementations of the kernel's module resolution protocols.
These are policy implementations that live at the app layer, not in the kernel.
"""

from .resolvers import EntryPointResolver
from .resolvers import StandardModuleSourceResolver
from .sources import FileSource
from .sources import GitSource
from .sources import PackageSource

__all__ = [
    "FileSource",
    "GitSource",
    "PackageSource",
    "StandardModuleSourceResolver",
    "EntryPointResolver",
]
