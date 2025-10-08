"""
Test specifically for the profile inheritance bug fix.

This test verifies that when a child profile extends a parent profile,
the module lists are properly merged rather than replaced.
"""

from pathlib import Path
import tempfile
from amplifier_app_cli.profiles.loader import ProfileLoader
from amplifier_app_cli.profiles.compiler import compile_profile_to_mount_plan
from amplifier_app_cli.main import deep_merge


def test_profile_inheritance_with_providers():
    """
    Test that profile inheritance properly merges module lists.

    This was the original bug: when production profile extended base profile,
    production's empty providers list would overwrite base's providers.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles_dir = Path(tmpdir)

        # Create base profile with a provider
        base_profile = profiles_dir / "base.toml"
        base_profile.write_text("""
[profile]
name = "base"
version = "1.0.0"
description = "Base profile with provider"

[session]
context = "simple"
orchestrator = "loop-streaming"

[[providers]]
module = "provider-anthropic"
[providers.config]
api_key = "${ANTHROPIC_API_KEY}"
model = "claude-3-5-sonnet-latest"
""")

        # Create production profile that extends base
        # This has NO providers defined (empty list after parsing)
        prod_profile = profiles_dir / "production.toml"
        prod_profile.write_text("""
[profile]
name = "production"
version = "1.0.0"
description = "Production profile"
extends = "base"

[session]
context = "simple"
orchestrator = "loop-streaming"

[[tools]]
module = "tool-web"

[[hooks]]
module = "hooks-logging"
[hooks.config]
level = "INFO"
""")

        # Load profiles
        loader = ProfileLoader([profiles_dir])

        # Load production profile (which extends base)
        prod = loader.load_profile("production")

        # Resolve inheritance chain
        inheritance_chain = loader.resolve_inheritance(prod)

        # This should return [base, production] in that order
        assert len(inheritance_chain) == 2
        assert inheritance_chain[0].profile.name == "base"
        assert inheritance_chain[1].profile.name == "production"

        # Now compile and merge as the main.py does
        profile_config = compile_profile_to_mount_plan(inheritance_chain[0], [])

        for parent_profile in inheritance_chain[1:]:
            profile_config = deep_merge(
                profile_config, compile_profile_to_mount_plan(parent_profile, [])
            )

        # Verify the provider from base is preserved
        assert len(profile_config["providers"]) == 1
        assert profile_config["providers"][0]["module"] == "provider-anthropic"

        # Verify tools from production are added
        assert len(profile_config["tools"]) == 1
        assert profile_config["tools"][0]["module"] == "tool-web"

        # Verify hooks from production are added
        assert len(profile_config["hooks"]) == 1
        assert profile_config["hooks"][0]["module"] == "hooks-logging"


def test_multi_level_inheritance():
    """Test inheritance chain with multiple levels."""
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles_dir = Path(tmpdir)

        # Create base profile
        base_profile = profiles_dir / "base.toml"
        base_profile.write_text("""
[profile]
name = "base"
version = "1.0.0"
description = "Base profile"

[session]
context = "simple"
orchestrator = "loop-streaming"

[[providers]]
module = "provider-anthropic"

[[tools]]
module = "tool-filesystem"
""")

        # Create dev profile extending base
        dev_profile = profiles_dir / "dev.toml"
        dev_profile.write_text("""
[profile]
name = "dev"
version = "1.0.0"
description = "Development profile"
extends = "base"

[session]
context = "simple"
orchestrator = "loop-streaming"

[[tools]]
module = "tool-bash"

[[hooks]]
module = "hooks-debug"
""")

        # Create test profile extending dev
        test_profile = profiles_dir / "test.toml"
        test_profile.write_text("""
[profile]
name = "test"
version = "1.0.0"
description = "Test profile"
extends = "dev"

[session]
context = "simple"
orchestrator = "loop-streaming"

[[tools]]
module = "tool-web"

[[agents]]
module = "agent-tester"
""")

        # Load and resolve
        loader = ProfileLoader([profiles_dir])
        test = loader.load_profile("test")
        inheritance_chain = loader.resolve_inheritance(test)

        # Should have [base, dev, test]
        assert len(inheritance_chain) == 3
        assert inheritance_chain[0].profile.name == "base"
        assert inheritance_chain[1].profile.name == "dev"
        assert inheritance_chain[2].profile.name == "test"

        # Compile and merge
        profile_config = compile_profile_to_mount_plan(inheritance_chain[0], [])

        for parent_profile in inheritance_chain[1:]:
            profile_config = deep_merge(
                profile_config, compile_profile_to_mount_plan(parent_profile, [])
            )

        # Verify all modules are present
        assert len(profile_config["providers"]) == 1  # From base
        assert profile_config["providers"][0]["module"] == "provider-anthropic"

        assert len(profile_config["tools"]) == 3  # From base, dev, and test
        tool_modules = [t["module"] for t in profile_config["tools"]]
        assert "tool-filesystem" in tool_modules
        assert "tool-bash" in tool_modules
        assert "tool-web" in tool_modules

        assert len(profile_config["hooks"]) == 1  # From dev
        assert profile_config["hooks"][0]["module"] == "hooks-debug"

        assert len(profile_config["agents"]) == 1  # From test
        assert profile_config["agents"][0]["module"] == "agent-tester"


def test_override_module_config():
    """Test that child profiles can override parent module configs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles_dir = Path(tmpdir)

        # Create base profile with configured provider
        base_profile = profiles_dir / "base.toml"
        base_profile.write_text("""
[profile]
name = "base"
version = "1.0.0"
description = "Base profile"

[session]
context = "simple"
orchestrator = "loop-streaming"

[[providers]]
module = "provider-anthropic"
[providers.config]
model = "claude-3-sonnet"
temperature = 0.7
""")

        # Create prod profile that overrides the provider config
        prod_profile = profiles_dir / "prod.toml"
        prod_profile.write_text("""
[profile]
name = "prod"
version = "1.0.0"
description = "Production profile"
extends = "base"

[session]
context = "simple"
orchestrator = "loop-streaming"

[[providers]]
module = "provider-anthropic"
[providers.config]
model = "claude-3-5-sonnet-latest"
temperature = 0.3
max_tokens = 4096
""")

        loader = ProfileLoader([profiles_dir])
        prod = loader.load_profile("prod")
        inheritance_chain = loader.resolve_inheritance(prod)

        # Compile and merge
        profile_config = compile_profile_to_mount_plan(inheritance_chain[0], [])

        for parent_profile in inheritance_chain[1:]:
            profile_config = deep_merge(
                profile_config, compile_profile_to_mount_plan(parent_profile, [])
            )

        # Should have one provider with prod's config
        assert len(profile_config["providers"]) == 1
        provider = profile_config["providers"][0]
        assert provider["module"] == "provider-anthropic"
        assert provider["config"]["model"] == "claude-3-5-sonnet-latest"
        assert provider["config"]["temperature"] == 0.3
        assert provider["config"]["max_tokens"] == 4096
