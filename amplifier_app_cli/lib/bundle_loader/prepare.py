"""Bundle preparation utilities for CLI app layer.

Bridges CLI discovery (search paths, packaged bundles) with foundation's
prepare workflow (load → compose → prepare → create_session).

This module enables the critical missing step: downloading and installing
modules from git sources before session creation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
) -> PreparedBundle:
    """Load bundle by name and prepare it for execution.

    Uses CLI discovery to find the bundle URI, then foundation's prepare()
    to download/install all modules from git sources.

    This is the CORRECT way to load bundles with remote modules:
    1. Discovery: bundle_name → URI (via CLI search paths)
    2. Load: URI → Bundle (handles file://, git+, http://, zip+)
    3. Prepare: Bundle → PreparedBundle (downloads modules, installs deps)

    Args:
        bundle_name: Bundle name to load (e.g., "foundation").
        discovery: CLI bundle discovery for name → URI resolution.
        install_deps: Whether to install Python dependencies for modules.

    Returns:
        PreparedBundle ready for create_session().

    Raises:
        FileNotFoundError: If bundle not found in any search path.
        RuntimeError: If preparation fails (download, install errors).

    Example:
        discovery = AppBundleDiscovery(search_paths=get_bundle_search_paths())
        prepared = await load_and_prepare_bundle("foundation", discovery)
        session = await prepared.create_session()
    """
    # 1. Discover bundle URI via CLI search paths
    uri = discovery.find(bundle_name)
    if not uri:
        available = discovery.list_bundles()
        raise FileNotFoundError(
            f"Bundle '{bundle_name}' not found. Available bundles: {', '.join(available) if available else 'none'}"
        )

    logger.info(f"Loading bundle '{bundle_name}' from {uri}")

    # 2. Load bundle via foundation (handles file://, git+, http://, zip+)
    bundle = await load_bundle(uri)
    logger.debug(f"Loaded bundle: {bundle.name} v{bundle.version}")

    # 3. Prepare: download modules from git sources, install deps
    logger.info(f"Preparing bundle '{bundle_name}' (install_deps={install_deps})")
    prepared = await bundle.prepare(install_deps=install_deps)
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
        bundle = await load_bundle(uri)
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
