"""Pydantic schemas for Amplifier agents."""

from typing import Any

from pydantic import BaseModel
from pydantic import Field

from .schema import ModuleConfig


class AgentMetadata(BaseModel):
    """Agent metadata and identification."""

    name: str = Field(..., description="Unique agent identifier")
    description: str = Field(..., description="Human-readable description of agent purpose")


class Agent(BaseModel):
    """
    Complete agent specification - partial mount plan.

    Agents are simpler than profiles:
    - No inheritance (no extends field)
    - No overlays across layers (first-match-wins resolution)
    - Just configuration overlays applied to parent sessions
    """

    meta: AgentMetadata = Field(..., description="Agent metadata")

    # Module lists - use same ModuleConfig as profiles
    providers: list[ModuleConfig] = Field(default_factory=list, description="Provider module overrides")
    tools: list[ModuleConfig] = Field(default_factory=list, description="Tool module overrides")
    hooks: list[ModuleConfig] = Field(default_factory=list, description="Hook module overrides")

    # Session config overrides
    session: dict[str, Any] | None = Field(None, description="Session configuration overrides")

    # System instruction
    system: dict[str, str] | None = Field(None, description="System instruction (instruction key)")

    def to_mount_plan_fragment(self) -> dict[str, Any]:
        """
        Convert agent to partial mount plan dict.

        Returns:
            Partial mount plan that can be merged with parent config
        """
        result: dict[str, Any] = {}

        # Include meta for structured access
        result["meta"] = self.meta.model_dump()

        # ALSO include name and description at top level for backward compatibility
        # (task tool and other components expect this format)
        result["name"] = self.meta.name
        result["description"] = self.meta.description

        # Add module lists if present
        if self.providers:
            result["providers"] = [p.to_dict() for p in self.providers]
        if self.tools:
            result["tools"] = [t.to_dict() for t in self.tools]
        if self.hooks:
            result["hooks"] = [h.to_dict() for h in self.hooks]

        # Add session overrides if present
        if self.session:
            result["session"] = self.session

        # Add system instruction if present
        if self.system:
            result["system"] = self.system

        return result
