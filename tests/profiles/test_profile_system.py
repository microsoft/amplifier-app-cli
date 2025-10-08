"""Integration tests for the profile system."""

import tempfile
from pathlib import Path

import pytest

from amplifier_app_cli.profiles import (
    ModuleConfig,
    Profile,
    ProfileLoader,
    ProfileManager,
    ProfileMetadata,
    SessionConfig,
    compile_profile_to_mount_plan,
)


@pytest.fixture
def temp_profile_dir():
    """Create a temporary directory for profile testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_base_profile(temp_profile_dir):
    """Create a sample base profile."""
    profile_path = temp_profile_dir / "base.toml"
    profile_path.write_text("""
[profile]
name = "base"
version = "1.0.0"
description = "Base profile for testing"

[session]
orchestrator = "loop-basic"
context = "context-simple"
max_tokens = 100000

[[providers]]
module = "provider-anthropic"

[[tools]]
module = "tool-filesystem"
""")
    return profile_path


@pytest.fixture
def sample_child_profile(temp_profile_dir):
    """Create a sample profile that extends base."""
    profile_path = temp_profile_dir / "dev.toml"
    profile_path.write_text("""
[profile]
name = "dev"
version = "1.0.0"
description = "Development profile"
extends = "base"

[session]
orchestrator = "loop-streaming"

[[tools]]
module = "tool-web"
""")
    return profile_path


def test_profile_loading(temp_profile_dir, sample_base_profile):
    """Test loading a profile from disk."""
    _ = sample_base_profile  # Fixture creates the file
    loader = ProfileLoader([temp_profile_dir])
    profile = loader.load_profile("base")

    assert profile.profile.name == "base"
    assert profile.profile.version == "1.0.0"
    assert profile.session.orchestrator == "loop-basic"
    assert profile.session.context == "context-simple"
    assert len(profile.providers) == 1
    assert profile.providers[0].module == "provider-anthropic"


def test_profile_inheritance(temp_profile_dir, sample_base_profile, sample_child_profile):
    """Test profile inheritance chain resolution."""
    _ = sample_base_profile  # Fixture creates the file
    _ = sample_child_profile  # Fixture creates the file
    loader = ProfileLoader([temp_profile_dir])
    child = loader.load_profile("dev")

    chain = loader.resolve_inheritance(child)

    # Chain should be: base (parent) -> dev (child)
    assert len(chain) == 2
    assert chain[0].profile.name == "base"
    assert chain[1].profile.name == "dev"


def test_profile_compilation(temp_profile_dir, sample_base_profile):
    """Test compilation of profile to Mount Plan."""
    _ = sample_base_profile  # Fixture creates the file
    loader = ProfileLoader([temp_profile_dir])
    profile = loader.load_profile("base")

    mount_plan = compile_profile_to_mount_plan(profile)

    # Verify Mount Plan structure
    assert "session" in mount_plan
    assert mount_plan["session"]["orchestrator"] == "loop-basic"
    assert mount_plan["session"]["context"] == "context-simple"

    # Verify context config
    assert "context" in mount_plan
    assert mount_plan["context"]["config"]["max_tokens"] == 100000

    # Verify modules
    assert "providers" in mount_plan
    assert len(mount_plan["providers"]) == 1
    assert mount_plan["providers"][0]["module"] == "provider-anthropic"

    assert "tools" in mount_plan
    assert len(mount_plan["tools"]) == 1
    assert mount_plan["tools"][0]["module"] == "tool-filesystem"


def test_profile_inheritance_compilation(
    temp_profile_dir, sample_base_profile, sample_child_profile
):
    """Test compilation with inheritance."""
    _ = sample_base_profile  # Fixture creates the file
    _ = sample_child_profile  # Fixture creates the file
    loader = ProfileLoader([temp_profile_dir])
    child = loader.load_profile("dev")

    chain = loader.resolve_inheritance(child)
    base_mount = compile_profile_to_mount_plan(chain[0])
    child_mount = compile_profile_to_mount_plan(chain[1])

    # Verify base mount plan
    assert base_mount["session"]["orchestrator"] == "loop-basic"
    assert len(base_mount["tools"]) == 1

    # Verify child mount plan
    assert child_mount["session"]["orchestrator"] == "loop-streaming"
    assert len(child_mount["tools"]) == 1
    assert child_mount["tools"][0]["module"] == "tool-web"


def test_profile_overlays(temp_profile_dir):
    """Test profile overlay merging."""
    # Create base profile
    (temp_profile_dir / "test.toml").write_text("""
