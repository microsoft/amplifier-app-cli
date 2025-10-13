"""Pydantic schemas for Amplifier profiles."""

from typing import Any

from pydantic import BaseModel
from pydantic import Field


class ProfileMetadata(BaseModel):
    """Profile metadata and identification."""

    name: str = Field(..., description="Unique profile identifier")
    version: str = Field(..., description="Semantic version (e.g., '1.0.0')")
    description: str = Field(..., description="Human-readable description")
    model: str | None = Field(None, description="Model in 'provider/model' format")
    extends: str | None = Field(None, description="Parent profile to inherit from")


class SessionConfig(BaseModel):
    """Core session configuration."""

    orchestrator: str = Field(..., description="Orchestrator module ID")
    context: str = Field(..., description="Context manager module ID")
    max_tokens: int | None = Field(None, description="Maximum tokens for context")
    compact_threshold: float | None = Field(None, description="Context compaction threshold (0.0-1.0)")
    auto_compact: bool | None = Field(None, description="Enable automatic compaction")


class ModuleConfig(BaseModel):
    """Configuration for a single module."""

    module: str = Field(..., description="Module ID to load")
    config: dict[str, Any] | None = Field(None, description="Module-specific configuration")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format for Mount Plan."""
        result: dict[str, Any] = {"module": self.module}
        if self.config is not None:
            result["config"] = self.config
        return result


class OrchestratorConfig(BaseModel):
    """Configuration for the orchestrator module."""

    config: dict[str, Any] = Field(default_factory=dict, description="Orchestrator-specific configuration")


class ContextConfig(BaseModel):
    """Context loading configuration."""

    files: list[str] = Field(default_factory=list, description="Context files/globs to load each turn")
    max_depth: int | None = Field(default=5, description="Maximum @mention recursion depth (None = unlimited)")


class AgentsConfig(BaseModel):
    """Agent registry configuration."""

    dirs: list[str] = Field(default_factory=list, description="Directories to search for agent .md files")


class TaskConfig(BaseModel):
    """Task tool configuration."""

    max_recursion_depth: int = Field(default=1, description="Maximum sub-agent recursion depth")


class LoggingConfig(BaseModel):
    """Logging configuration."""

    capture_model_io: bool = Field(default=False, description="Capture raw LLM requests/responses")
    redaction: list[str] = Field(
        default_factory=lambda: ["secrets", "pii-basic"], description="Redaction rules to apply"
    )


class UIConfig(BaseModel):
    """UI display configuration."""

    show_thinking_stream: bool = Field(default=True, description="Stream thinking deltas progressively")
    show_tool_lines: int = Field(default=5, description="Number of tool I/O lines to show in UI")


class Profile(BaseModel):
    """Complete profile specification."""

    profile: ProfileMetadata
    session: SessionConfig
    orchestrator: OrchestratorConfig | None = Field(None, description="Orchestrator configuration")
    context: ContextConfig | None = Field(None, description="Context loading configuration")
    agents_config: AgentsConfig | None = Field(None, description="Agent registry configuration")
    task: TaskConfig | None = Field(None, description="Task tool configuration")
    logging: LoggingConfig | None = Field(None, description="Logging configuration")
    ui: UIConfig | None = Field(None, description="UI display configuration")
    providers: list[ModuleConfig] = Field(default_factory=list)
    tools: list[ModuleConfig] = Field(default_factory=list)
    hooks: list[ModuleConfig] = Field(default_factory=list)
    agents: list[ModuleConfig] = Field(default_factory=list, description="Agent modules to load")

    def has_context_config(self) -> bool:
        """Check if profile has context-specific configuration."""
        return any(
            [
                self.session.max_tokens is not None,
                self.session.compact_threshold is not None,
                self.session.auto_compact is not None,
            ]
        )

    def get_context_config(self) -> dict[str, Any]:
        """Extract context configuration from session settings."""
        config = {}
        if self.session.max_tokens is not None:
            config["max_tokens"] = self.session.max_tokens
        if self.session.compact_threshold is not None:
            config["compact_threshold"] = self.session.compact_threshold
        if self.session.auto_compact is not None:
            config["auto_compact"] = self.session.auto_compact
        return config
