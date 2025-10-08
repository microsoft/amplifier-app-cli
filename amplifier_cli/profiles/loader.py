"""Profile loader for discovering and loading profile files."""

import logging
from pathlib import Path

import tomli

from .schema import Profile

logger = logging.getLogger(__name__)


class ProfileLoader:
    """Discovers and loads Amplifier profiles from multiple search paths."""

    def __init__(self, search_paths: list[Path] | None = None):
        """
        Initialize profile loader.

        Args:
            search_paths: Optional list of paths to search. If None, uses default paths.
        """
        if search_paths is None:
            self.search_paths = self._get_default_search_paths()
        else:
            self.search_paths = search_paths

    def _get_default_search_paths(self) -> list[Path]:
        """Get default profile search paths in precedence order (lowest to highest)."""
        paths = []

        # Bundled profiles shipped with the package (lowest precedence)
        bundled = Path(__file__).parent.parent.parent / "profiles"
        if bundled.exists():
            paths.append(bundled)

        # Official profiles (second lowest precedence)
        official = Path("/usr/share/amplifier/profiles")
        if official.exists():
            paths.append(official)

        # Team profiles (middle precedence)
        team = Path(".amplifier/profiles")
        if team.exists():
            paths.append(team)

        # User profiles (highest precedence)
        user = Path.home() / ".amplifier" / "profiles"
        if user.exists():
            paths.append(user)

        return paths

    def list_profiles(self) -> list[str]:
        """
        Discover all available profile names.

        Returns:
            List of profile names (without .toml extension)
        """
        profiles = set()

        for search_path in self.search_paths:
            if not search_path.exists():
                continue

            for profile_file in search_path.glob("*.toml"):
                # Profile name is filename without extension
                profiles.add(profile_file.stem)

        return sorted(profiles)

    def find_profile_file(self, name: str) -> Path | None:
        """
        Find a profile file by name, checking paths in reverse order (highest precedence first).

        Args:
            name: Profile name (without .toml extension)

        Returns:
            Path to profile file if found, None otherwise
        """
        # Search in reverse order (highest precedence first)
        for search_path in reversed(self.search_paths):
            profile_file = search_path / f"{name}.toml"
            if profile_file.exists():
                return profile_file

        return None

    def load_profile(self, name: str) -> Profile:
        """
        Load a profile by name.

        Args:
            name: Profile name (without .toml extension)

        Returns:
            Loaded and validated Profile object

        Raises:
            FileNotFoundError: If profile not found
            ValueError: If profile is invalid
        """
        profile_file = self.find_profile_file(name)

        if profile_file is None:
            raise FileNotFoundError(
                f"Profile '{name}' not found in search paths: {self.search_paths}"
            )

        try:
            with open(profile_file, "rb") as f:
                data = tomli.load(f)

            profile = Profile(**data)
            logger.debug(f"Loaded profile '{name}' from {profile_file}")
            return profile

        except Exception as e:
            raise ValueError(f"Invalid profile file {profile_file}: {e}")

    def resolve_inheritance(self, profile: Profile) -> list[Profile]:
        """
        Resolve profile inheritance chain from base to child.

        Args:
            profile: Profile to resolve inheritance for

        Returns:
            List of profiles in inheritance order (base first, child last)

        Raises:
            ValueError: If circular inheritance detected or parent not found
        """
        chain = []
        seen = set()
        current = profile

        # Build inheritance chain (child to parent)
        while current is not None:
            # Check for circular inheritance
            if current.profile.name in seen:
                raise ValueError(
                    f"Circular inheritance detected: {current.profile.name} "
                    f"already in chain {list(seen)}"
                )

            seen.add(current.profile.name)
            chain.append(current)

            # Load parent if specified
            if current.profile.extends:
                try:
                    current = self.load_profile(current.profile.extends)
                except FileNotFoundError:
                    raise ValueError(
                        f"Parent profile '{current.profile.extends}' not found for '{current.profile.name}'"
                    )
            else:
                current = None

        # Reverse to get base-to-child order
        chain.reverse()
        return chain

    def load_overlays(self, base_name: str) -> list[Profile]:
        """
        Load profile overlays for a given base profile name.

        Overlays are additional profile files with the same name in different
        search paths. They are merged in order: official → team → user.

        Args:
            base_name: Base profile name to find overlays for

        Returns:
            List of overlay profiles in precedence order (lowest to highest)
        """
        overlays = []

        # Check each search path for overlays (in order: official, team, user)
        for search_path in self.search_paths:
            overlay_file = search_path / f"{base_name}.toml"
            if overlay_file.exists():
                try:
                    with open(overlay_file, "rb") as f:
                        data = tomli.load(f)
                    overlay = Profile(**data)
                    overlays.append(overlay)
                    logger.debug(f"Loaded overlay for '{base_name}' from {overlay_file}")
                except Exception as e:
                    logger.warning(f"Failed to load overlay from {overlay_file}: {e}")

        # If we only found one profile (the base itself), return empty list
        # Overlays are when the SAME name appears in multiple paths
        if len(overlays) <= 1:
            return []

        # Return all except the first one (which is the base)
        # Wait, no. We want all of them because we'll merge them.
        # Actually, the base is loaded separately. Overlays are ADDITIONAL files
        # with the same name in DIFFERENT paths.
        # So if we find the same name in official, team, and user paths,
        # we have the base (from highest precedence path) and overlays (from lower paths).

        # Actually, rethinking this: the way overlays work is:
        # 1. Load the base profile (from highest precedence path)
        # 2. Look for files with the same name in LOWER precedence paths
        # 3. Merge them in precedence order

        # So if "dev.toml" exists in:
        # - /usr/share/amplifier/profiles/dev.toml (official)
        # - .amplifier/profiles/dev.toml (team)
        # - ~/.amplifier/profiles/dev.toml (user)

        # The base is the USER one (highest precedence)
        # The overlays to merge are TEAM and OFFICIAL (in that order)

        # But that doesn't make sense either. Let me reconsider...

        # Actually, from the design doc:
        # "After resolving inheritance, the system looks for overlays:
        # 1. Official overlay
        # 2. Team overlay
        # 3. User overlay"

        # So the base profile has a NAME (e.g., "dev")
        # The system then looks for overlay files with that name in each search path
        # And merges them in order: official < team < user

        # So actually, if you activate "dev" profile:
        # 1. Load "dev" from the highest precedence path (that's the PRIMARY profile)
        # 2. Check if "dev" ALSO exists in lower precedence paths (those are overlays)
        # 3. Merge: primary <- team overlay <- user overlay

        # Wait, I think I'm overthinking this. Let me look at the design again:

        # From PROFILES.md:
        # "After resolving inheritance, the system looks for overlays:
        # 1. Official overlay: <profile-name>.toml in official profiles directory
        # 2. Team overlay: <profile-name>.toml in team profiles directory
        # 3. User overlay: <profile-name>.toml in user profiles directory
        # Each overlay is merged with increasing precedence"

        # So if you have a profile called "base", the system:
        # 1. Loads the primary "base" profile (from wherever find_profile_file finds it)
        # 2. Looks for overlays in ALL search paths
        # 3. Merges them: official -> team -> user

        # But find_profile_file returns the FIRST match in REVERSE order (highest precedence).
        # So if "base.toml" exists in all three paths, find_profile_file returns the USER one.

        # Then load_overlays should return the TEAM and OFFICIAL ones?

        # Actually, I think the intent is:
        # - There's ONE primary profile (e.g., "dev" in official profiles)
        # - You can OVERLAY it by creating "dev.toml" in team or user profiles
        # - These overlays add/override settings from the base

        # So the correct logic is:
        # 1. Find ALL files with the given name across all search paths
        # 2. Return them in precedence order for merging

        return overlays

    def get_profile_source(self, name: str) -> str | None:
        """
        Determine which source a profile comes from.

        Args:
            name: Profile name

        Returns:
            "official", "team", "user", or None if not found
        """
        profile_file = self.find_profile_file(name)
        if profile_file is None:
            return None

        if "/usr/share/amplifier/profiles" in str(profile_file):
            return "official"
        elif ".amplifier/profiles" in str(profile_file):
            return "team"
        elif str(Path.home()) in str(profile_file):
            return "user"

        return "unknown"
