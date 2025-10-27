"""
Tests for collection resource discovery.

Per AGENTS.md: Test real behavior with real bundled collections.
"""

from pathlib import Path

from amplifier_app_cli.collections.discovery import discover_collection_resources
from amplifier_app_cli.collections.discovery import list_agents
from amplifier_app_cli.collections.discovery import list_profiles


def test_discover_foundation_collection():
    """Test discovering resources in foundation collection."""
    # Use REAL bundled collection
    package_dir = Path(__file__).parent.parent.parent / "amplifier_app_cli"
    collection_path = package_dir / "data" / "collections" / "foundation"

    resources = discover_collection_resources(collection_path)

    # Foundation should have profiles and context
    assert len(resources.profiles) > 0, "Foundation should have profiles"
    assert len(resources.context) > 0, "Foundation should have context files"

    # Foundation should NOT have agents
    assert len(resources.agents) == 0, "Foundation should not have agents"

    # Verify specific expected files exist
    profile_names = [p.stem for p in resources.profiles]
    assert "base" in profile_names
    assert "foundation" in profile_names


def test_discover_developer_expertise_collection():
    """Test discovering resources in developer-expertise collection."""
    # Use REAL bundled collection
    package_dir = Path(__file__).parent.parent.parent / "amplifier_app_cli"
    collection_path = package_dir / "data" / "collections" / "developer-expertise"

    resources = discover_collection_resources(collection_path)

    # Developer-expertise should have profiles and agents
    assert len(resources.profiles) > 0, "Developer-expertise should have profiles"
    assert len(resources.agents) > 0, "Developer-expertise should have agents"

    # Verify specific expected files exist
    profile_names = [p.stem for p in resources.profiles]
    assert "dev" in profile_names
    assert "full" in profile_names

    agent_names = [a.stem for a in resources.agents]
    assert "zen-architect" in agent_names


def test_discover_empty_collection(tmp_path):
    """Test discovering resources in empty collection."""
    # Create empty collection directory
    collection_path = tmp_path / "empty-collection"
    collection_path.mkdir()

    # Create minimal pyproject.toml
    (collection_path / "pyproject.toml").write_text('[project]\nname = "empty"\nversion = "1.0.0"\n')

    resources = discover_collection_resources(collection_path)

    # Should have no resources
    assert len(resources.profiles) == 0
    assert len(resources.agents) == 0
    assert len(resources.context) == 0
    assert len(resources.scenario_tools) == 0
    assert len(resources.modules) == 0
    assert not resources.has_resources()


def test_discover_profiles_only(tmp_path):
    """Test collection with only profiles."""
    collection_path = tmp_path / "profiles-only"
    collection_path.mkdir()

    # Create profiles directory with files
    profiles_dir = collection_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "dev.md").write_text("# Dev Profile")
    (profiles_dir / "prod.md").write_text("# Production Profile")

    resources = discover_collection_resources(collection_path)

    assert len(resources.profiles) == 2
    assert len(resources.agents) == 0
    assert resources.has_resources()

    profile_names = {p.stem for p in resources.profiles}
    assert profile_names == {"dev", "prod"}


def test_discover_context_recursive(tmp_path):
    """Test context discovery finds files in subdirectories."""
    collection_path = tmp_path / "context-test"
    collection_path.mkdir()

    # Create nested context structure
    context_dir = collection_path / "context"
    context_dir.mkdir()
    (context_dir / "top-level.md").write_text("# Top")

    shared_dir = context_dir / "shared"
    shared_dir.mkdir()
    (shared_dir / "shared.md").write_text("# Shared")

    deep_dir = shared_dir / "deep"
    deep_dir.mkdir()
    (deep_dir / "deep.md").write_text("# Deep")

    resources = discover_collection_resources(collection_path)

    # Should find all 3 files recursively
    assert len(resources.context) == 3

    # Verify paths
    names = {f.name for f in resources.context}
    assert names == {"top-level.md", "shared.md", "deep.md"}


def test_discover_scenario_tools(tmp_path):
    """Test scenario tools discovery."""
    collection_path = tmp_path / "tools-collection"
    collection_path.mkdir()

    # Create scenario-tools directory with valid tools
    tools_dir = collection_path / "scenario-tools"
    tools_dir.mkdir()

    # Valid tool 1
    tool1 = tools_dir / "analyzer"
    tool1.mkdir()
    (tool1 / "pyproject.toml").write_text('[project]\nname = "analyzer"\nversion = "1.0.0"\n')

    # Valid tool 2
    tool2 = tools_dir / "synthesizer"
    tool2.mkdir()
    (tool2 / "pyproject.toml").write_text('[project]\nname = "synthesizer"\nversion = "1.0.0"\n')

    # Invalid tool (no pyproject.toml)
    tool3 = tools_dir / "invalid"
    tool3.mkdir()

    resources = discover_collection_resources(collection_path)

    # Should find only valid tools
    assert len(resources.scenario_tools) == 2

    tool_names = {t.name for t in resources.scenario_tools}
    assert tool_names == {"analyzer", "synthesizer"}


def test_list_profiles_helper():
    """Test list_profiles helper function."""
    package_dir = Path(__file__).parent.parent.parent / "amplifier_app_cli"
    collection_path = package_dir / "data" / "collections" / "foundation"

    profiles = list_profiles(collection_path)

    assert len(profiles) > 0
    assert "base" in profiles
    assert "foundation" in profiles

    # Should be names without .md extension
    assert all(not name.endswith(".md") for name in profiles)


def test_list_agents_helper():
    """Test list_agents helper function."""
    package_dir = Path(__file__).parent.parent.parent / "amplifier_app_cli"
    collection_path = package_dir / "data" / "collections" / "developer-expertise"

    agents = list_agents(collection_path)

    assert len(agents) > 0
    assert "zen-architect" in agents

    # Should be names without .md extension
    assert all(not name.endswith(".md") for name in agents)


def test_discover_mixed_resources(tmp_path):
    """Test collection with multiple resource types."""
    collection_path = tmp_path / "mixed-collection"
    collection_path.mkdir()

    # Create all resource types
    (collection_path / "profiles").mkdir()
    (collection_path / "profiles" / "dev.md").write_text("# Dev")

    (collection_path / "agents").mkdir()
    (collection_path / "agents" / "helper.md").write_text("# Helper")

    (collection_path / "context").mkdir()
    (collection_path / "context" / "readme.md").write_text("# README")

    tools_dir = collection_path / "scenario-tools"
    tools_dir.mkdir()
    tool = tools_dir / "tool1"
    tool.mkdir()
    (tool / "pyproject.toml").write_text('[project]\nname = "tool1"\nversion = "1.0.0"\n')

    resources = discover_collection_resources(collection_path)

    assert len(resources.profiles) == 1
    assert len(resources.agents) == 1
    assert len(resources.context) == 1
    assert len(resources.scenario_tools) == 1
    assert resources.has_resources()
