"""User bundle registry for storing user-added bundles.

Stores user-added bundles in ~/.amplifier/bundle-registry.yaml
for discovery by AppBundleDiscovery.

Per IMPLEMENTATION_PHILOSOPHY: Minimal implementation - just YAML read/write.
Per KERNEL_PHILOSOPHY: This is app-layer policy (where to store user bundles).
"""

from __future__ import annotations

import logging
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Registry location (app-layer policy)
REGISTRY_PATH = Path.home() / ".amplifier" / "bundle-registry.yaml"


def load_user_registry() -> dict[str, dict[str, Any]]:
    """Load user bundle registry from disk.

    Returns:
        Dict mapping bundle names to info dicts with 'uri' and 'added_at' keys.
        Returns empty dict if registry doesn't exist.
    """
    if not REGISTRY_PATH.exists():
        return {}

    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("bundles", {})
    except Exception as e:
        logger.warning(f"Failed to load user registry: {e}")
        return {}


def save_user_registry(bundles: dict[str, dict[str, Any]]) -> None:
    """Save user bundle registry to disk.

    Args:
        bundles: Dict mapping bundle names to info dicts.
    """
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": 1,
        "bundles": bundles,
    }

    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def add_bundle(name: str, uri: str) -> None:
    """Add a bundle to the user registry.

    Args:
        name: Bundle name to register.
        uri: URI for the bundle (git+https://, file://, etc.).
    """
    bundles = load_user_registry()
    bundles[name] = {
        "uri": uri,
        "added_at": datetime.now(UTC).isoformat(),
    }
    save_user_registry(bundles)
    logger.debug(f"Added bundle '{name}' → {uri} to user registry")


def remove_bundle(name: str) -> bool:
    """Remove a bundle from the user registry.

    Args:
        name: Bundle name to remove.

    Returns:
        True if bundle was found and removed, False if not found.
    """
    bundles = load_user_registry()
    if name not in bundles:
        return False

    del bundles[name]
    save_user_registry(bundles)
    logger.debug(f"Removed bundle '{name}' from user registry")
    return True


def get_bundle(name: str) -> dict[str, Any] | None:
    """Get a bundle entry from the user registry.

    Args:
        name: Bundle name to look up.

    Returns:
        Info dict with 'uri' and 'added_at' keys, or None if not found.
    """
    bundles = load_user_registry()
    return bundles.get(name)


__all__ = [
    "REGISTRY_PATH",
    "add_bundle",
    "get_bundle",
    "load_user_registry",
    "remove_bundle",
    "save_user_registry",
]
