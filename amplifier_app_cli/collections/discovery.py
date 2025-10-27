"""Collection resource discovery - APP LAYER POLICY.

Convention over configuration: Discovers resources based on directory structure.

Per KERNEL_PHILOSOPHY:
- "Could two teams want different behavior?" → YES (discovery rules are policy)
- This is APP LAYER - kernel doesn't know about collections

Per IMPLEMENTATION_PHILOSOPHY:
- Ruthless simplicity: Direct filesystem checks, no complex caching
- YAGNI: Only discover what exists now
"""

from pathlib import Path


class CollectionResources:
    """Discovered resources in a collection (APP LAYER POLICY)."""

    def __init__(
        self,
        profiles: list[Path],
        agents: list[Path],
        context: list[Path],
        scenario_tools: list[Path],
        modules: list[Path],
    ):
        """Initialize with discovered resources.

        Args:
            profiles: List of profile .md files
            agents: List of agent .md files
            context: List of context .md files
            scenario_tools: List of scenario tool directories
            modules: List of module directories
        """
        self.profiles = profiles
        self.agents = agents
        self.context = context
        self.scenario_tools = scenario_tools
        self.modules = modules

    def has_resources(self) -> bool:
        """Check if any resources were discovered."""
        return bool(self.profiles or self.agents or self.context or self.scenario_tools or self.modules)

    def __repr__(self) -> str:
        parts = []
        if self.profiles:
            parts.append(f"profiles={len(self.profiles)}")
        if self.agents:
            parts.append(f"agents={len(self.agents)}")
        if self.context:
            parts.append(f"context={len(self.context)}")
        if self.scenario_tools:
            parts.append(f"scenario_tools={len(self.scenario_tools)}")
        if self.modules:
            parts.append(f"modules={len(self.modules)}")
        return f"CollectionResources({', '.join(parts)})"


def discover_collection_resources(collection_path: Path) -> CollectionResources:
    """
    Discover resources in collection using convention over configuration.

    This is APP LAYER POLICY - different apps could use different conventions.

    Convention:
    - profiles/ directory → profile .md files
    - agents/ directory → agent .md files
    - context/ directory → context .md files (recursive)
    - scenario-tools/ directory → tool packages (subdirs with pyproject.toml)
    - modules/ directory → amplifier modules (subdirs with pyproject.toml)

    Args:
        collection_path: Path to collection directory

    Returns:
        CollectionResources with discovered items

    Example:
        >>> resources = discover_collection_resources(Path("~/.amplifier/collections/foundation"))
        >>> print(f"Found {len(resources.profiles)} profiles")
        >>> for profile in resources.profiles:
        ...     print(f"  {profile.name}")
    """
    # Discover profiles
    profiles = []
    profiles_dir = collection_path / "profiles"
    if profiles_dir.exists() and profiles_dir.is_dir():
        # Only .md files directly in profiles/ (not recursive)
        profiles = sorted([f for f in profiles_dir.glob("*.md") if f.is_file()])

    # Discover agents
    agents = []
    agents_dir = collection_path / "agents"
    if agents_dir.exists() and agents_dir.is_dir():
        # Only .md files directly in agents/ (not recursive)
        agents = sorted([f for f in agents_dir.glob("*.md") if f.is_file()])

    # Discover context files
    context = []
    context_dir = collection_path / "context"
    if context_dir.exists() and context_dir.is_dir():
        # Recursive for context (can be organized in subdirs)
        context = sorted([f for f in context_dir.glob("**/*.md") if f.is_file()])

    # Discover scenario tools
    scenario_tools = []
    scenario_tools_dir = collection_path / "scenario-tools"
    if scenario_tools_dir.exists() and scenario_tools_dir.is_dir():
        # Subdirectories with pyproject.toml are tools
        for subdir in scenario_tools_dir.iterdir():
            if subdir.is_dir() and (subdir / "pyproject.toml").exists():
                scenario_tools.append(subdir)
        scenario_tools.sort(key=lambda p: p.name)

    # Discover modules
    modules = []
    modules_dir = collection_path / "modules"
    if modules_dir.exists() and modules_dir.is_dir():
        # Subdirectories with pyproject.toml are modules
        for subdir in modules_dir.iterdir():
            if subdir.is_dir() and (subdir / "pyproject.toml").exists():
                modules.append(subdir)
        modules.sort(key=lambda p: p.name)

    return CollectionResources(
        profiles=profiles,
        agents=agents,
        context=context,
        scenario_tools=scenario_tools,
        modules=modules,
    )


def list_profiles(collection_path: Path) -> list[str]:
    """
    List profile names in collection (APP LAYER HELPER).

    Args:
        collection_path: Path to collection directory

    Returns:
        List of profile names (without .md extension)

    Example:
        >>> profiles = list_profiles(Path("~/.amplifier/collections/foundation"))
        >>> print(profiles)
        ['base', 'foundation', 'production', 'test']
    """
    resources = discover_collection_resources(collection_path)
    return [p.stem for p in resources.profiles]


def list_agents(collection_path: Path) -> list[str]:
    """
    List agent names in collection (APP LAYER HELPER).

    Args:
        collection_path: Path to collection directory

    Returns:
        List of agent names (without .md extension)

    Example:
        >>> agents = list_agents(Path("~/.amplifier/collections/developer-expertise"))
        >>> print(agents)
        ['bug-hunter', 'modular-builder', 'zen-architect']
    """
    resources = discover_collection_resources(collection_path)
    return [a.stem for a in resources.agents]
