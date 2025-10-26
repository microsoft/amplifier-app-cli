"""Agent file resolver for discovering agent files from multiple search locations."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class AgentResolver:
    """Resolves agent files from standard search locations using first-match-wins strategy."""

    def __init__(self, search_paths: list[Path] | None = None):
        """
        Initialize agent resolver.

        Args:
            search_paths: Optional list of paths to search. If None, uses default paths.
        """
        if search_paths is None:
            self.search_paths = self._get_default_search_paths()
        else:
            self.search_paths = search_paths

    def _get_default_search_paths(self) -> list[Path]:
        """
        Get default agent search paths in precedence order (lowest to highest).

        Returns:
            List of search paths
        """
        paths = []

        # 1. Bundled agents (lowest precedence)
        package_dir = Path(__file__).parent.parent  # amplifier_app_cli package
        bundled = package_dir / "data" / "agents"
        if bundled.exists():
            paths.append(bundled)
            logger.debug(f"Found bundled agents: {bundled}")

        # 2. Project agents (middle precedence)
        project = Path(".amplifier/agents")
        if project.exists():
            paths.append(project)

        # 3. User agents (highest precedence)
        user = Path.home() / ".amplifier" / "agents"
        if user.exists():
            paths.append(user)

        return paths

    def resolve(self, agent_name: str) -> Path | None:
        """
        Resolve agent file by name using first-match-wins.

        Supports two formats (APP LAYER POLICY per KERNEL_PHILOSOPHY):
        1. Collection syntax: "collection:agents/name.md" (e.g., "developer-expertise:agents/zen-architect.md")
        2. Simple name: "zen-architect" (searches local paths)

        Resolution order for simple names (highest priority first):
        1. Environment variable AMPLIFIER_AGENT_<NAME>
        2. User agents (~/.amplifier/agents/)
        3. Project agents (.amplifier/agents/)
        4. Bundled agents (package data)

        Args:
            agent_name: Agent name (simple or collection:path format)

        Returns:
            Path to agent file if found, None otherwise
        """
        # NEW: Collection syntax (developer-expertise:agents/zen-architect.md)
        if ":" in agent_name:
            collection_name, agent_path = agent_name.split(":", 1)

            from ..collections import CollectionResolver
            collection_resolver = CollectionResolver()

            collection_path = collection_resolver.resolve(collection_name)
            if collection_path:
                full_path = collection_path / agent_path
                if full_path.exists() and full_path.is_file():
                    logger.debug(f"Resolved agent from collection: {agent_name}")
                    return full_path

            # Collection or resource not found
            logger.debug(f"Collection agent not found: {agent_name}")
            return None

        # EXISTING: Simple name resolution
        # 0. Check environment variable (absolute highest priority)
        env_key = f"AMPLIFIER_AGENT_{agent_name.upper().replace('-', '_')}"
        if env_path := os.getenv(env_key):
            path = Path(env_path)
            if path.exists():
                logger.debug(f"Resolved agent '{agent_name}' from env var: {path}")
                return path
            logger.warning(f"Env var {env_key} set but path doesn't exist: {env_path}")

        # 1-3. Search standard paths (reverse order = highest priority first)
        for search_path in reversed(self.search_paths):
            agent_file = search_path / f"{agent_name}.md"
            if agent_file.exists():
                logger.debug(f"Resolved agent '{agent_name}' from: {search_path}")
                return agent_file

        return None

    def list_agents(self) -> list[str]:
        """
        Discover all available agent names from all search paths.

        Returns:
            List of agent names (without .md extension)
        """
        agents = set()

        for search_path in self.search_paths:
            if not search_path.exists():
                continue

            for agent_file in search_path.glob("*.md"):
                # Skip README files
                if agent_file.stem.upper() == "README":
                    continue
                # Agent name is filename without extension
                agents.add(agent_file.stem)

        return sorted(agents)

    def get_agent_source(self, name: str) -> str | None:
        """
        Determine which source an agent comes from.

        Args:
            name: Agent name

        Returns:
            "bundled", "project", "user", "env", or None if not found
        """
        # Check env var first
        env_key = f"AMPLIFIER_AGENT_{name.upper().replace('-', '_')}"
        if os.getenv(env_key):
            return "env"

        agent_file = self.resolve(name)
        if agent_file is None:
            return None

        path_str = str(agent_file)

        # Check in precedence order
        if str(Path.home()) in path_str and ".amplifier/agents" in path_str:
            return "user"
        if ".amplifier/agents" in path_str:
            return "project"
        # Bundled agents (shipped with package)
        if "amplifier_app_cli" in path_str:
            return "bundled"

        return "unknown"
