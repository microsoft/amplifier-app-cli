"""Module source registry for canonical module sources.

Scans bundled profiles to build a registry of canonical git sources for modules.
This allows setup and other commands to reference modules without hardcoding URLs.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ModuleSourceRegistry:
    """Registry of canonical module sources from bundled profiles."""

    def __init__(self):
        """Initialize registry."""
        self._cache: dict[str, str] | None = None

    def get_canonical_source(self, module_id: str) -> str:
        """Get canonical source URL for a module.

        Searches bundled profiles for first occurrence of this module
        and returns its source field.

        Args:
            module_id: Module ID (e.g., "provider-anthropic", "tool-bash")

        Returns:
            Canonical git URL

        Raises:
            ValueError: If module not found in bundled profiles
        """
        if self._cache is None:
            self._build_cache()

        # Cache is guaranteed to be dict after _build_cache()
        assert self._cache is not None

        if module_id not in self._cache:
            raise ValueError(
                f"Module '{module_id}' not found in bundled profiles.\n"
                f"Cannot determine canonical source.\n"
                f"This usually means the module is community-contributed or custom.\n"
                f"Available modules: {', '.join(sorted(self._cache.keys()))}"
            )

        return self._cache[module_id]

    def _build_cache(self) -> None:
        """Build cache of canonical sources from bundled profiles."""
        from ..profile_system import ProfileLoader

        self._cache = {}

        # Get bundled profiles directory
        bundled_dir = Path(__file__).parent.parent / "data" / "profiles"

        # Create loader with only bundled profiles
        loader = ProfileLoader(search_paths=[bundled_dir])

        for profile_name in loader.list_profiles():
            # Skip DEFAULTS.yaml
            if profile_name == "DEFAULTS":
                continue

            try:
                profile = loader.load_profile(profile_name)

                # Extract sources from all module lists
                for module_list_attr in ["providers", "tools", "hooks"]:
                    module_list = getattr(profile, module_list_attr, None)
                    if module_list:
                        for module_spec in module_list:
                            # Only cache if we haven't seen this module yet (first wins)
                            # Source can be string or dict, we only cache strings
                            if (
                                module_spec.source
                                and isinstance(module_spec.source, str)
                                and module_spec.module not in self._cache
                            ):
                                self._cache[module_spec.module] = module_spec.source

                # Handle orchestrator and context (from session)
                if profile.session:
                    if hasattr(profile.session, "orchestrator") and profile.session.orchestrator:
                        module_spec = profile.session.orchestrator
                        if (
                            hasattr(module_spec, "source")
                            and module_spec.source
                            and isinstance(module_spec.source, str)
                        ):
                            module_id = module_spec.module if hasattr(module_spec, "module") else None
                            if module_id and module_id not in self._cache:
                                self._cache[module_id] = module_spec.source

                    if hasattr(profile.session, "context") and profile.session.context:
                        module_spec = profile.session.context
                        if (
                            hasattr(module_spec, "source")
                            and module_spec.source
                            and isinstance(module_spec.source, str)
                        ):
                            module_id = module_spec.module if hasattr(module_spec, "module") else None
                            if module_id and module_id not in self._cache:
                                self._cache[module_id] = module_spec.source

            except Exception as e:
                logger.debug(f"Failed to load profile {profile_name} for registry: {e}")
                continue

        logger.debug(f"Built module source registry with {len(self._cache)} modules")


# Singleton instance
_registry = None


def get_canonical_module_source(module_id: str) -> str:
    """Get canonical source for a module.

    Args:
        module_id: Module ID

    Returns:
        Canonical git URL

    Raises:
        ValueError: If module not found
    """
    global _registry
    if _registry is None:
        _registry = ModuleSourceRegistry()
    return _registry.get_canonical_source(module_id)
