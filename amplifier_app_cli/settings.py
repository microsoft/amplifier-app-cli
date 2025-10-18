"""Settings manager for unified settings.yaml files.

Manages three-scope settings system:
- User global (~/.amplifier/settings.yaml)
- Project (.amplifier/settings.yaml)
- Local (.amplifier/settings.local.yaml)
"""

import logging
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger(__name__)


class SettingsManager:
    """Manages settings across user/project/local scopes."""

    def __init__(self, amplifier_dir: Path | None = None):
        """Initialize settings manager with standard paths.

        Args:
            amplifier_dir: Base directory for project/local settings (for testing).
                          If None, uses .amplifier in current directory.
        """
        if amplifier_dir is None:
            amplifier_dir = Path(".amplifier")

        self.user_settings_file = Path.home() / ".amplifier" / "settings.yaml"
        self.project_settings_file = amplifier_dir / "settings.yaml"
        self.local_settings_file = amplifier_dir / "settings.local.yaml"

    def get_active_profile(self) -> str | None:
        """Get active profile from settings.

        Resolution order:
        1. Local settings (settings.local.yaml) - highest priority
        2. Project settings (settings.yaml)
        3. User settings (~/.amplifier/settings.yaml)
        4. None

        Returns:
            Active profile name or None
        """
        # Check local settings first
        local = self._read_settings(self.local_settings_file)
        if local and "profile" in local and "active" in local["profile"]:
            return local["profile"]["active"]

        # Check project settings
        project = self._read_settings(self.project_settings_file)
        if project and "profile" in project and "active" in project["profile"]:
            return project["profile"]["active"]

        # Check user settings
        user = self._read_settings(self.user_settings_file)
        if user and "profile" in user and "active" in user["active"]:
            return user["profile"]["active"]

        return None

    def set_active_profile(self, name: str) -> None:
        """Set active profile in local settings.

        Args:
            name: Profile name to activate
        """
        self._update_settings(self.local_settings_file, {"profile": {"active": name}})
        logger.info(f"Set active profile to: {name}")

    def clear_active_profile(self) -> None:
        """Clear active profile from local settings."""
        settings = self._read_settings(self.local_settings_file)
        if settings and "profile" in settings and "active" in settings["profile"]:
            del settings["profile"]["active"]
            # Clean up empty profile section
            if not settings["profile"]:
                del settings["profile"]
            self._write_settings(self.local_settings_file, settings)
            logger.info("Cleared active profile")

    def get_project_default(self) -> str | None:
        """Get project default profile.

        Returns:
            Project default profile name or None
        """
        project = self._read_settings(self.project_settings_file)
        if project and "profile" in project and "default" in project["profile"]:
            return project["profile"]["default"]
        return None

    def set_project_default(self, name: str) -> None:
        """Set project default profile.

        Args:
            name: Profile name to set as default
        """
        self._update_settings(self.project_settings_file, {"profile": {"default": name}})
        logger.info(f"Set project default profile to: {name}")

    def clear_project_default(self) -> None:
        """Clear project default profile."""
        settings = self._read_settings(self.project_settings_file)
        if settings and "profile" in settings and "default" in settings["profile"]:
            del settings["profile"]["default"]
            # Clean up empty profile section
            if not settings["profile"]:
                del settings["profile"]
            self._write_settings(self.project_settings_file, settings)
            logger.info("Cleared project default profile")

    def get_module_sources(self) -> dict[str, str]:
        """Get module source overrides merged from all settings.

        Returns:
            Dict of module_id -> source_uri
        """
        sources = {}

        # Start with user settings (lowest priority)
        user = self._read_settings(self.user_settings_file)
        if user and "sources" in user:
            sources.update(user["sources"])

        # Override with project settings
        project = self._read_settings(self.project_settings_file)
        if project and "sources" in project:
            sources.update(project["sources"])

        # Override with local settings (highest priority)
        local = self._read_settings(self.local_settings_file)
        if local and "sources" in local:
            sources.update(local["sources"])

        return sources

    def add_source_override(self, module_id: str, source: str, scope: str = "project") -> None:
        """Add module source override.

        Args:
            module_id: Module ID
            source: Source URI
            scope: "user", "project", or "local"
        """
        file_map = {
            "user": self.user_settings_file,
            "project": self.project_settings_file,
            "local": self.local_settings_file,
        }

        target_file = file_map.get(scope, self.project_settings_file)
        self._update_settings(target_file, {"sources": {module_id: source}})
        logger.info(f"Added {scope} source override for {module_id}: {source}")

    def remove_source_override(self, module_id: str, scope: str = "project") -> bool:
        """Remove module source override.

        Args:
            module_id: Module ID
            scope: "user", "project", or "local"

        Returns:
            True if removed, False if not found
        """
        file_map = {
            "user": self.user_settings_file,
            "project": self.project_settings_file,
            "local": self.local_settings_file,
        }

        target_file = file_map.get(scope, self.project_settings_file)
        settings = self._read_settings(target_file)

        if not settings or "sources" not in settings or module_id not in settings["sources"]:
            return False

        del settings["sources"][module_id]

        # Clean up empty sources section
        if not settings["sources"]:
            del settings["sources"]

        self._write_settings(target_file, settings)
        logger.info(f"Removed {scope} source override for {module_id}")
        return True

    def get_merged_settings(self) -> dict[str, Any]:
        """Get merged settings from all scopes.

        Merge order (later overrides earlier):
        1. User settings
        2. Project settings
        3. Local settings

        Returns:
            Merged settings dictionary
        """
        merged = {}

        # Start with user settings
        user = self._read_settings(self.user_settings_file)
        if user:
            merged = self._deep_merge(merged, user)

        # Merge project settings
        project = self._read_settings(self.project_settings_file)
        if project:
            merged = self._deep_merge(merged, project)

        # Merge local settings (highest priority)
        local = self._read_settings(self.local_settings_file)
        if local:
            merged = self._deep_merge(merged, local)

        return merged

    def _read_settings(self, path: Path) -> dict[str, Any] | None:
        """Read settings from YAML file.

        Args:
            path: Path to settings file

        Returns:
            Settings dict or None if file doesn't exist
        """
        if not yaml:
            logger.warning("PyYAML not available - cannot read settings files")
            return None

        if not path.exists():
            return None

        try:
            with open(path) as f:
                data = yaml.safe_load(f)
                return data if data else {}
        except Exception as e:
            logger.warning(f"Failed to read settings from {path}: {e}")
            return None

    def _write_settings(self, path: Path, settings: dict[str, Any]) -> None:
        """Write settings to YAML file.

        Args:
            path: Path to settings file
            settings: Settings dictionary
        """
        if not yaml:
            logger.error("PyYAML not available - cannot write settings files")
            return

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(path, "w") as f:
                yaml.dump(settings, f, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error(f"Failed to write settings to {path}: {e}")
            raise

    def _update_settings(self, path: Path, updates: dict[str, Any]) -> None:
        """Update settings file with new values (deep merge).

        Args:
            path: Path to settings file
            updates: Updates to merge
        """
        existing = self._read_settings(path) or {}
        merged = self._deep_merge(existing, updates)
        self._write_settings(path, merged)

    def _deep_merge(self, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        """Deep merge two dictionaries.

        Args:
            base: Base dictionary
            overlay: Overlay dictionary (takes precedence)

        Returns:
            Merged dictionary
        """
        result = base.copy()

        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result
