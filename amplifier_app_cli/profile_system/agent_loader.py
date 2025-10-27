"""Agent loader for discovering and loading agent files."""

import logging

from ..lib.mention_loading import MentionLoader
from ..utils.mentions import has_mentions
from .agent_resolver import AgentResolver
from .agent_schema import Agent
from .utils import parse_frontmatter
from .utils import parse_markdown_body

logger = logging.getLogger(__name__)


class AgentLoader:
    """Discovers and loads Amplifier agents from multiple search paths."""

    def __init__(self, resolver: AgentResolver | None = None):
        """
        Initialize agent loader.

        Args:
            resolver: Optional agent resolver. If None, creates default resolver.
        """
        if resolver is None:
            self.resolver = AgentResolver()
        else:
            self.resolver = resolver

    def list_agents(self) -> list[str]:
        """
        Discover all available agent names.

        Returns:
            List of agent names (without .md extension)
        """
        return self.resolver.list_agents()

    def load_agent(self, name: str) -> Agent:
        """
        Load an agent configuration from file.

        Args:
            name: Agent name (without .md extension)

        Returns:
            Loaded and validated agent

        Raises:
            FileNotFoundError: If agent not found
            ValueError: If agent file is invalid
        """
        agent_file = self.resolver.resolve(name)
        if agent_file is None:
            raise FileNotFoundError(f"Agent '{name}' not found in search paths")

        try:
            # Parse frontmatter
            data = parse_frontmatter(agent_file)

            # Parse markdown body
            markdown_body = parse_markdown_body(agent_file)

            # Process @mentions in markdown body (same as profiles)
            if markdown_body and has_mentions(markdown_body):
                logger.debug(f"Agent '{name}' has @mentions, loading context files...")
                mention_loader = MentionLoader()
                context_messages = mention_loader.load_mentions(markdown_body, relative_to=agent_file.parent)

                # Prepend loaded context to markdown body
                if context_messages:
                    # Extract string content from messages (handle both str and ContentBlock list)
                    context_parts = []
                    for msg in context_messages:
                        if isinstance(msg.content, str):
                            context_parts.append(msg.content)
                        elif isinstance(msg.content, list):
                            # ContentBlock list - extract text with explicit type narrowing
                            text_parts = []
                            for block in msg.content:
                                if hasattr(block, "text"):
                                    text_parts.append(block.text)  # type: ignore[attr-defined]
                                else:
                                    text_parts.append(str(block))
                            context_parts.append("".join(text_parts))
                        else:
                            context_parts.append(str(msg.content))

                    context_content = "\n\n".join(context_parts)
                    markdown_body = f"{context_content}\n\n{markdown_body}"
                    logger.debug(f"Expanded {len(context_messages)} @mentions for agent '{name}'")

            # Add markdown body as system instruction if present and not already defined
            if markdown_body:
                if "system" not in data:
                    data["system"] = {}
                if "instruction" not in data.get("system", {}):
                    data["system"]["instruction"] = markdown_body

            # Handle backward compatibility: old agents have name/description at top level
            # New agents have them under meta section
            if "meta" not in data:
                # Old format - migrate to new
                data["meta"] = {}
                if "name" in data:
                    data["meta"]["name"] = data.pop("name")
                else:
                    # Use filename as fallback
                    data["meta"]["name"] = name

                if "description" in data:
                    data["meta"]["description"] = data.pop("description")
                else:
                    data["meta"]["description"] = f"Agent: {name}"

            # Validate with Pydantic
            agent = Agent(**data)

            logger.debug(f"Loaded agent '{name}' from {agent_file}")
            return agent

        except Exception as e:
            raise ValueError(f"Invalid agent file {agent_file}: {e}")

    def get_agent_source(self, name: str) -> str | None:
        """
        Determine which source an agent comes from.

        Args:
            name: Agent name

        Returns:
            "bundled", "project", "user", "env", or None if not found
        """
        return self.resolver.get_agent_source(name)

    def load_agents_by_names(self, names: list[str]) -> dict[str, dict]:
        """
        Load multiple agents by name.

        Args:
            names: List of agent names to load

        Returns:
            Dict of {agent_name: mount_plan_fragment}
        """
        agents = {}

        for name in names:
            try:
                agent = self.load_agent(name)
                agents[name] = agent.to_mount_plan_fragment()
            except Exception as e:
                logger.warning(f"Failed to load agent '{name}': {e}")

        return agents
