"""App-layer module resolver with policy decisions.

This module implements the COMPOSITION PATTERN for module resolution:
- Foundation's BundleModuleResolver provides the MECHANISM (map IDs to paths)
- This module provides the POLICY (fallback strategy when modules aren't in bundle)

Per KERNEL_PHILOSOPHY.md: "Mechanism, not policy" - Foundation provides
capabilities, apps make decisions about how to use them.

Per AGENTS.md "Mechanism vs Policy" section: Apps wrap/compose foundation's
resolver rather than adding fallback parameters to foundation.
"""

from __future__ import annotations

import asyncio
import logging
import os
from importlib import metadata
from pathlib import Path
from typing import Any
from typing import Protocol
from typing import runtime_checkable

from amplifier_foundation.paths.resolution import get_amplifier_home
from amplifier_foundation.sources import SimpleSourceResolver

logger = logging.getLogger(__name__)


class ModuleResolutionError(Exception):
    """Error during module resolution."""

    pass


# =============================================================================
# Foundation-based Source classes (use new cache format)
# =============================================================================


class FoundationGitSource:
    """Git source that uses foundation's SimpleSourceResolver.

    Uses foundation's GitSourceHandler which creates new-format cache directories:
    {repo-name}-{hash}/ instead of the legacy {hash}/{ref}/ format.
    """

    def __init__(self, uri: str) -> None:
        """Initialize with git URI.

        Args:
            uri: Full git URI (e.g., git+https://github.com/org/repo@ref)
        """
        self.uri = uri
        self._cache_dir = get_amplifier_home() / "cache"
        self._resolver = SimpleSourceResolver(cache_dir=self._cache_dir)

    def resolve(self) -> Path:
        """Resolve to cached git repository path (sync wrapper).

        Uses foundation's async resolver wrapped in sync for kernel compatibility.
        Handles both sync and async contexts safely by running in a separate thread
        when called from within an async context.

        Returns:
            Path to cached module directory.

        Raises:
            ModuleResolutionError: Clone/resolution failed.
        """
        from concurrent.futures import ThreadPoolExecutor

        from amplifier_foundation.exceptions import BundleNotFoundError

        def _run_async():
            """Run the async resolver in a new event loop (in a separate thread)."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._resolver.resolve(self.uri))
            finally:
                loop.close()

        try:
            # Check if we're in an async context
            try:
                asyncio.get_running_loop()
                # We're in async context - run in thread pool to avoid nested loop error
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_run_async)
                    result = future.result()
            except RuntimeError:
                # No running loop - we can safely create one directly
                result = _run_async()

            return result.active_path
        except BundleNotFoundError as e:
            raise ModuleResolutionError(str(e)) from e

    def __repr__(self) -> str:
        return f"FoundationGitSource({self.uri})"


class FoundationFileSource:
    """Local filesystem path source."""

    def __init__(self, path: str | Path) -> None:
        """Initialize with file path.

        Args:
            path: Absolute or relative path to module directory
        """
        if isinstance(path, str):
            if path.startswith("file://"):
                path = path[7:]
            path = Path(path)
        self.path = path.resolve()

    def resolve(self) -> Path:
        """Resolve to filesystem path."""
        if not self.path.exists():
            raise ModuleResolutionError(f"Module path not found: {self.path}")
        if not self.path.is_dir():
            raise ModuleResolutionError(f"Module path is not a directory: {self.path}")
        if not any(self.path.glob("**/*.py")):
            raise ModuleResolutionError(
                f"Path does not contain a valid Python module: {self.path}"
            )
        return self.path

    def __repr__(self) -> str:
        return f"FoundationFileSource({self.path})"


class FoundationPackageSource:
    """Installed Python package source."""

    def __init__(self, package_name: str) -> None:
        """Initialize with package name.

        Args:
            package_name: Python package name
        """
        self.package_name = package_name

    def resolve(self) -> Path:
        """Resolve to installed package path.

        Returns:
            Path to installed package.

        Raises:
            ModuleResolutionError: Package not installed.
        """
        try:
            dist = metadata.distribution(self.package_name)
            if dist.files:
                package_files = [
                    f
                    for f in dist.files
                    if not any(
                        part.endswith((".dist-info", ".data")) for part in f.parts
                    )
                ]
                if package_files:
                    return Path(str(dist.locate_file(package_files[0]))).parent
                return Path(str(dist.locate_file(dist.files[0]))).parent
            return Path(str(dist.locate_file("")))
        except metadata.PackageNotFoundError:
            raise ModuleResolutionError(
                f"Package '{self.package_name}' not installed. Install with: uv pip install {self.package_name}"
            )

    def __repr__(self) -> str:
        return f"FoundationPackageSource({self.package_name})"


# =============================================================================
# Foundation-based Settings Resolver (replaces StandardModuleSourceResolver)
# =============================================================================


class SettingsProviderProtocol(Protocol):
    """Interface for settings access."""

    def get_module_sources(self) -> dict[str, str]:
        """Get module source overrides from settings."""
        ...


class FoundationSettingsResolver:
    """Settings-based resolver using foundation's source handling.

    Uses the same 6-layer resolution strategy as StandardModuleSourceResolver,
    but returns Source objects that use foundation's GitSourceHandler for
    git operations. This ensures the NEW cache format is used:
    {repo-name}-{hash}/ instead of legacy {hash}/{ref}/ format.

    Resolution order (first match wins):
    1. Environment variable (AMPLIFIER_MODULE_<ID>)
    2. Workspace convention (workspace_dir/<id>/)
    3. Settings provider (merges project + user settings)
    4. Legacy module pattern (removed)
    6. Installed package
    """

    def __init__(
        self,
        workspace_dir: Path | None = None,
        settings_provider: SettingsProviderProtocol | None = None,
    ) -> None:
        """Initialize resolver with optional configuration.

        Args:
            workspace_dir: Optional workspace directory path (layer 2)
            settings_provider: Optional settings provider (layer 3)
        """
        self.workspace_dir = workspace_dir
        self.settings_provider = settings_provider

    def resolve(
        self, module_id: str, source_hint: str | None = None
    ) -> FoundationGitSource | FoundationFileSource | FoundationPackageSource:
        """Resolve module through 6-layer fallback.

        Args:
            module_id: Module identifier (e.g., "provider-anthropic").
            source_hint: Optional source URI hint.

        Returns:
            Source object (FoundationGitSource, FoundationFileSource, or FoundationPackageSource).

        Raises:
            ModuleResolutionError: Module not found in any layer.
        """
        source, _layer = self.resolve_with_layer(module_id, source_hint)
        return source

    def resolve_with_layer(
        self, module_id: str, source_hint: str | None = None
    ) -> tuple[
        FoundationGitSource | FoundationFileSource | FoundationPackageSource, str
    ]:
        """Resolve module and return which layer resolved it.

        Args:
            module_id: Module identifier (e.g., "provider-anthropic").
            source_hint: Optional source URI hint.

        Returns:
            Tuple of (source, layer_name).
            layer_name is one of: env, workspace, settings, source_hint, package
        """
        # Layer 1: Environment variable
        env_key = f"AMPLIFIER_MODULE_{module_id.upper().replace('-', '_')}"
        if env_value := os.getenv(env_key):
            logger.debug(f"[module:resolve] {module_id} -> env var ({env_value})")
            return (self._parse_source(env_value, module_id), "env")

        # Layer 2: Workspace convention
        if self.workspace_dir and (
            workspace_source := self._check_workspace(module_id)
        ):
            logger.debug(f"[module:resolve] {module_id} -> workspace")
            return (workspace_source, "workspace")

        # Layer 3: Settings provider (collapsed project + user settings)
        if self.settings_provider:
            sources = self.settings_provider.get_module_sources()
            if module_id in sources:
                logger.debug(f"[module:resolve] {module_id} -> settings")
                return (self._parse_source(sources[module_id], module_id), "settings")

        # Layer 4: Source hint (from bundle config)
        if source_hint:
            logger.debug(f"[module:resolve] {module_id} -> source_hint")
            return (self._parse_source(source_hint, module_id), "source_hint")

        # Layer 6: Installed package (fallback)
        logger.debug(f"[module:resolve] {module_id} -> package")
        return (self._resolve_package(module_id), "package")

    def _parse_source(
        self, source: str | dict, module_id: str
    ) -> FoundationGitSource | FoundationFileSource | FoundationPackageSource:
        """Parse source URI into Source instance.

        Args:
            source: String URI or dict object
            module_id: Module ID (for error messages)

        Returns:
            Source instance using foundation handlers.
        """
        # Object format (MCP-aligned)
        if isinstance(source, dict):
            source_type = source.get("type")
            if source_type == "git":
                # Build URI from dict fields
                url = source["url"]
                ref = source.get("ref", "main")
                uri = f"git+{url}@{ref}"
                if subdir := source.get("subdirectory"):
                    uri += f"#subdirectory={subdir}"
                return FoundationGitSource(uri)
            if source_type == "file":
                return FoundationFileSource(source["path"])
            if source_type == "package":
                return FoundationPackageSource(source["name"])
            raise ValueError(
                f"Invalid source type '{source_type}' for module '{module_id}'"
            )

        # String format
        source = str(source)

        if source.startswith("git+"):
            return FoundationGitSource(source)
        if (
            source.startswith("file://")
            or source.startswith("/")
            or source.startswith(".")
        ):
            return FoundationFileSource(source)
        # Assume package name
        return FoundationPackageSource(source)

    def _check_workspace(self, module_id: str) -> FoundationFileSource | None:
        """Check workspace convention for module."""
        if not self.workspace_dir:
            return None

        workspace_path = self.workspace_dir / module_id
        if not workspace_path.exists():
            return None

        # Check for empty submodule
        if self._is_empty_submodule(workspace_path):
            logger.debug(
                f"Module {module_id} workspace dir is empty submodule, skipping"
            )
            return None

        # Check if valid module
        if not any(workspace_path.glob("**/*.py")):
            logger.warning(
                f"Module {module_id} in workspace but contains no Python files, skipping"
            )
            return None

        return FoundationFileSource(workspace_path)

    def _is_empty_submodule(self, path: Path) -> bool:
        """Check if directory is uninitialized git submodule."""
        git_file = path / ".git"
        return (
            git_file.exists() and git_file.is_file() and not any(path.glob("**/*.py"))
        )

    def _resolve_package(self, module_id: str) -> FoundationPackageSource:
        """Resolve to installed package using fallback logic."""
        # Try exact ID
        try:
            metadata.distribution(module_id)
            return FoundationPackageSource(module_id)
        except metadata.PackageNotFoundError:
            pass

        # Try convention
        convention_name = f"amplifier-module-{module_id}"
        try:
            metadata.distribution(convention_name)
            return FoundationPackageSource(convention_name)
        except metadata.PackageNotFoundError:
            pass

        # Both failed
        raise ModuleResolutionError(
            f"Module '{module_id}' not found\n\n"
            f"Resolution attempted:\n"
            f"  1. Environment: AMPLIFIER_MODULE_{module_id.upper().replace('-', '_')} (not set)\n"
            f"  2. Workspace: {self.workspace_dir / module_id if self.workspace_dir else 'N/A'} (not found)\n"
            f"  3. Settings: (no entry)\n"
            f"  4. Source hint: (no source specified)\n"
            f"  5. Package: Tried '{module_id}' and '{convention_name}' (neither installed)\n\n"
            f"Suggestions:\n"
            f"  - Add source to bundle: source: git+https://...\n"
            f"  - Add source override: amplifier source add {module_id} <source>\n"
            f"  - Install package: uv pip install <package-name>"
        )

    def get_module_source(self, module_id: str) -> str | None:
        """Get module source URI as string.

        Provides compatibility with StandardModuleSourceResolver interface.

        Args:
            module_id: Module identifier.

        Returns:
            String source URI, or None if not found.
        """
        # Check settings provider
        if self.settings_provider:
            sources = self.settings_provider.get_module_sources()
            if module_id in sources:
                return sources[module_id]

        return None

    def __repr__(self) -> str:
        return f"FoundationSettingsResolver(workspace={self.workspace_dir}, settings={self.settings_provider is not None})"


@runtime_checkable
class ModuleResolver(Protocol):
    """Protocol for module resolvers."""

    def resolve(self, module_id: str, hint: Any = None) -> Any:
        """Resolve module ID to source."""
        ...


class AppModuleResolver:
    """Composes bundle resolver with settings-based fallback.

    This is app-layer POLICY: when a module isn't in the bundle,
    try to resolve it from user settings (for provider-agnostic bundles).

    Use Case: A bundle like 'recipes' might not include a provider,
    allowing users to use their preferred provider from settings.
    The bundle includes tools/orchestrator/context, and the app-layer
    resolves the provider from user configuration.

    Example:
        # Foundation provides the mechanism
        prepared = await bundle.prepare()

        # App wraps with policy
        app_resolver = AppModuleResolver(
            bundle_resolver=prepared.resolver,
            settings_resolver=user_settings_resolver,
        )

        # Mount app resolver
        await session.coordinator.mount("module-source-resolver", app_resolver)
    """

    def __init__(
        self,
        bundle_resolver: Any,
        settings_resolver: Any | None = None,
    ) -> None:
        """Initialize with resolvers.

        Args:
            bundle_resolver: Foundation's BundleModuleResolver.
            settings_resolver: Optional resolver for fallback (e.g., from user settings).
                Should implement resolve(module_id, hint) method.
        """
        self._bundle = bundle_resolver
        self._settings = settings_resolver

    def resolve(self, module_id: str, source_hint: Any = None) -> Any:
        """Resolve module ID with fallback policy.

        Policy: Try bundle first, fall back to settings resolver.

        Args:
            module_id: Module identifier (e.g., "provider-anthropic").
            source_hint: Optional hint for resolution.

        Returns:
            Module source.

        Raises:
            ModuleNotFoundError: If module not found in bundle or settings.
        """
        # Try bundle first (primary source)
        try:
            return self._bundle.resolve(module_id, source_hint)
        except ModuleNotFoundError:
            pass  # Fall through to settings resolver

        # Try settings resolver (fallback)
        if self._settings is not None:
            try:
                result = self._settings.resolve(module_id, source_hint)
                logger.debug(f"Resolved '{module_id}' from settings fallback")
                return result
            except Exception as e:
                logger.debug(f"Settings fallback failed for '{module_id}': {e}")
                pass  # Fall through to error

        # Neither worked - raise informative error
        available = list(getattr(self._bundle, "_paths", {}).keys())
        raise ModuleNotFoundError(
            f"Module '{module_id}' not found in bundle or user settings. "
            f"Bundle contains: {available}. "
            f"Ensure the module is included in the bundle or configure a provider in settings."
        )

    def get_module_source(self, module_id: str) -> str | None:
        """Get module source path as string.

        Provides compatibility with StandardModuleSourceResolver interface.

        Args:
            module_id: Module identifier.

        Returns:
            String path to module, or None if not found.
        """
        # Check bundle first
        paths = getattr(self._bundle, "_paths", {})
        if module_id in paths:
            return str(paths[module_id])

        # Check settings resolver if available
        if self._settings is not None and hasattr(self._settings, "get_module_source"):
            return self._settings.get_module_source(module_id)

        return None
