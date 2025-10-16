"""Tests for ProfileLoader."""

import pytest
from amplifier_app_cli.profile_system.loader import ProfileLoader
from amplifier_app_cli.profile_system.schema import Profile


class TestProfileLoader:
    """Test profile loading functionality."""

    @pytest.fixture
    def temp_profiles_dir(self, tmp_path):
        """Create a temporary profiles directory."""
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        return profiles_dir

    @pytest.fixture
    def loader_with_temp(self, temp_profiles_dir):
        """Create ProfileLoader with temporary directory."""
        return ProfileLoader([temp_profiles_dir])

    def test_validate_model_pair_valid(self, loader_with_temp):
        """Test validation of valid model pairs."""
        # Valid formats
        loader_with_temp.validate_model_pair("anthropic/claude-3-5-sonnet")
        loader_with_temp.validate_model_pair("openai/gpt-4")
        loader_with_temp.validate_model_pair("provider/model-with-dashes")

    def test_validate_model_pair_invalid(self, loader_with_temp):
        """Test validation of invalid model pairs."""
        # Missing slash
        with pytest.raises(ValueError, match="must be 'provider/model'"):
            loader_with_temp.validate_model_pair("invalid-format")

        # Empty provider
        with pytest.raises(ValueError, match="Invalid model pair"):
            loader_with_temp.validate_model_pair("/model")

        # Empty model
        with pytest.raises(ValueError, match="Invalid model pair"):
            loader_with_temp.validate_model_pair("provider/")

        # Empty string
        with pytest.raises(ValueError, match="must be 'provider/model'"):
            loader_with_temp.validate_model_pair("")

    def test_merge_profiles_basic(self, loader_with_temp):
        """Test basic profile merging."""
        # Create parent profile
        parent_dict = {
            "profile": {"name": "parent", "version": "1.0.0", "description": "Parent profile"},
            "session": {"orchestrator": "loop", "context": "simple", "max_tokens": 1000},
            "providers": [{"module": "provider-base"}],
        }
        parent = Profile(**parent_dict)

        # Create child profile that overrides some values
        child_dict = {
            "profile": {"name": "child", "version": "1.0.0", "description": "Child profile", "extends": "parent"},
            "session": {"orchestrator": "loop", "context": "simple", "max_tokens": 2000},
            "tools": [{"module": "tool-extra"}],
        }
        child = Profile(**child_dict)

        # Merge profiles
        merged = loader_with_temp.merge_profiles(parent, child)

        # Check overridden values
        assert merged.session.max_tokens == 2000  # Child overrides

        # Check inherited values
        assert merged.providers == parent.providers  # Inherited from parent

        # Check new values
        assert merged.tools == child.tools  # New in child

    def test_merge_profiles_deep_nesting(self, loader_with_temp):
        """Test merging with deeply nested configuration."""
        parent_dict = {
            "profile": {"name": "parent", "version": "1.0.0", "description": "Parent"},
            "session": {"orchestrator": "loop", "context": "simple"},
            "orchestrator": {"config": {"timeout": 30, "retry": 3}},
        }
        parent = Profile(**parent_dict)

        child_dict = {
            "profile": {"name": "child", "version": "1.0.0", "description": "Child"},
            "session": {"orchestrator": "loop", "context": "simple"},
            "orchestrator": {"config": {"timeout": 60}},  # Override only timeout
        }
        child = Profile(**child_dict)

        merged = loader_with_temp.merge_profiles(parent, child)

        # Deep merge should override only specified values
        assert merged.orchestrator.config["timeout"] == 60  # Overridden
        assert merged.orchestrator.config["retry"] == 3  # Preserved from parent

    def test_merge_profiles_none_removal(self, loader_with_temp):
        """Test that None values in child remove parent values."""
        parent_dict = {
            "profile": {"name": "parent", "version": "1.0.0", "description": "Parent"},
            "session": {"orchestrator": "loop", "context": "simple", "max_tokens": 1000},
        }
        parent = Profile(**parent_dict)

        child_dict = {
            "profile": {"name": "child", "version": "1.0.0", "description": "Child"},
            "session": {"orchestrator": "loop", "context": "simple", "max_tokens": None},
        }
        child = Profile(**child_dict)

        merged = loader_with_temp.merge_profiles(parent, child)

        # None in child should remove the value
        assert merged.session.max_tokens is None

    def test_load_profile_simple(self, temp_profiles_dir):
        """Test loading a simple profile without inheritance."""
        # Create a simple profile file
        profile_content = """---
profile:
  name: simple
  version: 1.0.0
  description: Simple profile
session:
  orchestrator: loop
  context: simple
---

Simple test profile for unit testing.
"""
        (temp_profiles_dir / "simple.md").write_text(profile_content)

        loader = ProfileLoader([temp_profiles_dir])
        profile = loader.load_profile("simple")

        assert profile.profile.name == "simple"
        assert profile.session.orchestrator == "loop"
        assert profile.session.context == "simple"

    def test_load_profile_with_inheritance(self, temp_profiles_dir):
        """Test loading a profile with inheritance."""
        # Create parent profile
        parent_content = """---
profile:
  name: base
  version: 1.0.0
  description: Base profile
session:
  orchestrator: loop
  context: simple
  max_tokens: 1000
providers:
  - module: provider-base
---

Base profile for testing inheritance.
"""
        (temp_profiles_dir / "base.md").write_text(parent_content)

        # Create child profile
        child_content = """---
profile:
  name: extended
  version: 1.0.0
  description: Extended profile
  extends: base
session:
  orchestrator: loop
  context: simple
  max_tokens: 2000
tools:
  - module: tool-extra
---

Extended profile that inherits from base.
"""
        (temp_profiles_dir / "extended.md").write_text(child_content)

        loader = ProfileLoader([temp_profiles_dir])
        profile = loader.load_profile("extended")

        # Check inheritance worked
        assert profile.profile.name == "extended"
        assert profile.session.context.config and profile.session.context.config.get("max_tokens") == 2000  # Overridden
        assert len(profile.providers) == 1  # Inherited
        assert profile.providers[0].module == "provider-base"
        assert len(profile.tools) == 1  # New in child
        assert profile.tools[0].module == "tool-extra"

    def test_circular_dependency_detection(self, temp_profiles_dir):
        """Test that circular dependencies are detected."""
        # Create profile A that extends B
        profile_a = """---
profile:
  name: a
  version: 1.0.0
  description: Profile A
  extends: b
session:
  orchestrator: loop
  context: simple
---

Profile A (circular dependency test).
"""
        (temp_profiles_dir / "a.md").write_text(profile_a)

        # Create profile B that extends A (circular)
        profile_b = """---
profile:
  name: b
  version: 1.0.0
  description: Profile B
  extends: a
session:
  orchestrator: loop
  context: simple
---

Profile B (circular dependency test).
"""
        (temp_profiles_dir / "b.md").write_text(profile_b)

        loader = ProfileLoader([temp_profiles_dir])

        with pytest.raises(ValueError, match="Circular dependency"):
            loader.load_profile("a")

    def test_load_profile_not_found(self, loader_with_temp):
        """Test loading a non-existent profile."""
        with pytest.raises(FileNotFoundError, match="Profile 'nonexistent' not found"):
            loader_with_temp.load_profile("nonexistent")

    def test_load_profile_invalid_yaml(self, temp_profiles_dir):
        """Test loading an invalid YAML file."""
        # Create invalid YAML frontmatter
        (temp_profiles_dir / "invalid.md").write_text("---\nthis is not valid yaml: {{\n---")

        loader = ProfileLoader([temp_profiles_dir])

        with pytest.raises(ValueError, match="Invalid profile file"):
            loader.load_profile("invalid")

    def test_load_profile_missing_required_fields(self, temp_profiles_dir):
        """Test loading profile with missing required fields."""
        # Profile missing required session fields
        incomplete_profile = """---
profile:
  name: incomplete
  version: 1.0.0
  description: Incomplete profile
---

Incomplete profile for testing validation.
"""
        (temp_profiles_dir / "incomplete.md").write_text(incomplete_profile)

        loader = ProfileLoader([temp_profiles_dir])

        with pytest.raises(ValueError, match="Invalid profile file"):
            loader.load_profile("incomplete")

    def test_list_profiles(self, temp_profiles_dir):
        """Test listing available profiles."""
        # Create several profile files
        for name in ["profile1", "profile2", "profile3"]:
            content = f"""---
profile:
  name: {name}
  version: 1.0.0
  description: Test profile {name}
session:
  orchestrator: loop
  context: simple
---

Test profile {name} for listing.
"""
            (temp_profiles_dir / f"{name}.md").write_text(content)

        # Also create a non-MD file that should be ignored
        (temp_profiles_dir / "not-a-profile.txt").write_text("Not a profile")

        loader = ProfileLoader([temp_profiles_dir])
        profiles = loader.list_profiles()

        assert sorted(profiles) == ["profile1", "profile2", "profile3"]
        assert "not-a-profile" not in profiles

    def test_find_profile_file_precedence(self, tmp_path):
        """Test that profile file discovery respects precedence."""
        # Create multiple directories with same profile
        low_priority = tmp_path / "low"
        high_priority = tmp_path / "high"
        low_priority.mkdir()
        high_priority.mkdir()

        # Same profile in both directories
        (low_priority / "test.md").write_text("---\nprofile:\n  name: test\n---\nlow priority")
        (high_priority / "test.md").write_text("---\nprofile:\n  name: test\n---\nhigh priority")

        # Loader with paths in order: low, high
        # find_profile_file searches in reverse (high first)
        loader = ProfileLoader([low_priority, high_priority])

        found = loader.find_profile_file("test")
        assert found == high_priority / "test.md"

    def test_load_profile_with_model_validation(self, temp_profiles_dir):
        """Test that model field is validated when present."""
        # Profile with valid model
        valid_model_profile = """---
profile:
  name: with-model
  version: 1.0.0
  description: Profile with model
  model: anthropic/claude-3-5-sonnet
session:
  orchestrator: loop
  context: simple
---

Profile with model specification.
"""
        (temp_profiles_dir / "with-model.md").write_text(valid_model_profile)

        loader = ProfileLoader([temp_profiles_dir])

        # Should load successfully
        profile = loader.load_profile("with-model")
        assert profile.profile.model == "anthropic/claude-3-5-sonnet"

        # Profile with invalid model
        invalid_model_profile = """---
profile:
  name: bad-model
  version: 1.0.0
  description: Profile with invalid model
  model: invalid-format
session:
  orchestrator: loop
  context: simple
---

Profile with invalid model format.
"""
        (temp_profiles_dir / "bad-model.md").write_text(invalid_model_profile)

        # Should fail validation
        with pytest.raises(ValueError, match="must be 'provider/model'"):
            loader.load_profile("bad-model")

    def test_deep_inheritance_chain(self, temp_profiles_dir):
        """Test loading profile with deep inheritance chain."""
        # Create a chain: base -> middle -> top
        base_content = """---
profile:
  name: base
  version: 1.0.0
  description: Base profile
session:
  orchestrator: loop
  context: simple
  max_tokens: 1000
providers:
  - module: provider-base
---

Base profile in inheritance chain.
"""
        (temp_profiles_dir / "base.md").write_text(base_content)

        middle_content = """---
profile:
  name: middle
  version: 1.0.0
  description: Middle profile
  extends: base
session:
  orchestrator: loop
  context: simple
  max_tokens: 2000
tools:
  - module: tool-middle
---

Middle profile in inheritance chain.
"""
        (temp_profiles_dir / "middle.md").write_text(middle_content)

        top_content = """---
profile:
  name: top
  version: 1.0.0
  description: Top profile
  extends: middle
session:
  orchestrator: loop
  context: advanced
hooks:
  - module: hook-top
---

Top profile in inheritance chain.
"""
        (temp_profiles_dir / "top.md").write_text(top_content)

        loader = ProfileLoader([temp_profiles_dir])
        profile = loader.load_profile("top")

        # Check full inheritance chain
        assert profile.profile.name == "top"
        assert profile.session.context.module == "advanced"  # Overridden at top
        assert profile.session.context.config.get("max_tokens") == 2000  # From middle
        assert len(profile.providers) == 1  # From base
        assert len(profile.tools) == 1  # From middle
        assert len(profile.hooks) == 1  # From top
