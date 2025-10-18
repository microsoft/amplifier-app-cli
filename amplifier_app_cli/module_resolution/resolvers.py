"""Module resolver implementations - app layer policy.

Concrete implementations of the ModuleSourceResolver protocol:
- StandardModuleSourceResolver: 6-layer fallback resolution
- EntryPointResolver: Simple entry point based resolution
"""

import logging
import os
from pathlib import Path

from amplifier_core.module_sources import ModuleNotFoundError
from amplifier_core.module_sources import ModuleSource

from .sources import FileSource
from .sources import GitSource
from .sources import PackageSource

try:
    import yaml
except ImportError:
    yaml = None  # Optional dependency

logger = logging.getLogger(__name__)


class StandardModuleSourceResolver:
    """Reference implementation with 6-layer fallback.

    Resolution order (first match wins):
    1. Environment variable (AMPLIFIER_MODULE_<ID>)
    2. Workspace convention (.amplifier/modules/<id>/)
    3. Project settings (.amplifier/settings.yaml sources section)
    4. User settings (~/.amplifier/settings.yaml sources section)
    5. Profile source (profile_hint)
    6. Installed package (amplifier-module-<id> or <id>)
    """

    def resolve(self, module_id: str, profile_hint=None) -> ModuleSource:
        """Resolve module through 6-layer fallback."""
        source, _layer = self.resolve_with_layer(module_id, profile_hint)
        return source

    def resolve_with_layer(self, module_id: str, profile_hint=None) -> tuple[ModuleSource, str]:
        """Resolve module and return which layer resolved it.

        Returns:
            Tuple of (ModuleSource, layer_name)
            layer_name is one of: env, workspace, project, user, profile, package
        """
        # Layer 1: Environment variable
        env_key = f"AMPLIFIER_MODULE_{module_id.upper().replace('-', '_')}"
        if env_value := os.getenv(env_key):
            logger.debug(f"[module:resolve] {module_id} -> env var ({env_value})")
            return (self._parse_source(env_value, module_id), "env")

        # Layer 2: Workspace convention
        if workspace_source := self._check_workspace(module_id):
            logger.debug(f"[module:resolve] {module_id} -> workspace")
            return (workspace_source, "workspace")

        # Layer 3: Project settings (.amplifier/settings.yaml)
        if project_source := self._read_yaml_source(Path(".amplifier/settings.yaml"), module_id):
            logger.debug(f"[module:resolve] {module_id} -> project settings")
            return (self._parse_source(project_source, module_id), "project")

        # Layer 4: User settings (~/.amplifier/settings.yaml)
        user_config = Path.home() / ".amplifier" / "settings.yaml"
        if user_source := self._read_yaml_source(user_config, module_id):
            logger.debug(f"[module:resolve] {module_id} -> user settings")
            return (self._parse_source(user_source, module_id), "user")

        # Layer 5: Profile source
        if profile_hint:
            logger.debug(f"[module:resolve] {module_id} -> profile")
            return (self._parse_source(profile_hint, module_id), "profile")

        # Layer 6: Installed package (fallback)
        logger.debug(f"[module:resolve] {module_id} -> package")
        return (self._resolve_package(module_id), "package")

    def _parse_source(self, source, module_id: str) -> ModuleSource:
        """Parse source (string URI or object) into ModuleSource.

        Args:
            source: String URI or dict (MCP-aligned object format)
            module_id: Module ID (for error messages)

        Returns:
            ModuleSource instance

        Raises:
            ValueError: Invalid source format
        """
        # Object format (MCP-aligned)
        if isinstance(source, dict):
            source_type = source.get("type")
            if source_type == "git":
                return GitSource(
                    url=source["url"], ref=source.get("ref", "main"), subdirectory=source.get("subdirectory")
                )
            if source_type == "file":
                return FileSource(source["path"])
            if source_type == "package":
                return PackageSource(source["name"])
            raise ValueError(f"Invalid source type '{source_type}' for module '{module_id}'")

        # String format
        source = str(source)

        if source.startswith("git+"):
            return GitSource.from_uri(source)
        if source.startswith("file://") or source.startswith("/") or source.startswith("."):
            return FileSource(source)
        # Assume package name
        return PackageSource(source)

    def _check_workspace(self, module_id: str) -> FileSource | None:
        """Check workspace convention for module.

        Args:
            module_id: Module identifier

        Returns:
            FileSource if found and valid, None otherwise
        """
        workspace_path = Path(".amplifier/modules") / module_id

        if not workspace_path.exists():
            return None

        # Check for empty submodule (has .git but no code)
        if self._is_empty_submodule(workspace_path):
            logger.debug(f"Module {module_id} workspace dir is empty submodule, skipping")
            return None

        # Check if valid module
        if not any(workspace_path.glob("**/*.py")):
            logger.warning(f"Module {module_id} in workspace but contains no Python files, skipping")
            return None

        return FileSource(workspace_path)

    def _is_empty_submodule(self, path: Path) -> bool:
        """Check if directory is uninitialized git submodule.

        Args:
            path: Directory to check

        Returns:
            True if empty submodule, False otherwise
        """
        # Has .git file (submodule marker) but no Python files
        git_file = path / ".git"
        return git_file.exists() and git_file.is_file() and not any(path.glob("**/*.py"))

    def _read_yaml_source(self, config_path: Path, module_id: str) -> str | dict | None:
        """Read module source from YAML config file.

        Args:
            config_path: Path to YAML config file
            module_id: Module identifier

        Returns:
            Source string/dict if found, None otherwise
        """
        if not config_path.exists():
            return None

        if yaml is None:
            logger.warning(f"PyYAML not installed, cannot read {config_path}")
            return None

        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)

            if not config or "sources" not in config:
                return None

            return config["sources"].get(module_id)

        except Exception as e:
            logger.warning(f"Failed to read {config_path}: {e}")
            return None

    def _resolve_package(self, module_id: str) -> PackageSource:
        """Resolve to installed package using fallback logic.

        Tries:
        1. Exact module ID as package name
        2. amplifier-module-<id> convention

        Args:
            module_id: Module identifier

        Returns:
            PackageSource

        Raises:
            ModuleNotFoundError: Neither package exists
        """
        import importlib.metadata

        # Try exact ID
        try:
            importlib.metadata.distribution(module_id)
            return PackageSource(module_id)
        except importlib.metadata.PackageNotFoundError:
            pass

        # Try convention
        convention_name = f"amplifier-module-{module_id}"
        try:
            importlib.metadata.distribution(convention_name)
            return PackageSource(convention_name)
        except importlib.metadata.PackageNotFoundError:
            pass

        # Both failed
        raise ModuleNotFoundError(
            f"Module '{module_id}' not found\n\n"
            f"Resolution attempted:\n"
            f"  1. Environment: AMPLIFIER_MODULE_{module_id.upper().replace('-', '_')} (not set)\n"
            f"  2. Workspace: .amplifier/modules/{module_id} (not found)\n"
            f"  3. Project: .amplifier/settings.yaml (no entry)\n"
            f"  4. User: ~/.amplifier/settings.yaml (no entry)\n"
            f"  5. Profile: (no source specified)\n"
            f"  6. Package: Tried '{module_id}' and '{convention_name}' (neither installed)\n\n"
            f"Suggestions:\n"
            f"  - Add source to profile: source: git+https://...\n"
            f"  - Add source override: amplifier source add {module_id} git+https://...\n"
            f"  - Install package: uv pip install <package-name>"
        )

    def __repr__(self) -> str:
        return "StandardModuleSourceResolver(6-layer)"


