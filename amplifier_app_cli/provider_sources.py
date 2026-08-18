"""Canonical sources for provider modules."""

import importlib
import importlib.metadata
import importlib.util
import logging
import site
import subprocess
import sys
from typing import TYPE_CHECKING

from rich.console import Console

from .utils.error_format import escape_markup

if TYPE_CHECKING:
    from amplifier_app_cli.lib.settings import AppSettings

logger = logging.getLogger(__name__)

# Single source of truth for known provider git URLs
DEFAULT_PROVIDER_SOURCES = {
    "provider-anthropic": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "provider-azure-openai": "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
    "provider-chat-completions": "git+https://github.com/microsoft/amplifier-module-provider-chat-completions@main",
    "provider-gemini": "git+https://github.com/microsoft/amplifier-module-provider-gemini@main",
    "provider-github-copilot": "git+https://github.com/microsoft/amplifier-module-provider-github-copilot@main",
    "provider-ollama": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
    "provider-openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "provider-vllm": "git+https://github.com/microsoft/amplifier-module-provider-vllm@main",
}

# Runtime dependencies between providers.
# Some providers extend others (e.g., Azure OpenAI extends OpenAI's provider class).
# These are runtime dependencies, NOT build dependencies, to avoid transitive
# dependency issues with editable installs during development.
# Format: {"dependent": ["dependency1", "dependency2", ...]}
PROVIDER_DEPENDENCIES: dict[str, list[str]] = {
    "provider-azure-openai": [
        "provider-openai"
    ],  # AzureOpenAIProvider extends OpenAIProvider
}


def _get_ordered_providers(sources: dict[str, str]) -> list[tuple[str, str]]:
    """Order providers so dependencies are installed first (topological sort).

    Ensures providers that depend on others are installed after their dependencies.
    For example, provider-azure-openai depends on provider-openai at runtime
    (AzureOpenAIProvider extends OpenAIProvider), so openai must be installed first.

    Args:
        sources: Dict mapping module_id to source URI

    Returns:
        List of (module_id, source_uri) tuples in dependency-respecting order
    """
    ordered: list[tuple[str, str]] = []
    remaining = set(sources.keys())

    while remaining:
        # Find providers whose dependencies are all satisfied (not in remaining)
        ready = [
            p
            for p in remaining
            if all(dep not in remaining for dep in PROVIDER_DEPENDENCIES.get(p, []))
        ]

        if not ready:
            # No providers ready - either circular dependency or dependency not in sources.
            # Fall back to taking any remaining provider to avoid infinite loop.
            ready = [sorted(remaining)[0]]
            logger.debug(
                f"Dependency ordering: no ready providers, falling back to {ready[0]}"
            )

        # Process ready providers in sorted order for determinism
        for provider in sorted(ready):
            ordered.append((provider, sources[provider]))
            remaining.remove(provider)

    return ordered


def get_effective_provider_sources(
    config_manager: "AppSettings | None" = None,
) -> dict[str, str]:
    """Get provider sources with settings modules and overrides applied.

    Merges, in ascending order of precedence:

    1. ``DEFAULT_PROVIDER_SOURCES`` (known providers, pinned to @main)
    2. ``sources.modules`` (``amplifier source add``)
    3. ``modules.providers[].source`` (``amplifier module add provider-X --source ...``)
    4. ``overrides.<module_id>.source`` (settings.yaml ``overrides`` block)
    5. ``config.providers[].source`` (``amplifier provider add/use --source ...``)

    Steps 2, 4 and 5 mirror the precedence that the runtime uses when it builds
    ``combined_sources`` in ``runtime/config.py``. Install time and run time MUST
    agree: if they disagree, Amplifier installs one build of a provider and then
    runs a different one, and any user pin is silently overwritten with @main.

    Args:
        config_manager: Optional config manager for source overrides and settings

    Returns:
        Dict mapping module_id to source URI
    """
    sources = dict(DEFAULT_PROVIDER_SOURCES)

    if config_manager:
        # 1. Apply source overrides for known providers (sources.modules)
        overrides = config_manager.get_module_sources()
        for module_id in list(sources.keys()):
            if module_id in overrides:
                sources[module_id] = overrides[module_id]
                logger.debug(
                    f"Using override source for {module_id}: {overrides[module_id]}"
                )

        # 2. Add user-added provider modules from settings
        # These are providers added via `amplifier module add provider-X --source ...`
        merged = config_manager.get_merged_settings()
        settings_providers = merged.get("modules", {}).get("providers", [])
        for provider in settings_providers:
            if isinstance(provider, dict):
                module_id = provider.get("module")
                source = provider.get("source")
                if module_id and source:
                    if module_id not in sources:
                        sources[module_id] = source
                        logger.debug(f"Added settings provider {module_id}: {source}")
                    elif sources[module_id] != source:
                        # Settings source overrides default (user's explicit choice)
                        sources[module_id] = source
                        logger.debug(f"Using settings source for {module_id}: {source}")

        # 3. Apply `overrides.<module_id>.source` from settings.yaml.
        # Higher precedence than sources.modules, matching the runtime.
        try:
            for module_id, source in config_manager.get_source_overrides().items():
                if module_id.startswith("provider-") or module_id in sources:
                    if sources.get(module_id) != source:
                        logger.debug(
                            f"Using settings override source for {module_id}: {source}"
                        )
                    sources[module_id] = source
        except Exception as e:  # pragma: no cover - defensive, settings are best-effort
            logger.debug(f"Could not read module source overrides: {e}")

        # 4. Apply `config.providers[].source` - written by `amplifier provider add`
        # and `amplifier provider use --source`. This is the most specific signal a
        # user can give about which build of a provider they want, so it wins.
        try:
            for provider in config_manager.get_provider_overrides():
                if not isinstance(provider, dict):
                    continue
                module_id = provider.get("module")
                source = provider.get("source")
                if module_id and source:
                    if sources.get(module_id) != source:
                        logger.debug(
                            f"Using configured provider source for {module_id}: {source}"
                        )
                    sources[module_id] = source
        except Exception as e:  # pragma: no cover - defensive, settings are best-effort
            logger.debug(f"Could not read configured provider sources: {e}")

    return sources


