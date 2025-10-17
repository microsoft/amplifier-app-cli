"""Profile system for Amplifier.

Profiles are reusable configuration bundles that compile to Mount Plans.
Agents are specialized sub-session configurations.
"""

from .agent_loader import AgentLoader
from .agent_manager import AgentManager
from .agent_resolver import AgentResolver
from .agent_schema import Agent
from .agent_schema import AgentMetadata
from .compiler import compile_profile_to_mount_plan
from .loader import ProfileLoader
from .manager import ProfileManager
from .schema import ModuleConfig
from .schema import Profile
from .schema import ProfileMetadata
from .schema import SessionConfig
from .utils import parse_frontmatter
from .utils import parse_markdown_body

__all__ = [
    # Profile system
    "Profile",
    "ProfileMetadata",
    "SessionConfig",
    "ModuleConfig",
    "ProfileLoader",
    "ProfileManager",
    "compile_profile_to_mount_plan",
    # Agent system
    "Agent",
    "AgentMetadata",
    "AgentLoader",
    "AgentManager",
    "AgentResolver",
    # Shared utilities
    "parse_frontmatter",
    "parse_markdown_body",
]
