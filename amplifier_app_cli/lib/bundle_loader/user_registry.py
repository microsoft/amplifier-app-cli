"""User bundle registry - DEPRECATED.

This module is deprecated. User-added bundles are now stored in
~/.amplifier/settings.yaml under bundle.added, consolidating all
user configuration in one place.

Migration happens automatically when get_added_bundles() is called.

Use AppSettings instead:
    from amplifier_app_cli.lib.settings import AppSettings

    settings = AppSettings()
    settings.add_bundle(name, uri)           # Add bundle
    settings.remove_added_bundle(name)       # Remove bundle
    settings.get_added_bundles()             # Get all added bundles
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Legacy registry location (kept for migration)
REGISTRY_PATH = Path.home() / ".amplifier" / "bundle-registry.yaml"


def _emit_deprecation_warning(func_name: str) -> None:
    """Emit deprecation warning for legacy function usage."""
    warnings.warn(
        f"user_registry.{func_name}() is deprecated. "
        "Use AppSettings().add_bundle() or get_added_bundles() instead. "
        "User bundles are now stored in settings.yaml under bundle.added.",
        DeprecationWarning,
        stacklevel=3,
    )


def load_user_registry() -> dict[str, dict[str, Any]]:
    """Load user bundle registry - DEPRECATED.

    Returns data from settings.yaml bundle.added, converted to legacy format.
    """
    _emit_deprecation_warning("load_user_registry")

    from amplifier_app_cli.lib.settings import AppSettings

    app_settings = AppSettings()
    added_bundles = app_settings.get_added_bundles()

    # Convert to legacy format: {name: {"uri": uri, "added_at": None}}
    return {name: {"uri": uri, "added_at": None} for name, uri in added_bundles.items()}


def save_user_registry(bundles: dict[str, dict[str, Any]]) -> None:
    """Save user bundle registry - DEPRECATED.

    Writes to settings.yaml bundle.added instead of bundle-registry.yaml.
    """
    _emit_deprecation_warning("save_user_registry")

    from amplifier_app_cli.lib.settings import AppSettings

    app_settings = AppSettings()

    # Clear existing and add new bundles
    existing = app_settings.get_added_bundles()
    for name in list(existing.keys()):
        app_settings.remove_added_bundle(name)

    for name, info in bundles.items():
        uri = info.get("uri") if isinstance(info, dict) else None
        if uri:
            app_settings.add_bundle(name, uri)


def add_bundle(name: str, uri: str) -> None:
    """Add a bundle to the user registry - DEPRECATED.

    Use AppSettings().add_bundle(name, uri) instead.
    """
    _emit_deprecation_warning("add_bundle")

    from amplifier_app_cli.lib.settings import AppSettings

    AppSettings().add_bundle(name, uri)
    logger.debug(f"Added bundle '{name}' → {uri} to settings.yaml")


def remove_bundle(name: str) -> bool:
    """Remove a bundle from the user registry - DEPRECATED.

    Use AppSettings().remove_added_bundle(name) instead.
    """
    _emit_deprecation_warning("remove_bundle")

    from amplifier_app_cli.lib.settings import AppSettings

    result = AppSettings().remove_added_bundle(name)
    if result:
        logger.debug(f"Removed bundle '{name}' from settings.yaml")
    return result


def get_bundle(name: str) -> dict[str, Any] | None:
    """Get a bundle entry from the user registry - DEPRECATED.

    Use AppSettings().get_added_bundles().get(name) instead.
    """
    _emit_deprecation_warning("get_bundle")

    from amplifier_app_cli.lib.settings import AppSettings

    added = AppSettings().get_added_bundles()
    uri = added.get(name)
    if uri:
        return {"uri": uri, "added_at": None}
    return None


__all__ = [
    "REGISTRY_PATH",
    "add_bundle",
    "get_bundle",
    "load_user_registry",
    "remove_bundle",
    "save_user_registry",
]