[profile]
name = "test"
version = "1.0.0"
description = "Test profile"

[session]
orchestrator = "loop-basic"
context = "context-simple"

[[tools]]
module = "tool-filesystem"
""")

    loader = ProfileLoader([temp_profile_dir])
    base = loader.load_profile("test")

    # Create overlay
    overlay_profile = Profile(
        profile=ProfileMetadata(
            name="test-overlay",
            version="1.0.0",
            description="Overlay",
            extends=None,
        ),
        session=SessionConfig(
            orchestrator="loop-streaming",
            context="context-simple",
            max_tokens=None,
            compact_threshold=None,
            auto_compact=None,
        ),
        tools=[ModuleConfig(module="tool-web", config=None)],
    )

    # Compile with overlay
    mount_plan = compile_profile_to_mount_plan(base, [overlay_profile])

    # Verify overlay was applied
    assert mount_plan["session"]["orchestrator"] == "loop-streaming"
    assert len(mount_plan["tools"]) == 2
    tool_modules = [t["module"] for t in mount_plan["tools"]]
    assert "tool-filesystem" in tool_modules
    assert "tool-web" in tool_modules


def test_profile_manager_state(temp_profile_dir):
    """Test ProfileManager active profile state management."""
    state_file = temp_profile_dir / "active-profile.txt"
    manager = ProfileManager(state_file)

    # Initially no active profile
    assert manager.get_active_profile() is None

    # Set active profile
    manager.set_active_profile("dev")
    assert manager.get_active_profile() == "dev"

    # Verify persistence
    assert state_file.exists()
    assert state_file.read_text().strip() == "dev"

    # Clear active profile
    manager.clear_active_profile()
    assert manager.get_active_profile() is None
    assert not state_file.exists()


def test_module_list_merging():
    """Test module list merging by module ID."""
    base = Profile(
        profile=ProfileMetadata(name="base", version="1.0.0", description="Base", extends=None),
        session=SessionConfig(
            orchestrator="loop-basic",
            context="context-simple",
            max_tokens=None,
            compact_threshold=None,
            auto_compact=None,
        ),
        tools=[
            ModuleConfig(module="tool-filesystem", config={"base": True}),
            ModuleConfig(module="tool-bash", config=None),
        ],
    )

    overlay = Profile(
        profile=ProfileMetadata(
            name="overlay", version="1.0.0", description="Overlay", extends=None
        ),
        session=SessionConfig(
            orchestrator="loop-basic",
            context="context-simple",
            max_tokens=None,
            compact_threshold=None,
            auto_compact=None,
        ),
        tools=[
            ModuleConfig(module="tool-filesystem", config={"overlay": True}),  # Override
            ModuleConfig(module="tool-web", config=None),  # Add new
        ],
    )

    mount_plan = compile_profile_to_mount_plan(base, [overlay])

    # Verify merging
    tools = mount_plan["tools"]
    assert len(tools) == 3

    # tool-filesystem should be overridden
    fs_tool = next(t for t in tools if t["module"] == "tool-filesystem")
    assert fs_tool["config"]["overlay"] is True
    assert "base" not in fs_tool["config"]

    # tool-bash should remain
    bash_tool = next(t for t in tools if t["module"] == "tool-bash")
    assert bash_tool is not None

    # tool-web should be added
    web_tool = next(t for t in tools if t["module"] == "tool-web")
    assert web_tool is not None


def test_profile_not_found():
    """Test error handling for missing profiles."""
    loader = ProfileLoader([Path("/nonexistent")])

    with pytest.raises(FileNotFoundError):
        loader.load_profile("nonexistent")


def test_circular_inheritance(temp_profile_dir):
    """Test circular inheritance detection."""
    (temp_profile_dir / "a.toml").write_text("""
[profile]
name = "a"
version = "1.0.0"
description = "Profile A"
extends = "b"

[session]
orchestrator = "loop-basic"
context = "context-simple"
""")

    (temp_profile_dir / "b.toml").write_text("""
[profile]
name = "b"
version = "1.0.0"
description = "Profile B"
extends = "a"

[session]
orchestrator = "loop-basic"
context = "context-simple"
""")

    loader = ProfileLoader([temp_profile_dir])
    profile_a = loader.load_profile("a")

    with pytest.raises(ValueError, match="Circular inheritance"):
        loader.resolve_inheritance(profile_a)
