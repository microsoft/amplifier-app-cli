"""Profile manager for tracking active profile state."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ProfileManager:
    """
    Manages profile state for the current project.

    Supports two-tier profile system:
    - Local profile (.amplifier/profile) - Developer's choice, gitignored
    - Project default (.amplifier/default-profile) - Team default, checked in

    Precedence: Local → Project Default → None
    """

    def __init__(self, amplifier_dir: Path | None = None):
        """
        Initialize profile manager.

        Args:
            amplifier_dir: Path to .amplifier directory. If None, uses current directory.
        """
        if amplifier_dir is None:
            amplifier_dir = Path(".amplifier")

        self.amplifier_dir = amplifier_dir
        self.local_profile_file = amplifier_dir / "profile"
        self.default_profile_file = amplifier_dir / "default-profile"

    def get_active_profile(self) -> str | None:
        """
        Get the currently active profile name.

        Checks local profile first, then project default.

        Returns:
            Active profile name, or None if no profile is active
        """
        # Check local profile first
        local = self._read_profile_file(self.local_profile_file)
        if local:
            logger.debug(f"Active profile from local: {local}")
            return local

        # Fall back to project default
        default = self._read_profile_file(self.default_profile_file)
        if default:
            logger.debug(f"Active profile from project default: {default}")
            return default

        return None

    def get_profile_source(self) -> tuple[str | None, str | None]:
        """
        Get the active profile and its source.

        Returns:
            Tuple of (profile_name, source) where source is:
            - "local" for .amplifier/profile
            - "default" for .amplifier/default-profile
            - None if no profile is active
        """
        # Check local profile first
        local = self._read_profile_file(self.local_profile_file)
        if local:
            return (local, "local")

        # Check project default
        default = self._read_profile_file(self.default_profile_file)
        if default:
            return (default, "default")

        return (None, None)

    def set_active_profile(self, name: str) -> None:
        """
        Set the local active profile for the current developer.

        Creates .amplifier directory if it doesn't exist.

        Args:
            name: Profile name to activate locally
        """
        self._write_profile_file(self.local_profile_file, name)
        logger.info(f"Activated local profile: {name}")

    def clear_active_profile(self) -> None:
        """
        Clear the local active profile.

        This will cause the project default (if any) to be used.
        Removes the local profile file if it exists.
        """
        if self.local_profile_file.exists():
            self.local_profile_file.unlink()
            logger.info("Cleared local profile")
        else:
            logger.debug("No local profile to clear")

    def get_project_default(self) -> str | None:
        """
        Get the project default profile name.

        Returns:
            Project default profile name, or None if not set
        """
        return self._read_profile_file(self.default_profile_file)

    def set_project_default(self, name: str) -> None:
        """
        Set the project default profile.

        This file is intended to be checked into version control.
        Creates .amplifier directory if it doesn't exist.

        Args:
            name: Profile name to set as project default
        """
        self._write_profile_file(self.default_profile_file, name)
        logger.info(f"Set project default profile: {name}")

    def clear_project_default(self) -> None:
        """
        Clear the project default profile.

        Removes the default-profile file if it exists.
        """
        if self.default_profile_file.exists():
            self.default_profile_file.unlink()
            logger.info("Cleared project default profile")
        else:
            logger.debug("No project default to clear")

    def _read_profile_file(self, path: Path) -> str | None:
        """
        Read profile name from file.

        Args:
            path: Path to profile file

        Returns:
            Profile name, or None if file doesn't exist or is empty
        """
        if not path.exists():
            return None

        try:
            profile_name = path.read_text().strip()
            return profile_name if profile_name else None
        except Exception as e:
            logger.warning(f"Failed to read profile from {path}: {e}")
            return None

    def _write_profile_file(self, path: Path, name: str) -> None:
        """
        Write profile name to file atomically.

        Args:
            path: Path to profile file
            name: Profile name to write
        """
        # Ensure .amplifier directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write atomically: write to temp file, then rename
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(name + "\n")
        temp_path.replace(path)