def is_provider_module_installed(provider_id: str) -> bool:
    """Check whether a provider module is installed and importable.

    Args:
        provider_id: Provider module ID (e.g., "provider-anthropic"). A bare
            name such as "anthropic" is normalized to "provider-anthropic".

    Returns:
        True if the module can be imported, False otherwise

    Note:
        A registered entry point is NOT sufficient evidence that a provider is
        usable. Providers are installed editable (``uv pip install -e <cache>``),
        so anything that deletes the module cache while leaving site-packages
        intact strands the ``.dist-info`` -- and therefore the entry point --
        pointing at a directory that no longer exists. ``amplifier reset --remove
        cache`` does exactly this when amplifier was not installed via ``uv
        tool``: ``_uninstall_amplifier()`` bails out early but
        ``_remove_amplifier_dir()`` still runs. Manual cache cleanup has the same
        effect. Such a provider still advertises itself but fails to import, so
        we require the entry point's module to actually resolve before treating
        the provider as installed -- otherwise the skip-if-installed check turns
        a repairable state into a permanent one.
    """
    module_id = (
        provider_id
        if provider_id.startswith("provider-")
        else f"provider-{provider_id}"
    )

    # Prefer the entry point for discovery (it is authoritative about which
    # module implements the provider), but confirm that module still resolves.
    try:
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        for ep in eps:
            if ep.name == module_id:
                try:
                    return importlib.util.find_spec(ep.module) is not None
                except (ImportError, AttributeError, ValueError):
                    # Parent package missing/broken -> treat as not installed.
                    return False
    except Exception:
        pass

    # Fall back to direct import check when no entry point is registered.
    try:
        provider_name = module_id.replace("provider-", "")
        module_name = f"amplifier_module_provider_{provider_name.replace('-', '_')}"
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def is_local_path(source_uri: str) -> bool:
    """Check if source URI is a local file path.

    Args:
        source_uri: Source URI string

    Returns:
        True if local path (starts with /, ./, ../, or file://)
    """
    return (
        source_uri.startswith("/")
        or source_uri.startswith("./")
        or source_uri.startswith("../")
        or source_uri.startswith("file://")
    )


def source_from_uri(source_uri: str):
    """Create appropriate source from URI (local path or git URL).

    Single source of truth for source type decision - use this instead of
    manually checking is_local_path() and creating FileSource/GitSource.

    Uses foundation-based source classes that create new-format cache directories:
    {repo-name}-{hash}/ instead of legacy {hash}/{ref}/ format.

    Args:
        source_uri: Source URI (git+https://... or local path like /path, ./path)

    Returns:
        FoundationFileSource for local paths, FoundationGitSource for git URLs
    """
    from amplifier_app_cli.lib.bundle_loader.resolvers import FoundationFileSource
    from amplifier_app_cli.lib.bundle_loader.resolvers import FoundationGitSource

    if is_local_path(source_uri):
        return FoundationFileSource(source_uri)
    return FoundationGitSource(source_uri)


def ensure_provider_installed(
    module_id: str,
    config_manager: "AppSettings | None" = None,
    console: Console | None = None,
) -> bool:
    """Ensure a single provider module is installed.

    This is a lightweight alternative to install_known_providers() that installs
    only the specified provider. Used for auto-fixing the post-update scenario
    where settings exist but the venv was wiped.

    Args:
        module_id: Provider module ID (e.g., "provider-anthropic")
        config_manager: Optional config manager for source overrides
        console: Optional Rich console for status messages

    Returns:
        True if provider was installed (or already available), False on failure
    """
    import importlib
    import importlib.metadata
    import site

    # Normalize module ID
    if not module_id.startswith("provider-"):
        module_id = f"provider-{module_id}"

    # Get source URI for this provider
    sources = get_effective_provider_sources(config_manager)
    source_uri = sources.get(module_id)

    if not source_uri:
        logger.warning(f"No source found for provider {module_id}")
        return False

    try:
        if console:
            console.print(f"[dim]Installing {module_id}...[/dim]", end="")

        # Resolve and install
        source = source_from_uri(source_uri)
        module_path = source.resolve()

        result = subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "-e",
                str(module_path),
                "--python",
                sys.executable,
                "--refresh",  # Force fresh fetch from git sources
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Install failed: {result.stderr}")

        # Refresh Python's view of installed packages
        importlib.invalidate_caches()
        for site_dir in site.getsitepackages():
            site.addsitedir(site_dir)
        if hasattr(importlib.metadata, "distributions"):
            list(importlib.metadata.distributions())

        if console:
            console.print(" [green]✓[/green]")

        logger.info(f"Successfully installed {module_id}")
        return True

    except Exception as e:
        logger.warning(f"Failed to install {module_id}: {e}")
        if console:
            console.print(" [red]✗[/red]")
        return False


