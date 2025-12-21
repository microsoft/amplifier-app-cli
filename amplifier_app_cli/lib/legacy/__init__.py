"""LEGACY: Backward compatibility wrappers for amplifier-module-resolution.

DELETE WHEN: Profiles/collections removed in Phase 4.

This module provides API-compatible wrappers that maintain backward
compatibility with the deprecated amplifier-module-resolution library.
Once profiles and collections are fully removed, this entire module
can be deleted.

Usage:
    # BEFORE (deprecated)
    from amplifier_module_resolution import StandardModuleSourceResolver
    from amplifier_module_resolution.sources import FileSource, GitSource

    # AFTER (this module)
    from amplifier_app_cli.lib.legacy import StandardModuleSourceResolver
    from amplifier_app_cli.lib.legacy import FileSource, GitSource
"""

from .resolver import CollectionModuleProviderProtocol
from .resolver import SettingsProviderProtocol
from .resolver import StandardModuleSourceResolver
from .sources import FileSource
from .sources import GitSource
from .sources import InstallError
from .sources import ModuleResolutionError
from .sources import PackageSource

__all__ = [
    # Main resolver
    "StandardModuleSourceResolver",
    # Protocols
    "SettingsProviderProtocol",
    "CollectionModuleProviderProtocol",
    # Source types
    "FileSource",
    "GitSource",
    "PackageSource",
    # Exceptions
    "ModuleResolutionError",
    "InstallError",
]
