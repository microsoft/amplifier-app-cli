"""Effective configuration summary utilities.

Extracts display-friendly information from resolved configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EffectiveConfigSummary:
    """Summary of effective configuration for display."""

    profile: str
    provider_name: str  # Friendly name (e.g., "Azure OpenAI")
    provider_module: str  # Module ID (e.g., "provider-azure-openai")
    model: str
    orchestrator: str
    tool_count: int
    hook_count: int

    def format_banner_line(self) -> str:
        """Format as single-line summary for banner display.

        Returns:
            Formatted string like "Profile: dev | Provider: Azure OpenAI | gpt-5-codex"
        """
        return f"Profile: {self.profile} | Provider: {self.provider_name} | {self.model}"


def get_effective_config_summary(
    config: dict[str, Any],
    profile_name: str = "default",
) -> EffectiveConfigSummary:
    """Extract effective configuration summary from resolved config.

    Args:
        config: Resolved mount plan configuration dict
        profile_name: Active profile name

    Returns:
        EffectiveConfigSummary with display-friendly information
    """
    # Extract provider info
    providers = config.get("providers", [])
    if providers and isinstance(providers[0], dict):
        first_provider = providers[0]
        provider_module = first_provider.get("module", "unknown")
        provider_config = first_provider.get("config", {})
        model = provider_config.get("default_model", "default")

        # Try to get friendly provider name
        provider_name = _get_provider_display_name(provider_module)
    else:
        provider_module = "none"
        provider_name = "None"
        model = "none"

    # Extract orchestrator
    session_config = config.get("session", {})
    orchestrator = session_config.get("orchestrator", "loop-basic")
    if isinstance(orchestrator, dict):
        orchestrator = orchestrator.get("module", "loop-basic")

    # Count tools and hooks
    tool_count = len(config.get("tools", []))
    hook_count = len(config.get("hooks", []))

    return EffectiveConfigSummary(
        profile=profile_name,
        provider_name=provider_name,
        provider_module=provider_module,
        model=model,
        orchestrator=orchestrator,
        tool_count=tool_count,
        hook_count=hook_count,
    )


def _get_provider_display_name(provider_module: str) -> str:
    """Get friendly display name for a provider module.

    Args:
        provider_module: Provider module ID (e.g., "provider-azure-openai")

    Returns:
        Friendly display name (e.g., "Azure OpenAI")
    """
    # Try to get from provider's get_info()
    try:
        from .provider_loader import get_provider_info

        info = get_provider_info(provider_module)
        if info and "display_name" in info:
            return info["display_name"]
    except Exception as e:
        logger.debug(f"Could not get provider info for {provider_module}: {e}")

    # Fallback: Convert module ID to friendly name
    # "provider-azure-openai" -> "Azure OpenAI"
    name = provider_module.replace("provider-", "")
    # Handle common cases
    name_map = {
        "anthropic": "Anthropic",
        "openai": "OpenAI",
        "azure-openai": "Azure OpenAI",
        "ollama": "Ollama",
        "vllm": "vLLM",
    }
    return name_map.get(name, name.replace("-", " ").title())


__all__ = ["EffectiveConfigSummary", "get_effective_config_summary"]
