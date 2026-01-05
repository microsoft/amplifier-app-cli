"""Provider loading utilities for configuration.

Provides lightweight provider loading for configuration commands
without requiring a full session/coordinator setup.
"""

import asyncio
import importlib
import importlib.metadata
import logging
import os
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from amplifier_core import ModelInfo  # pyright: ignore[reportAttributeAccessIssue]

    from amplifier_app_cli.lib.legacy import ConfigManager

logger = logging.getLogger(__name__)


def _get_provider_module_name(provider_id: str) -> str:
    """Convert provider ID to Python module name.

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic" or "anthropic")

    Returns:
        Python module name (e.g., "amplifier_module_provider_anthropic")
    """
    # Normalize provider ID
    if provider_id.startswith("provider-"):
        provider_id = provider_id[9:]

    return f"amplifier_module_provider_{provider_id.replace('-', '_')}"


def _load_provider_module(provider_id: str) -> Any:
    """Load a provider module.

    Tries entry points first, then direct import.

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic")

    Returns:
        Loaded Python module

    Raises:
        ImportError: If module cannot be loaded
    """
    # Normalize to full module ID
    module_id = provider_id if provider_id.startswith("provider-") else f"provider-{provider_id}"

    # Try entry point first
    try:
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        for ep in eps:
            if ep.name == module_id:
                # Entry point loads the mount function, get its module
                mount_fn = ep.load()
                return importlib.import_module(mount_fn.__module__.rsplit(".", 1)[0])
    except Exception as e:
        logger.debug(f"Entry point lookup failed for {module_id}: {e}")

    # Try direct import
    module_name = _get_provider_module_name(provider_id)
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"Could not load provider module '{provider_id}': {e}") from e


def load_provider_class(provider_id: str) -> type | None:
    """Load a provider class for configuration purposes.

    This is a lightweight load that doesn't require a full coordinator.
    Returns the provider class (e.g., AnthropicProvider) that can be
    instantiated to query get_info() and list_models().

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic" or "anthropic")

    Returns:
        Provider class if found, None otherwise
    """
    try:
        module = _load_provider_module(provider_id)

        # Look for provider class in module's __all__ or by convention
        # Convention: {Name}Provider (e.g., AnthropicProvider)
        provider_name = provider_id.replace("provider-", "") if provider_id.startswith("provider-") else provider_id
        class_name = f"{provider_name.title().replace('-', '')}Provider"

        # Try exact match first
        if hasattr(module, class_name):
            return getattr(module, class_name)

        # Try from __all__
        if hasattr(module, "__all__"):
            for name in module.__all__:
                if name.endswith("Provider"):
                    cls = getattr(module, name, None)
                    if cls and isinstance(cls, type):
                        return cls

        # Try any class ending in Provider
        for name in dir(module):
            if name.endswith("Provider") and not name.startswith("_"):
                cls = getattr(module, name, None)
                if cls and isinstance(cls, type):
                    return cls

        logger.warning(f"No provider class found in module for '{provider_id}'")
        return None

    except ImportError as e:
        logger.debug(f"Could not load provider class for '{provider_id}': {e}")
        return None


def get_provider_models(
    provider_id: str,
    config_manager: "ConfigManager | None" = None,
    collected_config: dict[str, Any] | None = None,
) -> list["ModelInfo"]:
    """Get available models for a provider.

    Loads the provider and queries list_models() to get dynamic model list.
    Re-raises authentication and connection errors so callers can show meaningful messages.
    Returns empty list only for non-critical failures (provider not found, no list_models method).

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic" or "anthropic")
        config_manager: Optional config manager (for future source resolution)
        collected_config: Optional config values collected from user (base_url, host, etc.)
            Used to instantiate provider with real connection values for dynamic model discovery.

    Returns:
        List of ModelInfo for available models, empty list if unavailable

    Raises:
        Exception: Re-raises authentication errors, API errors, and connection errors
            so callers can display meaningful error messages to users.
    """
    provider_class = load_provider_class(provider_id)
    if not provider_class:
        return []

    # Try different instantiation approaches for different provider signatures
    # Pass collected_config so providers can use real connection values
    provider = _try_instantiate_provider(provider_class, collected_config)
    if provider is None:
        logger.debug(f"Could not instantiate provider '{provider_id}' for model listing")
        return []

    # Check if provider has list_models
    if not hasattr(provider, "list_models"):
        logger.debug(f"Provider '{provider_id}' does not have list_models()")
        return []

    # Call list_models (may be sync or async)
    # Let exceptions propagate - auth errors, API errors, connection errors
    # should be shown to the user, not silently swallowed
    list_models_fn = provider.list_models
    if asyncio.iscoroutinefunction(list_models_fn):
        return asyncio.run(list_models_fn())
    return list_models_fn()


def _resolve_env_placeholder(value: str | None) -> str | None:
    """Resolve ${VAR} placeholders to actual environment values.

    Config values like "${VLLM_BASE_URL}" are placeholders for the final config file.
    For runtime use (like model discovery), we need the actual values.

    Args:
        value: Value that may contain ${VAR} placeholder

    Returns:
        Resolved value from environment, or original value if not a placeholder
    """
    if value and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var)
    return value


