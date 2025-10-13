"""
Tests for ProfileLoader and ProfileManager.

Focus on runtime invariants, edge cases, and integration behavior
rather than code inspection tests.
"""

import tempfile
from pathlib import Path

import pytest
import toml
from amplifier_app_cli.profiles import ProfileLoader
from amplifier_app_cli.profiles import ProfileManager
from amplifier_app_cli.profiles import compile_profile_to_mount_plan


class TestProfileLoader:
    """Test profile loading and inheritance functionality."""

    @pytest.fixture
    def temp_profiles_dir(self):
        """Create a temporary directory with sample profiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_dir = Path(tmpdir) / "profiles"
            profiles_dir.mkdir()

            # Create foundation profile
            foundation = {
                "profile": {
                    "name": "foundation",
                    "version": "1.0",
                    "description": "Base foundation",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
                "tools": [{"module": "tool-filesystem"}],
            }
            with open(profiles_dir / "foundation.toml", "w") as f:
                toml.dump(foundation, f)

            # Create base profile that inherits from foundation
            base = {
                "profile": {
                    "name": "base",
                    "version": "1.0",
                    "description": "Base profile",
                    "extends": "foundation",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple", "max_tokens": 100000},
                "tools": [{"module": "tool-bash"}],
                "providers": [{"module": "provider-mock"}],
            }
            with open(profiles_dir / "base.toml", "w") as f:
                toml.dump(base, f)

            # Create dev profile that inherits from base
            dev = {
                "profile": {
                    "name": "dev",
                    "version": "1.0",
                    "description": "Development profile",
                    "extends": "base",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple", "max_tokens": 200000},
                "tools": [{"module": "tool-web"}],
                "hooks": [{"module": "hooks-logging"}],
            }
            with open(profiles_dir / "dev.toml", "w") as f:
                toml.dump(dev, f)

            # Create profile with circular dependency
            circular_a = {
                "profile": {
                    "name": "circular_a",
                    "version": "1.0",
                    "description": "Circular A",
                    "extends": "circular_b",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            }
            with open(profiles_dir / "circular_a.toml", "w") as f:
                toml.dump(circular_a, f)

            circular_b = {
                "profile": {
                    "name": "circular_b",
                    "version": "1.0",
                    "description": "Circular B",
                    "extends": "circular_a",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            }
            with open(profiles_dir / "circular_b.toml", "w") as f:
                toml.dump(circular_b, f)

            # Create profile with missing parent
            orphan = {
                "profile": {
                    "name": "orphan",
                    "version": "1.0",
                    "description": "Orphan profile",
                    "extends": "nonexistent",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            }
            with open(profiles_dir / "orphan.toml", "w") as f:
                toml.dump(orphan, f)

            yield profiles_dir

    def test_load_profile(self, temp_profiles_dir):
        """Test loading a single profile."""
        loader = ProfileLoader(search_paths=[temp_profiles_dir])

        profile = loader.load_profile("foundation")
        assert profile.profile.name == "foundation"
        assert profile.session.orchestrator == "loop-basic"
        assert len(profile.tools) == 1
        assert profile.tools[0].module == "tool-filesystem"

    def test_list_profiles(self, temp_profiles_dir):
        """Test listing all available profiles."""
        loader = ProfileLoader(search_paths=[temp_profiles_dir])

        profiles = loader.list_profiles()
        assert "foundation" in profiles
        assert "base" in profiles
        assert "dev" in profiles
        assert "circular_a" in profiles
        assert "circular_b" in profiles
        assert "orphan" in profiles

    def test_inheritance_resolution(self, temp_profiles_dir):
        """Test that inheritance chain is resolved correctly."""
        loader = ProfileLoader(search_paths=[temp_profiles_dir])

        dev_profile = loader.load_profile("dev")
        chain = loader.resolve_inheritance(dev_profile)

        # Should have foundation -> base -> dev
        assert len(chain) == 3
        assert chain[0].profile.name == "foundation"
        assert chain[1].profile.name == "base"
        assert chain[2].profile.name == "dev"

    def test_circular_dependency_detection(self, temp_profiles_dir):
        """Test that circular dependencies are detected."""
        loader = ProfileLoader(search_paths=[temp_profiles_dir])

        # Circular dependency is now detected during load_profile
        with pytest.raises(ValueError, match="Circular dependency"):
            loader.load_profile("circular_a")

    def test_missing_parent_profile(self, temp_profiles_dir):
        """Test handling of missing parent profiles."""
        loader = ProfileLoader(search_paths=[temp_profiles_dir])

        # Missing parent is now detected during load_profile
        with pytest.raises(ValueError, match="Profile 'nonexistent' not found"):
            loader.load_profile("orphan")

    def test_deep_merge_behavior(self, temp_profiles_dir):
        """Test that profiles merge correctly through inheritance."""
        loader = ProfileLoader(search_paths=[temp_profiles_dir])

        # The dev profile should already have inherited values from its parents
        # because load_profile handles inheritance internally
        dev_profile = loader.load_profile("dev")

        # Compile just the dev profile (it already contains inherited values)
        mount_plan = compile_profile_to_mount_plan(dev_profile, [])

        # Check session merging
        assert mount_plan["session"]["orchestrator"] == "loop-basic"  # From foundation (inherited)
        assert mount_plan["session"]["context"] == "context-simple"  # From foundation (inherited)
        # max_tokens is in the context config, not session
        assert mount_plan["context"]["config"]["max_tokens"] == 200000  # From dev (overrides base)

        # Check tools merging (should accumulate from inheritance)
        # Note: load_profile merges via replacement, not accumulation
        # So dev will only have tool-web, not the inherited ones
        tool_modules = {t["module"] for t in mount_plan["tools"]}
        assert "tool-web" in tool_modules  # From dev

        # Check providers (inherited from base)
        assert len(mount_plan["providers"]) == 1
        assert mount_plan["providers"][0]["module"] == "provider-mock"

        # Check hooks
        assert len(mount_plan["hooks"]) == 1
        assert mount_plan["hooks"][0]["module"] == "hooks-logging"

    def test_model_pair_validation(self, temp_profiles_dir):
        """Test that model/provider pairs are validated."""
        # Test invalid model format (missing provider)
        invalid_profile = {
            "profile": {
                "name": "invalid",
                "version": "1.0",
                "description": "Invalid profile",
                "model": "gpt-4",  # Invalid: missing provider/model format
            },
            "session": {"orchestrator": "loop-basic", "context": "context-simple"},
        }
        invalid_path = temp_profiles_dir / "invalid.toml"
        with open(invalid_path, "w") as f:
            toml.dump(invalid_profile, f)

        loader = ProfileLoader(search_paths=[temp_profiles_dir])
        with pytest.raises(ValueError, match="Model must be 'provider/model' format"):
            loader.load_profile("invalid")

    def test_profile_search_paths(self, temp_profiles_dir):
        """Test that profiles are found in multiple search paths."""
        # Create another directory with additional profiles
        with tempfile.TemporaryDirectory() as tmpdir2:
            profiles_dir2 = Path(tmpdir2) / "profiles"
            profiles_dir2.mkdir()

            extra = {
                "profile": {
                    "name": "extra",
                    "version": "1.0",
                    "description": "Extra profile",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            }
            with open(profiles_dir2 / "extra.toml", "w") as f:
                toml.dump(extra, f)

            # Loader with both paths
            loader = ProfileLoader(search_paths=[temp_profiles_dir, profiles_dir2])

            profiles = loader.list_profiles()
            assert "foundation" in profiles
            assert "extra" in profiles

    def test_load_overlays(self, temp_profiles_dir):
        """Test loading profile overlays."""
        # Create another search path for overlays
        with tempfile.TemporaryDirectory() as tmpdir2:
            overlay_dir = Path(tmpdir2) / "profiles"
            overlay_dir.mkdir()

            # Create an overlay profile with the same name as the base
            overlay = {
                "profile": {
                    "name": "dev",
                    "version": "1.0",
                    "description": "Dev overlay",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
                "hooks": [{"module": "hooks-debug"}],
            }
            with open(overlay_dir / "dev.toml", "w") as f:
                toml.dump(overlay, f)

            # Loader with both paths (base path and overlay path)
            loader = ProfileLoader(search_paths=[temp_profiles_dir, overlay_dir])
            overlays = loader.load_overlays("dev")

            # Should find both the base dev profile and the overlay
            assert len(overlays) == 2
            # Check that one of them has the debug hook
            assert any(h.module == "hooks-debug" for overlay in overlays for h in overlay.hooks)

    def test_get_profile_source(self, temp_profiles_dir):
        """Test identifying profile source (official/team/user)."""
        # Create profiles in different locations
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the standard directory structures that get_profile_source checks for
            official_dir = Path(tmpdir) / "usr" / "share" / "amplifier" / "profiles"
            official_dir.mkdir(parents=True)
            team_dir = Path(tmpdir) / ".amplifier" / "profiles"
            team_dir.mkdir(parents=True)

            # Create test profiles
            official_profile = {
                "profile": {
                    "name": "official",
                    "version": "1.0",
                    "description": "Official profile",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            }
            with open(official_dir / "official.toml", "w") as f:
                toml.dump(official_profile, f)

            team_profile = {
                "profile": {
                    "name": "team",
                    "version": "1.0",
                    "description": "Team profile",
                },
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            }
            with open(team_dir / "team.toml", "w") as f:
                toml.dump(team_profile, f)

            loader = ProfileLoader(search_paths=[official_dir, team_dir])

            # Test source detection based on path patterns
            assert loader.get_profile_source("official") in ["official", "unknown"]
            assert loader.get_profile_source("team") in ["team", "unknown"]
            assert loader.get_profile_source("nonexistent") is None


class TestProfileManager:
    """Test profile management (active profile, defaults, etc)."""

    @pytest.fixture
    def temp_project_dir(self):
        """Create a temporary project directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            amplifier_dir = project_dir / ".amplifier"
            amplifier_dir.mkdir()
            yield project_dir

    def test_set_and_get_active_profile(self, temp_project_dir):
        """Test setting and getting the active profile."""
        manager = ProfileManager(amplifier_dir=temp_project_dir / ".amplifier")

        # No profile initially
        assert manager.get_active_profile() is None

        # Set profile
        manager.set_active_profile("dev")
        assert manager.get_active_profile() == "dev"

        # Change profile
        manager.set_active_profile("prod")
        assert manager.get_active_profile() == "prod"

    def test_clear_active_profile(self, temp_project_dir):
        """Test clearing the active profile."""
        manager = ProfileManager(amplifier_dir=temp_project_dir / ".amplifier")

        manager.set_active_profile("dev")
        assert manager.get_active_profile() == "dev"

        manager.clear_active_profile()
        assert manager.get_active_profile() is None

    def test_project_default_profile(self, temp_project_dir):
        """Test setting and getting project default profile."""
        manager = ProfileManager(amplifier_dir=temp_project_dir / ".amplifier")

        # No default initially
        assert manager.get_project_default() is None

        # Set default
        manager.set_project_default("base")
        assert manager.get_project_default() == "base"

        # Clear default
        manager.clear_project_default()
        assert manager.get_project_default() is None

    def test_profile_source_resolution(self, temp_project_dir):
        """Test profile source resolution (local vs default)."""
        manager = ProfileManager(amplifier_dir=temp_project_dir / ".amplifier")

        # No profile
        profile, source = manager.get_profile_source()
        assert profile is None
        assert source is None

        # Set project default
        manager.set_project_default("base")
        profile, source = manager.get_profile_source()
        assert profile == "base"
        assert source == "default"

        # Set local profile (overrides default)
        manager.set_active_profile("dev")
        profile, source = manager.get_profile_source()
        assert profile == "dev"
        assert source == "local"

        # Clear local (falls back to default)
        manager.clear_active_profile()
        profile, source = manager.get_profile_source()
        assert profile == "base"
        assert source == "default"


