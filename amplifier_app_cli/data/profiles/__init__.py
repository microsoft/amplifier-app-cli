"""Bundled profiles package."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_system_default_profile() -> str:
    """Get system default profile from bundled configuration.

    This is the SINGLE source of truth for the system default profile.

    Returns:
        Name of the default profile
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not available, using hardcoded default")
        return "dev"

    defaults_file = Path(__file__).parent / "DEFAULTS.yaml"

    if defaults_file.exists():
        try:
            with open(defaults_file, encoding="utf-8") as f:
                defaults = yaml.safe_load(f)
                if defaults and "default_profile" in defaults:
                    return defaults["default_profile"]
        except Exception as e:
            logger.warning(f"Failed to read DEFAULTS.yaml: {e}")

    # Ultimate fallback if file is missing/corrupted
    return "dev"
