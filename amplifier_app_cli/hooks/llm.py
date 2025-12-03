"""LLM-powered hooks (placeholder).

This module requires pydantic-ai to be installed.
Install with: uv pip install pydantic-ai
"""

from __future__ import annotations

import logging
from typing import Any

from .models import HookConfig, HookResult

logger = logging.getLogger(__name__)


class LLMHookExecutor:
    """LLM-powered hook executor (not yet implemented)."""
    
    def __init__(self, config: HookConfig, model_name: str = "claude-3-5-haiku-20241022"):
        self.config = config
        self.model_name = model_name
        logger.warning(
            f"LLM hooks not yet fully implemented. "
            f"Hook {config.name} will always return continue."
        )
    
    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Execute LLM hook (placeholder)."""
        logger.warning(f"LLM hook {self.config.name} called but not implemented")
        return HookResult.continue_("LLM hooks not yet implemented")
