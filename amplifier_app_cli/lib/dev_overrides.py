"""Development overrides for local resource paths.

Philosophy: Simple key->path mapping at any scope. Most specific scope wins.
Only supports local paths (not remote) for development convenience.

This is PERMANENT functionality (not legacy) that works for both:
- Legacy codepath
- New bundles codepath

Usage:
    from amplifier_app_cli.lib.dev_overrides import resolve_dev_override

    # Check for module override
    override = resolve_dev_override("modules", "provider-anthropic")
    if override:
        return FileSource(str(override))

    # Check for bundle override
    override = resolve_dev_override("bundles", "foundation")
    if override:
        return load_bundle(f"file://{override}")

Configuration (settings.yaml / settings.local.yaml):
    overrides:
      modules:
        provider-anthropic: /home/user/repos/amplifier-module-provider-anthropic
      bundles:
        foundation: ../amplifier-foundation-bundle
      agents:
        explorer: ./my-agents/explorer.md
      context:
        common-agent-base: /home/user/repos/my-context/common.md
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import yaml

logger = logging.getLogger(__name__)

ResourceType = Literal["modules", "agents", "bundles", "context"]


def resolve_dev_override(resource_type: ResourceType, resource_id: str) -> Path | None:
    """Check for development override path.

    Resolution order (most specific wins):
    1. .amplifier/settings.local.yaml (machine-specific, gitignored)
    2. .amplifier/settings.yaml (project-level, shared)
    3. ~/.amplifier/settings.yaml (user defaults)

    Args:
        resource_type: "modules", "agents", "bundles", or "context"
        resource_id: The identifier of the resource

    Returns:
        Resolved local path if override exists and is valid, None otherwise.
    """
    # Scope paths (most specific first)
    scope_paths = [
        (Path.cwd() / ".amplifier" / "settings.local.yaml", Path.cwd() / ".amplifier"),
        (Path.cwd() / ".amplifier" / "settings.yaml", Path.cwd() / ".amplifier"),
        (Path.home() / ".amplifier" / "settings.yaml", Path.home() / ".amplifier"),
    ]

    for settings_file, scope_dir in scope_paths:
        if not settings_file.exists():
            continue

        try:
            with open(settings_file, encoding="utf-8") as f:
                settings = yaml.safe_load(f) or {}
        except Exception:
            continue  # Skip malformed files

        overrides = settings.get("overrides", {}).get(resource_type, {})
        if resource_id in overrides:
            path = Path(overrides[resource_id])
            # Resolve relative paths against scope directory
            if not path.is_absolute():
                path = (scope_dir / path).resolve()
            # Only return if path actually exists
            if path.exists():
                logger.debug(f"[dev_override] {resource_type}/{resource_id} -> {path}")
                return path
            logger.warning(f"[dev_override] {resource_type}/{resource_id} path not found: {path}")

    return None