class EntryPointResolver:
    """Simple entry point based resolution for kernel fallback.

    Used when no custom resolver is mounted.
    """

    def resolve(self, module_id: str, profile_hint=None) -> ModuleSource:
        """Resolve using entry points.

        Args:
            module_id: Module identifier
            profile_hint: Ignored (not used in entry point resolution)

        Returns:
            PackageSource for module

        Raises:
            ModuleNotFoundError: Entry point not found
        """
        import importlib.metadata

        # Try to find entry point
        try:
            eps = importlib.metadata.entry_points(group="amplifier.modules")
            # Look for entry point with name = module_id
            for ep in eps:
                if ep.name == module_id:
                    # Return package source
                    if ep.dist is not None:
                        return PackageSource(ep.dist.name)
                    # If dist is None, it's a development entry point
                    raise ModuleNotFoundError(
                        f"Module '{module_id}' found but has no distribution metadata. "
                        f"This typically means it's loaded in development mode."
                    )

            # Not found
            raise ModuleNotFoundError(
                f"Module '{module_id}' not found in entry points. "
                f"Install with: uv pip install amplifier-module-{module_id}"
            )

        except Exception as e:
            raise ModuleNotFoundError(f"Entry point lookup failed for '{module_id}': {e}")

    def __repr__(self) -> str:
        return "EntryPointResolver(kernel-default)"
