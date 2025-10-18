"""Profile manager for tracking active profile state.

Delegates to SettingsManager for actual I/O.
"""

import logging

logger = logging.getLogger(__name__)


class ProfileManager:
    """Manages profile selection using settings.yaml files.

    Delegates to SettingsManager for file I/O.
    Provides profile-specific API on top of settings system.
    """

    def __init__(self, amplifier_dir=None):
        """Initialize profile manager with settings backend.

        Args:
            amplifier_dir: Base directory for settings files (for testing).
                          If None, uses .amplifier in current directory.
        """
        from ..settings import SettingsManager

        self.settings = SettingsManager(amplifier_dir=amplifier_dir if amplifier_dir else None)

    def get_active_profile(self) -> str | None:
        """Get the currently active profile name.

        Resolution order:
        1. Local settings (.amplifier/settings.local.yaml)
        2. Project default (.amplifier/settings.yaml)
        3. User settings (~/.amplifier/settings.yaml)

        Returns:
            Active profile name, or None if no profile is set
        """
        active = self.settings.get_active_profile()
        if active:
            return active

        # Fall back to project default
        default = self.settings.get_project_default()
        if default:
            return default

        return None

    def get_profile_source(self) -> tuple[str | None, str | None]:
        """Get the active profile and its source.

        Returns:
            Tuple of (profile_name, source) where source is:
            - "local" for settings.local.yaml
            - "default" for settings.yaml (project default)
            - "user" for ~/.amplifier/settings.yaml
            - None if no profile is active
        """
        # Check local first
        active = self.settings.get_active_profile()
        if active:
            return (active, "local")

        # Check project default
        default = self.settings.get_project_default()
        if default:
            return (default, "default")

        return (None, None)

    def set_active_profile(self, name: str) -> None:
        """Set the active profile for the current developer.

        Writes to .amplifier/settings.local.yaml

        Args:
            name: Profile name to activate locally
        """
        self.settings.set_active_profile(name)
        logger.info(f"Activated profile: {name}")

    def clear_active_profile(self) -> None:
        """Clear the local active profile.

        Removes active profile from settings.local.yaml.
        Falls back to project default or system default.
        """
        self.settings.clear_active_profile()
        logger.info("Cleared active profile")

    def get_project_default(self) -> str | None:
        """Get the project default profile name.

        Returns:
            Project default profile name, or None if not set
        """
        return self.settings.get_project_default()

    def set_project_default(self, name: str) -> None:
        """Set the project default profile.

        Writes to .amplifier/settings.yaml (should be committed to git).

        Args:
            name: Profile name to set as project default
        """
        self.settings.set_project_default(name)
        logger.info(f"Set project default: {name}")

    def clear_project_default(self) -> None:
        """Clear the project default profile.

        Removes default from settings.yaml.
        """
        self.settings.clear_project_default()
        logger.info("Cleared project default")
