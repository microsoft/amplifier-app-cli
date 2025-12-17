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

import logging
from typing import Any
from typing import Protocol
from typing import runtime_checkable

logger = logging.getLogger(__name__)


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

    def resolve(self, module_id: str, hint: Any = None) -> Any:
        """Resolve module ID with fallback policy.

        Policy: Try bundle first, fall back to settings resolver.

        Args:
            module_id: Module identifier (e.g., "provider-anthropic").
            hint: Optional hint for resolution.

        Returns:
            Module source.

        Raises:
            ModuleNotFoundError: If module not found in bundle or settings.
        """
        # Try bundle first (primary source)
        try:
            return self._bundle.resolve(module_id, hint)
        except ModuleNotFoundError:
            pass  # Fall through to settings resolver

        # Try settings resolver (fallback)
        if self._settings is not None:
            try:
                result = self._settings.resolve(module_id, hint)
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