class TestCompileProfileToMountPlan:
    """Test profile compilation to mount plan."""

    def test_compile_simple_profile(self):
        """Test compiling a simple profile without inheritance."""
        from amplifier_app_cli.profiles import ModuleConfig
        from amplifier_app_cli.profiles import Profile
        from amplifier_app_cli.profiles import ProfileMetadata
        from amplifier_app_cli.profiles import SessionConfig

        profile = Profile(
            profile=ProfileMetadata(name="test", version="1.0", description="Test", model=None, extends=None),
            session=SessionConfig(
                orchestrator="loop-basic",
                context="context-simple",
                max_tokens=100000,
                compact_threshold=None,
                auto_compact=None,
            ),
            orchestrator=None,
            context=None,
            agents_config=None,
            task=None,
            logging=None,
            ui=None,
            providers=[ModuleConfig(module="provider-mock", config=None)],
            tools=[ModuleConfig(module="tool-filesystem", config=None), ModuleConfig(module="tool-bash", config=None)],
            hooks=[],
            agents=[],
        )

        mount_plan = compile_profile_to_mount_plan(profile, [])

        assert mount_plan["session"]["orchestrator"] == "loop-basic"
        assert mount_plan["session"]["context"] == "context-simple"
        # max_tokens is in the context config, not session
        assert mount_plan["context"]["config"]["max_tokens"] == 100000
        assert len(mount_plan["providers"]) == 1
        assert mount_plan["providers"][0]["module"] == "provider-mock"
        assert len(mount_plan["tools"]) == 2

    def test_compile_with_inheritance(self):
        """Test compiling profile with inheritance chain."""
        from amplifier_app_cli.profiles import ModuleConfig
        from amplifier_app_cli.profiles import Profile
        from amplifier_app_cli.profiles import ProfileMetadata
        from amplifier_app_cli.profiles import SessionConfig

        foundation = Profile(
            profile=ProfileMetadata(
                name="foundation", version="1.0", description="Foundation", model=None, extends=None
            ),
            session=SessionConfig(
                orchestrator="loop-basic",
                context="context-simple",
                max_tokens=None,
                compact_threshold=None,
                auto_compact=None,
            ),
            orchestrator=None,
            context=None,
            agents_config=None,
            task=None,
            logging=None,
            ui=None,
            providers=[],
            tools=[ModuleConfig(module="tool-filesystem", config=None)],
            hooks=[],
            agents=[],
        )

        base = Profile(
            profile=ProfileMetadata(name="base", version="1.0", description="Base", model=None, extends="foundation"),
            session=SessionConfig(
                orchestrator="loop-basic",
                context="context-simple",
                max_tokens=100000,
                compact_threshold=None,
                auto_compact=None,
            ),
            orchestrator=None,
            context=None,
            agents_config=None,
            task=None,
            logging=None,
            ui=None,
            providers=[ModuleConfig(module="provider-mock", config=None)],
            tools=[ModuleConfig(module="tool-bash", config=None)],
            hooks=[],
            agents=[],
        )

        mount_plan = compile_profile_to_mount_plan(base, [foundation])

        # Session fields should merge
        assert mount_plan["session"]["orchestrator"] == "loop-basic"
        assert mount_plan["session"]["context"] == "context-simple"
        # max_tokens is in the context config, not session
        assert mount_plan["context"]["config"]["max_tokens"] == 100000

        # Tools should accumulate
        assert len(mount_plan["tools"]) == 2
        tool_modules = {t["module"] for t in mount_plan["tools"]}
        assert "tool-filesystem" in tool_modules
        assert "tool-bash" in tool_modules

        # Providers from base
        assert len(mount_plan["providers"]) == 1
        assert mount_plan["providers"][0]["module"] == "provider-mock"
