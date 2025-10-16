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


class ModuleConfig(BaseModel):
    """Configuration for a single module."""

    module: str = Field(..., description="Module ID to load")
    source: str | dict[str, Any] | None = Field(
        None, description="Module source (git URL, file path, or package name). String or object format."
    )
    config: dict[str, Any] | None = Field(None, description="Module-specific configuration")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format for Mount Plan."""
        result: dict[str, Any] = {"module": self.module}
        if self.source is not None:
            result["source"] = self.source
        if self.config is not None:
            result["config"] = self.config
        return result


class SessionConfig(BaseModel):
    """Core session configuration."""

    orchestrator: ModuleConfig = Field(..., description="Orchestrator module configuration")
    context: ModuleConfig = Field(..., description="Context module configuration")


class AgentsConfig(BaseModel):
    """Configuration for agent discovery and filtering."""

    dirs: list[str] | None = Field(None, description="Directories to search for agent .md files")
    include: list[str] | None = Field(None, description="Specific agents to include (filters discovered agents)")
    inline: dict[str, dict] | None = Field(None, description="Inline agent definitions (partial mount plans)")


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
    agents: AgentsConfig | None = Field(None, description="Agent discovery, filtering, and inline definitions")
    task: TaskConfig | None = Field(None, description="Task tool configuration")
    logging: LoggingConfig | None = Field(None, description="Logging configuration")
    ui: UIConfig | None = Field(None, description="UI display configuration")
    providers: list[ModuleConfig] = Field(default_factory=list)
    tools: list[ModuleConfig] = Field(default_factory=list)
    hooks: list[ModuleConfig] = Field(default_factory=list)
