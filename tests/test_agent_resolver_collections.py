"""
Tests for AgentResolver with collections support.

Tests collection:agents/name.md syntax.
Per AGENTS.md: Test real behavior with real bundled collections.
"""

from amplifier_app_cli.profile_system.agent_resolver import AgentResolver


def test_resolve_agent_with_collection_syntax():
    """
    Test resolving agent using collection syntax.

    Uses REAL developer-expertise collection: developer-expertise:agents/zen-architect.md
    """
    resolver = AgentResolver()

    # Resolve agent using collection syntax
    path = resolver.resolve("developer-expertise:agents/zen-architect.md")
    assert path is not None
    assert path.name == "zen-architect.md"
    assert path.exists()


def test_resolve_agent_collection_not_found():
    """Test resolving agent when collection doesn't exist."""
    resolver = AgentResolver()

    path = resolver.resolve("nonexistent:agents/some-agent.md")
    assert path is None


def test_resolve_agent_resource_not_found():
    """Test resolving agent when collection exists but agent doesn't."""
    resolver = AgentResolver()

    path = resolver.resolve("developer-expertise:agents/nonexistent.md")
    assert path is None


def test_resolve_agent_natural_collection_syntax():
    """Test natural collection:name syntax for agents (user-friendly)."""
    resolver = AgentResolver()

    # Natural syntax: developer-expertise:zen-architect (auto-adds agents/ and .md)
    path = resolver.resolve("developer-expertise:zen-architect")
    assert path is not None
    assert path.name == "zen-architect.md"
    assert path.exists()

    # Also test with .md extension
    path2 = resolver.resolve("developer-expertise:zen-architect.md")
    assert path2 is not None
    assert path2.name == "zen-architect.md"

    # Test another agent
    path3 = resolver.resolve("developer-expertise:bug-hunter")
    assert path3 is not None
    assert path3.name == "bug-hunter.md"


def test_resolve_agent_simple_name_still_works():
    """Test that simple agent names still work (backward compat)."""
    resolver = AgentResolver()

    # Resolve by simple name (searches local paths)
    path = resolver.resolve("zen-architect")
    assert path is not None
    assert path.name == "zen-architect.md"