def _try_instantiate_provider(
    provider_class: type,
    collected_config: dict[str, Any] | None = None,
) -> Any | None:
    """Try to instantiate a provider class with various constructor signatures.

    Different providers have different constructor requirements:
    - Standard: (api_key, config) - Anthropic, OpenAI
    - Azure: (*, base_url, api_key, config) - Azure OpenAI
    - Ollama: (host, config)
    - VLLM: (base_url, *, config) - no api_key

    Args:
        provider_class: Provider class to instantiate
        collected_config: Optional config values collected from user (base_url, host, etc.)

    Returns:
        Provider instance or None if all attempts fail
    """
    collected_config = collected_config or {}

    # Extract connection values from collected config
    # Resolve ${VAR} placeholders to actual environment values
    raw_base_url = collected_config.get("base_url") or collected_config.get("azure_endpoint")
    raw_host = collected_config.get("host")
    raw_api_key = collected_config.get("api_key")

    base_url = _resolve_env_placeholder(raw_base_url) or "http://placeholder"
    host = _resolve_env_placeholder(raw_host) or "http://localhost:11434"
    api_key = _resolve_env_placeholder(raw_api_key) or ""

    # Approach 1: Standard (api_key, config) - Anthropic, OpenAI
    try:
        return provider_class(api_key=api_key, config={})
    except (TypeError, ValueError):
        pass

    # Approach 2: Azure-style (keyword-only base_url with api_key)
    try:
        return provider_class(base_url=base_url, api_key=api_key, config={})
    except (TypeError, ValueError):
        pass

    # Approach 3: VLLM-style (base_url without api_key)
    try:
        return provider_class(base_url=base_url, config={})
    except (TypeError, ValueError):
        pass

    # Approach 4: Ollama-style (host, config)
    try:
        return provider_class(host=host, config={})
    except (TypeError, ValueError):
        pass

    # Approach 5: Just config
    try:
        return provider_class(config={})
    except (TypeError, ValueError):
        pass

    # Approach 6: No args
    try:
        return provider_class()
    except (TypeError, ValueError):
        pass

    return None


def _cleanup_provider(provider: Any) -> None:
    """Clean up provider's internal httpx clients to prevent event loop warnings.

    Provider SDKs (like Anthropic, OpenAI) internally create httpx.AsyncClient instances.
    When providers are instantiated outside an async context (like during first-run wizard),
    these clients can cause "Event loop is closed" warnings when garbage collected.

    This function attempts to close these clients properly before the provider
    goes out of scope.

    Args:
        provider: Provider instance to clean up
    """
    import gc

    # Common SDK client attribute names used by various provider SDKs
    client_attrs = ["_client", "client", "_async_client", "_http_client"]

    for attr_name in client_attrs:
        client = getattr(provider, attr_name, None)
        if client is None:
            continue

        # Try to close the httpx client synchronously
        # httpx.Client (sync) has close()
        if hasattr(client, "close") and callable(client.close):
            try:
                client.close()
            except Exception:
                pass

        # For httpx.AsyncClient, we need to run aclose() in an event loop
        # Create a temporary loop just for cleanup to avoid the "Event loop is closed" error
        if hasattr(client, "aclose") and callable(client.aclose):
            try:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(client.aclose())
                finally:
                    loop.close()
            except Exception:
                # If async cleanup fails, try to close transport directly as fallback
                if hasattr(client, "_transport") and hasattr(client._transport, "close"):
                    try:
                        client._transport.close()
                    except Exception:
                        pass

    # Clear reference and force garbage collection while we're still in control
    del provider
    gc.collect()


def get_provider_info(provider_id: str) -> dict[str, Any] | None:
    """Get provider metadata.

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic" or "anthropic")

    Returns:
        Provider info dict if available, None otherwise
    """
    provider = None
    try:
        provider_class = load_provider_class(provider_id)
        if not provider_class:
            logger.debug(f"get_provider_info: load_provider_class returned None for '{provider_id}'")
            return None

        # Try different instantiation approaches for different provider signatures
        provider = _try_instantiate_provider(provider_class)
        if provider is None:
            logger.warning(f"Could not instantiate provider '{provider_id}' with any known signature")
            return None

        # Check if provider has get_info
        if not hasattr(provider, "get_info"):
            logger.debug(f"get_provider_info: provider '{provider_id}' has no get_info method")
            return None

        info = provider.get_info()
        return info.model_dump() if hasattr(info, "model_dump") else vars(info)

    except Exception as e:
        logger.warning(f"get_provider_info failed for '{provider_id}': {type(e).__name__}: {e}")
        return None

    finally:
        # Clean up provider's internal httpx clients to prevent "Event loop is closed" warnings
        # This is necessary because provider SDKs create httpx.AsyncClient internally,
        # and when instantiated outside an async context, cleanup fails on GC
        if provider is not None:
            _cleanup_provider(provider)


__all__ = ["load_provider_class", "get_provider_models", "get_provider_info"]