def install_known_providers(
    config_manager: "AppSettings | None" = None,
    console: Console | None = None,
    verbose: bool = True,
    force: bool = False,
    failures_out: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Install known provider modules that are not already present.

    Downloads and caches known providers so they can be discovered
    via entry points for use in init and provider use commands.

    Uses source overrides from config_manager if available, otherwise
    falls back to DEFAULT_PROVIDER_SOURCES.

    Providers that are already installed are left untouched unless ``force``
    is set. Reinstalling an already-working provider is not a no-op: it
    replaces whatever build is present with the one this function resolves,
    which silently discards a user's pinned build.

    Supports both git URLs (git+https://...) and local file paths
    (./path, ../path, /absolute/path, file://path).

    Args:
        config_manager: Optional config manager for source overrides
        console: Optional Rich console for progress display
        verbose: Whether to show progress messages
        force: Reinstall providers even if they are already installed
        failures_out: Optional list to receive ``(module_id, reason)`` pairs for
            providers that failed to install. Purely additive -- the return
            value is unchanged, so existing callers need no update.

            Without this, the reason a provider failed to install is written to
            the log and then discarded. That matters downstream: when auto-init
            later reports "the module is not installed", the actual cause (a
            network failure, a bad source override, a broken build) is already
            gone, leaving the user to guess. Callers that intend to explain a
            missing provider can pass a list here and surface the real reason
            alongside the symptom.

    Returns:
        List of provider module IDs that are available after this call
        (newly installed plus already-present ones)
    """
    installed: list[str] = []
    failed: list[tuple[str, str]] = []

    # Get effective sources (with overrides applied)
    sources = get_effective_provider_sources(config_manager)

    # Order providers so dependencies are installed first
    # (e.g., provider-openai before provider-azure-openai)
    ordered_providers = _get_ordered_providers(sources)

    for module_id, source_uri in ordered_providers:
        # Leave already-installed providers alone. Overwriting them would
        # discard the build the user actually has in place.
        if not force and is_provider_module_installed(module_id):
            logger.debug(f"{module_id} already installed, skipping")
            if verbose and console:
                console.print(f"  [dim]{module_id} already installed, skipping[/dim]")
            installed.append(module_id)
            continue

        try:
            if verbose and console:
                console.print(f"  Installing {module_id}...", end="")

            # Use helper to create appropriate source type (DRY)
            source = source_from_uri(source_uri)

            # Resolve downloads to cache (for git) or validates path (for local)
            module_path = source.resolve()

            # Always install editable (-e) so that:
            # 1. Cache updates are immediately effective without reinstall
            # 2. Consistent behavior with foundation's ModuleActivator
            # 3. Dependencies are properly installed from the source location
            result = subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "-e",
                    str(module_path),
                    "--python",
                    sys.executable,
                ],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Failed to install: {result.stderr}")

            if verbose and console:
                suffix = " (local)" if is_local_path(source_uri) else ""
                console.print(f" [green]✓[/green]{suffix}")

            installed.append(module_id)

        except Exception as e:
            failed.append((module_id, str(e)))
            logger.warning(f"Failed to install {module_id}: {e}")

            if verbose and console:
                console.print(
                    f"[red]Failed to install {module_id}: {escape_markup(e)}[/red]"
                )

    if failed and verbose and console:
        console.print(
            f"\n[yellow]Warning: {len(failed)} provider(s) failed to install[/yellow]"
        )

    # Hand the failure reasons to a caller that asked for them. Everything
    # else about this function's contract is unchanged -- callers that don't
    # pass `failures_out` see identical behavior.
    if failures_out is not None:
        failures_out.extend(failed)

    # Refresh Python's view of installed packages so they're immediately importable.
    # Without this, the current Python process won't see packages installed via subprocess.
    # This must be thorough - just invalidate_caches() is not enough for subprocess installs.
    if installed:
        importlib.invalidate_caches()

        # Re-add site directories to ensure newly installed packages are found
        for site_dir in site.getsitepackages():
            site.addsitedir(site_dir)

        # Force refresh of importlib.metadata distributions cache
        if hasattr(importlib.metadata, "distributions"):
            list(importlib.metadata.distributions())

    return installed


__all__ = [
    "DEFAULT_PROVIDER_SOURCES",
    "ensure_provider_installed",
    "get_effective_provider_sources",
    "install_known_providers",
    "is_local_path",
    "source_from_uri",
]
