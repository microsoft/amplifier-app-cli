"""Bundle loading utilities for CLI app layer.

Implements app-specific bundle discovery and loading policy.

This module bridges CLI-specific discovery (search paths, packaged bundles)
with foundation's bundle preparation workflow (load → compose → prepare → create_session).
"""

# Foundation imports (third-party) - sorted alphabetically
from amplifier_foundation import Bundle
from amplifier_foundation import BundleRegistry
from amplifier_foundation import load_bundle
from amplifier_foundation.bundle import BundleModuleResolver
from amplifier_foundation.bundle import PreparedBundle

# Local imports
from amplifier_app_cli.lib.bundle_loader import user_registry
from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery
from amplifier_app_cli.lib.bundle_loader.source_resolver import create_bundle_cache
from amplifier_app_cli.lib.bundle_loader.source_resolver import create_bundle_source_resolver
from amplifier_app_cli.lib.bundle_loader.source_resolver import get_bundle_cache_dir

__all__ = [
    # CLI-specific
    "AppBundleDiscovery",
    "create_bundle_cache",
    "create_bundle_source_resolver",
    "get_bundle_cache_dir",
    "user_registry",
    # Foundation re-exports
    "Bundle",
    "BundleModuleResolver",
    "BundleRegistry",
    "PreparedBundle",
    "load_bundle",
]
