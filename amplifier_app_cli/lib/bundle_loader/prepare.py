"""Bundle preparation utilities for CLI app layer.

Bridges CLI discovery (search paths, packaged bundles) with foundation's
prepare workflow (load → compose → prepare → create_session).

This module enables the critical missing step: downloading and installing
modules from git sources before session creation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Callable

from amplifier_foundation import Bundle
from amplifier_foundation import load_bundle
from amplifier_foundation.bundle import PreparedBundle

if TYPE_CHECKING:
    from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery

logger = logging.getLogger(__name__)


async def load_and_prepare_bundle(
    bundle_name: str,
    discovery: AppBundleDiscovery,
    install_deps: bool = True,
    compose_behaviors: list[str] | None = None,
    source_overrides: dict[str, str] | None = None,
) -> PreparedBundle:
    """Load bundle by name or URI and prepare it for execution.

    Uses CLI discovery to find the bundle URI, then foundation's prepare()
    to download/install all modules from git sources.

    This is the CORRECT way to load bundles with remote modules:
    1. Discovery: bundle_name → URI (via CLI search paths), or use URI directly
    2. Load: URI → Bundle (handles file://, git+, http://, zip+)
    3. Compose: Optionally compose additional behavior bundles
    4. Prepare: Bundle → PreparedBundle (downloads modules, installs deps)

    Args:
        bundle_name: Bundle name (e.g., "foundation") or URI (e.g., "git+https://...").
            If a URI is provided, it's used directly without discovery lookup.
        discovery: CLI bundle discovery for name → URI resolution.
        install_deps: Whether to install Python dependencies for modules.
        compose_behaviors: Optional list of behavior bundle URIs to compose
            onto the main bundle before preparation. These are typically
            app-level policy behaviors like notifications.
            Example: ["git+https://github.com/org/bundle@main#subdirectory=behaviors/foo.yaml"]
        source_overrides: Optional dict mapping module_id -> source_uri.
            Passed to bundle.prepare() to override module sources before download.
            This enables settings.yaml overrides to take effect at prepare time.

    Returns:
        PreparedBundle ready for create_session().

    Raises:
        FileNotFoundError: If bundle not found in any search path.
        RuntimeError: If preparation fails (download, install errors).

    Example:
        discovery = AppBundleDiscovery(search_paths=get_bundle_search_paths())
        prepared = await load_and_prepare_bundle("foundation", discovery)
        session = await prepared.create_session()

    Example with URI:
        prepared = await load_and_prepare_bundle(
            "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/amplifier-dev.yaml",
            discovery,
        )

    Example with behaviors:
        prepared = await load_and_prepare_bundle(
            "foundation",
            discovery,
            compose_behaviors=[
                "git+https://github.com/microsoft/amplifier-bundle-notify@main#subdirectory=behaviors/desktop-notifications.yaml"
            ],
        )

    Example with source overrides:
        prepared = await load_and_prepare_bundle(
            "foundation",
            discovery,
            source_overrides={"tool-task": "/local/path/to/module"},
        )
    """
    # Check if input looks like a URI rather than a bundle name
    # URI prefixes that indicate direct loading without discovery
    uri_prefixes = ("git+", "file://", "http://", "https://", "zip+")
    if bundle_name.startswith(uri_prefixes):
        logger.info(f"Input is URI, loading directly: {bundle_name}")
        uri = bundle_name
    else:
        # 1. Discover bundle URI via CLI search paths
        uri = discovery.find(bundle_name)
    if not uri:
        available = discovery.list_bundles()
        raise FileNotFoundError(
            f"Bundle '{bundle_name}' not found. Available bundles: {', '.join(available) if available else 'none'}"
        )

    logger.info(f"Loading bundle '{bundle_name}' from {uri}")

    # 2. Load bundle via foundation (handles file://, git+, http://, zip+)
    # CRITICAL: Pass discovery.registry so well-known bundles (foundation, etc.)
    # are available when resolving includes. Without this, includes fail because
    # load_bundle() creates an empty registry that doesn't know about well-known bundles.
    bundle = await load_bundle(uri, registry=discovery.registry)
    logger.debug(f"Loaded bundle: {bundle.name} v{bundle.version}")

    # 3. Compose additional behavior bundles (app-level policies like notifications)
    if compose_behaviors:
        for behavior_uri in compose_behaviors:
            logger.info(f"Composing behavior: {behavior_uri}")
            try:
                behavior_bundle = await load_bundle(
                    behavior_uri, registry=discovery.registry
                )
                bundle = bundle.compose(behavior_bundle)
                logger.debug(
                    f"Composed behavior '{behavior_bundle.name}' onto '{bundle.name}'"
                )
            except Exception as e:
                logger.warning(f"Failed to compose behavior '{behavior_uri}': {e}")
                # Continue without this behavior - notifications are optional

    # 4. Prepare: download modules from git sources, install deps
    # Build source resolver callback if overrides provided
    def make_source_resolver(
        overrides: dict[str, str],
    ) -> Callable[[str, str], str]:
        def resolver(module_id: str, original_source: str) -> str:
            resolved = overrides.get(module_id, original_source)
            if resolved != original_source:
                logger.info(f"Source override: {module_id} -> {resolved}")
            return resolved

        return resolver

    resolver_callback = (
        make_source_resolver(source_overrides) if source_overrides else None
    )

    logger.info(f"Preparing bundle '{bundle_name}' (install_deps={install_deps})")
    prepared = await bundle.prepare(
        install_deps=install_deps, source_resolver=resolver_callback
    )
    logger.info(f"Bundle '{bundle_name}' prepared successfully")

    return prepared


async def compose_and_prepare_bundles(
    bundle_names: list[str],
    discovery: AppBundleDiscovery,
    install_deps: bool = True,
) -> PreparedBundle:
    """Load multiple bundles, compose them, and prepare.

    Later bundles override earlier bundles (same precedence as foundation's
    end_to_end example).

    Use this when you need to layer bundles, e.g.:
    - Base "foundation" bundle with common tools
    - Provider-specific bundle on top

    Args:
        bundle_names: Bundle names in order (first = base, later = overlays).
        discovery: CLI bundle discovery for name → URI resolution.
        install_deps: Whether to install Python dependencies for modules.

    Returns:
        PreparedBundle from composed bundles.

    Raises:
        ValueError: If bundle_names is empty.
        FileNotFoundError: If any bundle not found.
        RuntimeError: If preparation fails.

    Example:
        prepared = await compose_and_prepare_bundles(
            ["foundation", "my-provider-bundle"],
            discovery,
        )
        session = await prepared.create_session()
    """
    if not bundle_names:
        raise ValueError("At least one bundle name required")

    bundles: list[Bundle] = []
    for name in bundle_names:
        uri = discovery.find(name)
        if not uri:
            raise FileNotFoundError(f"Bundle '{name}' not found")

        logger.info(f"Loading bundle '{name}' from {uri}")
        # Pass registry so well-known bundles are available for include resolution
        bundle = await load_bundle(uri, registry=discovery.registry)
        bundles.append(bundle)

    # Compose: first bundle is base, others overlay
    if len(bundles) == 1:
        composed = bundles[0]
        logger.debug("Single bundle, no composition needed")
    else:
        composed = bundles[0].compose(*bundles[1:])
        logger.info(f"Composed {len(bundles)} bundles")

    # Prepare the composed bundle
    logger.info(f"Preparing composed bundle (install_deps={install_deps})")
    prepared = await composed.prepare(install_deps=install_deps)
    logger.info("Composed bundle prepared successfully")

    return prepared


async def prepare_bundle_from_uri(
    uri: str,
    install_deps: bool = True,
) -> PreparedBundle:
    """Load and prepare a bundle directly from URI.

    Use this when you have a URI string and don't need CLI discovery.

    Args:
        uri: Bundle URI (file://, git+https://, https://, zip+).
        install_deps: Whether to install Python dependencies.

    Returns:
        PreparedBundle ready for create_session().

    Example:
        prepared = await prepare_bundle_from_uri(
            "git+https://github.com/org/my-bundle@main"
        )
        session = await prepared.create_session()
    """
    logger.info(f"Loading bundle from URI: {uri}")
    bundle = await load_bundle(uri)
    logger.debug(f"Loaded bundle: {bundle.name} v{bundle.version}")

    logger.info(f"Preparing bundle (install_deps={install_deps})")
    prepared = await bundle.prepare(install_deps=install_deps)
    logger.info("Bundle prepared successfully")

    return prepared


__all__ = [
    "load_and_prepare_bundle",
    "compose_and_prepare_bundles",
    "prepare_bundle_from_uri",
]
