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

        Includes both direct agent directories and collection agents per
        COLLECTIONS_GUIDE search path precedence.

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

        # 2. Bundled collection agents
        bundled_collections = package_dir / "data" / "collections"
        if bundled_collections.exists():
            for collection_dir in bundled_collections.iterdir():
                if collection_dir.is_dir():
                    agents_dir = collection_dir / "agents"
                    if agents_dir.exists():
                        paths.append(agents_dir)
                        logger.debug(f"Found bundled collection agents: {agents_dir}")

        # 3. Project agents (middle precedence)
        project = Path(".amplifier/agents")
        if project.exists():
            paths.append(project)

        # 4. Project collection agents
        project_collections = Path(".amplifier/collections")
        if project_collections.exists():
            for collection_dir in project_collections.iterdir():
                if collection_dir.is_dir():
                    agents_dir = collection_dir / "agents"
                    if agents_dir.exists():
                        paths.append(agents_dir)
                        logger.debug(f"Found project collection agents: {agents_dir}")

        # 5. User agents (high precedence)
        user = Path.home() / ".amplifier" / "agents"
        if user.exists():
            paths.append(user)

        # 6. User collection agents (highest precedence)
        user_collections = Path.home() / ".amplifier" / "collections"
        if user_collections.exists():
            for collection_dir in user_collections.iterdir():
                if collection_dir.is_dir():
                    agents_dir = collection_dir / "agents"
                    if agents_dir.exists():
                        paths.append(agents_dir)
                        logger.debug(f"Found user collection agents: {agents_dir}")

        return paths

    def resolve(self, agent_name: str) -> Path | None:
        """
        Resolve agent file by name using first-match-wins.

        Supports multiple formats (APP LAYER POLICY per KERNEL_PHILOSOPHY):
        1. Collection with simple name: "developer-expertise:zen-architect" → searches collection/agents/zen-architect.md
        2. Collection with full path: "developer-expertise:agents/zen-architect.md" → uses exact path
        3. Simple name: "zen-architect" (searches local paths)

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
        # Collection syntax (developer-expertise:zen-architect or developer-expertise:agents/zen-architect.md)
        if ":" in agent_name:
            collection_name, agent_path = agent_name.split(":", 1)

            from ..collections import CollectionResolver

            collection_resolver = CollectionResolver()

            collection_path = collection_resolver.resolve(collection_name)
            if collection_path:
                # Try as full path first (developer-expertise:agents/zen-architect.md)
                full_path = collection_path / agent_path
                if full_path.exists() and full_path.is_file():
                    logger.debug(f"Resolved agent from collection: {agent_name}")
                    return full_path

                # Try as simple name (developer-expertise:zen-architect → agents/zen-architect.md)
                if not agent_path.startswith("agents/"):
                    # Add agents/ prefix and .md extension if needed
                    simple_name = agent_path if agent_path.endswith(".md") else f"{agent_path}.md"
                    full_path = collection_path / "agents" / simple_name
                    if full_path.exists() and full_path.is_file():
                        logger.debug(f"Resolved agent from collection (natural syntax): {agent_name}")
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

        Returns agent names with collection prefix when from collections:
        - Simple: "zen-architect", "bug-hunter"
        - Collection: "design-intelligence:art-director", "developer-expertise:zen-architect"

        Returns:
            List of agent names
        """
        agents = set()

        for search_path in self.search_paths:
            if not search_path.exists():
                continue

            for agent_file in search_path.glob("*.md"):
                # Skip README files
                if agent_file.stem.upper() == "README":
                    continue

                agent_name = agent_file.stem

                # Check if this agent is from a collection
                # Collection agents are in: .../collections/<collection-name>/agents/<agent>.md
                if "/collections/" in str(search_path):
                    # Extract collection name
                    parts = search_path.parts
                    try:
                        collections_idx = parts.index("collections")
                        collection_name = parts[collections_idx + 1]
                        # Use collection:agent format
                        agents.add(f"{collection_name}:{agent_name}")
                    except (ValueError, IndexError):
                        # Fallback to simple name if parsing fails
                        agents.add(agent_name)
                else:
                    # Simple agent (not from collection)
                    agents.add(agent_name)

        return sorted(agents)

    def get_agent_source(self, name: str) -> str | None:
        """
        Determine which source an agent comes from.

        Args:
            name: Agent name (simple or collection:agent format)

        Returns:
            "bundled", "bundled-collection", "project", "project-collection",
            "user", "user-collection", "env", or None if not found
        """
        # Check env var first
        env_key = f"AMPLIFIER_AGENT_{name.upper().replace('-', '_')}"
        if os.getenv(env_key):
            return "env"

        agent_file = self.resolve(name)
        if agent_file is None:
            return None

        path_str = str(agent_file)

        # Check for collections first (they have "/collections/" in path)
        if "/collections/" in path_str:
            # Bundled collection (check BEFORE home, since uv installs to ~/.local/share/uv)
            if "amplifier_app_cli" in path_str and "data/collections" in path_str:
                return "bundled"
            # Project collection (relative path, not under home)
            if ".amplifier/collections" in path_str and str(Path.home()) not in path_str:
                return "project-collection"
            # User collection (under home directory)
            if str(Path.home()) in path_str and ".amplifier/collections" in path_str:
                return "user-collection"
            return "collection"  # Unknown collection type

        # Non-collection agents
        # Check in precedence order (highest first)
        if str(Path.home()) in path_str and ".amplifier/agents" in path_str:
            return "user"
        if ".amplifier/agents" in path_str:
            return "project"
        # Bundled agents (shipped with package)
        if "amplifier_app_cli" in path_str:
            return "bundled"

        return "unknown"
