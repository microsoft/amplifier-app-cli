"""Central settings management for Amplifier.

Philosophy: One settings file prevents proliferation, maintains simplicity.
"""

import logging
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path.home() / ".amplifier" / "settings.yaml"

DEFAULT_SETTINGS = {
    "updates": {
        "check_frequency_hours": 24,
        "auto_prompt": True,
        "last_check": None,
    }
}


def load_settings() -> dict:
    """Load settings from ~/.amplifier/settings.yaml.

    Creates file with defaults if it doesn't exist.
    """
    if not SETTINGS_FILE.exists():
        # Create directory if needed
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Write defaults
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(DEFAULT_SETTINGS, f, default_flow_style=False, sort_keys=False)

        return DEFAULT_SETTINGS.copy()

    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}

        # Ensure updates section exists (backwards compat)
        if "updates" not in settings:
            settings["updates"] = DEFAULT_SETTINGS["updates"].copy()

        return settings
    except Exception as e:
        logger.warning(f"Could not load settings from {SETTINGS_FILE}: {e}")
        return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict):
    """Save settings to ~/.amplifier/settings.yaml."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(settings, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.error(f"Could not save settings to {SETTINGS_FILE}: {e}")


def get_update_settings() -> dict:
    """Get just the updates section from settings."""
    settings = load_settings()
    return settings.get("updates", DEFAULT_SETTINGS["updates"].copy())


def save_update_last_check(timestamp: datetime):
    """Update last_check timestamp in settings."""
    settings = load_settings()
    settings["updates"]["last_check"] = timestamp.isoformat()
    save_settings(settings)
