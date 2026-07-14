"""Provider normalization and prepared-bundle synchronization policy."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..lib.merge_utils import merge_module_items

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle

logger = logging.getLogger(__name__)


def _sync_overrides_to_bundle(
    prepared: PreparedBundle,
    bundle_config: dict[str, Any],
    *,
    sync_tools: bool = False,
) -> None:
    """Sync mount-plan overrides to the bundle used for child composition."""
    bundle = getattr(prepared, "bundle", None)
    if bundle is None:
        return

    providers = bundle_config.get("providers")
    if providers and hasattr(bundle, "providers"):
        bundle.providers = list(providers)
        logger.debug(
            "Synced %d provider(s) from settings to bundle.providers: %s",
            len(providers),
            [p.get("module", "?") for p in providers],
        )

    if sync_tools:
        tools = bundle_config.get("tools")
        if tools and hasattr(bundle, "tools"):
            bundle.tools = list(tools)

    hooks = bundle_config.get("hooks")
    if hooks and hasattr(bundle, "hooks"):
        bundle.hooks = list(hooks)


def _ensure_raw_defaults(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure CLI-safe observability and transport defaults are present."""
    result = []
    for provider in providers:
        if isinstance(provider, dict):
            provider_copy = provider.copy()
            config = provider_copy.get("config", {})
            if isinstance(config, dict):
                config = config.copy()
                config.pop("debug", None)
                config.pop("raw_debug", None)
                if "raw" not in config:
                    config["raw"] = True
                if provider_copy.get("module") in {
                    "provider-openai",
                    "provider-azure-openai",
                }:
                    if "use_streaming" not in config:
                        config["use_streaming"] = False
                    model_name = str(
                        config.get("model") or config.get("default_model") or ""
                    )
                    if (
                        model_name.startswith("gpt-5.5")
                        and config.get("prompt_cache_retention") == "in_memory"
                    ):
                        config["prompt_cache_retention"] = "24h"
                provider_copy["config"] = config
            result.append(provider_copy)
        else:
            result.append(provider)
    return result


def map_provider_ids_to_instance_ids(
    providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map settings ``id`` fields to kernel ``instance_id`` fields."""
    result = []
    for provider in providers:
        if (
            isinstance(provider, dict)
            and "id" in provider
            and "instance_id" not in provider
        ):
            provider = {**provider, "instance_id": provider["id"]}
        result.append(provider)
    return result


def apply_provider_overrides(
    providers: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge provider overrides into matching provider instances."""
    if not overrides:
        return providers

    override_map = {}
    for override in overrides:
        if isinstance(override, dict) and "module" in override:
            key = override.get("id") or override["module"]
            override_map[key] = override

    result = []
    for provider in providers:
        if isinstance(provider, dict):
            key = provider.get("id") or provider.get("module", "")
            if key in override_map:
                result.append(merge_module_items(provider, override_map[key]))
            else:
                result.append(provider)
        else:
            result.append(provider)
    return result


def inject_user_providers(config: dict, prepared_bundle: PreparedBundle) -> None:
    """Inject user providers into a provider-agnostic bundle mount plan."""
    if "providers" in config and not prepared_bundle.mount_plan.get("providers"):
        prepared_bundle.mount_plan["providers"] = config["providers"]


__all__ = [
    "apply_provider_overrides",
    "inject_user_providers",
    "map_provider_ids_to_instance_ids",
]
