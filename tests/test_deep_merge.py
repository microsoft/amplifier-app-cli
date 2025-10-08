"""Tests for the deep_merge function with module list handling."""

from amplifier_app_cli.main import deep_merge


def test_deep_merge_replaces_regular_values():
    """Test that regular values are replaced by overlay."""
    base = {"key": "base_value", "number": 1}
    overlay = {"key": "overlay_value", "number": 2}
    result = deep_merge(base, overlay)
    assert result == {"key": "overlay_value", "number": 2}


def test_deep_merge_merges_dicts_recursively():
    """Test that nested dicts are merged recursively."""
    base = {"config": {"a": 1, "b": 2}}
    overlay = {"config": {"b": 3, "c": 4}}
    result = deep_merge(base, overlay)
    assert result == {"config": {"a": 1, "b": 3, "c": 4}}


def test_deep_merge_module_lists_basic():
    """Test that module lists are merged by module ID."""
    base = {
        "providers": [{"module": "provider-anthropic", "config": {"api_key": "old_key"}}],
        "tools": [{"module": "tool-filesystem"}],
    }
    overlay = {
        "providers": [
            {"module": "provider-anthropic", "config": {"api_key": "new_key"}},
            {"module": "provider-openai", "config": {"api_key": "openai_key"}},
        ],
        "tools": [],  # Empty list should not overwrite
    }

    result = deep_merge(base, overlay)

    # Provider-anthropic should be updated, provider-openai added
    assert len(result["providers"]) == 2
    anthropic = next(p for p in result["providers"] if p["module"] == "provider-anthropic")
    assert anthropic["config"]["api_key"] == "new_key"

    openai = next(p for p in result["providers"] if p["module"] == "provider-openai")
    assert openai["config"]["api_key"] == "openai_key"

    # Tools: Empty overlay list means no tools to add/update, base remains
    assert len(result["tools"]) == 1
    assert result["tools"][0]["module"] == "tool-filesystem"


def test_deep_merge_profile_inheritance_scenario():
    """Test the actual profile inheritance scenario that was failing."""
    # Base profile with provider
    base_config = {
        "providers": [
            {"module": "provider-anthropic", "config": {"api_key": "${ANTHROPIC_API_KEY}"}}
        ],
        "tools": [],
        "hooks": [],
        "agents": [],
    }

    # Production profile that extends base (has empty providers list)
    production_config = {
        "providers": [],  # This was overwriting base's providers
        "tools": [{"module": "tool-web"}],
        "hooks": [],
        "agents": [],
    }

    result = deep_merge(base_config, production_config)

    # The fix: providers should NOT be overwritten by empty list
    # Empty overlay list means no providers to add/update, base providers are preserved
    assert len(result["providers"]) == 1
    assert result["providers"][0]["module"] == "provider-anthropic"

    # Tools should have the web tool
    assert len(result["tools"]) == 1
    assert result["tools"][0]["module"] == "tool-web"


def test_deep_merge_preserves_order():
    """Test that module order is preserved during merge."""
    base = {
        "providers": [{"module": "provider-a"}, {"module": "provider-b"}, {"module": "provider-c"}]
    }
    overlay = {
        "providers": [
            {"module": "provider-b", "config": {"updated": True}},
            {"module": "provider-d"},
        ]
    }

    result = deep_merge(base, overlay)

    # Order should be: a, b (updated), c, d
    modules = [p["module"] for p in result["providers"]]
    assert modules == ["provider-a", "provider-b", "provider-c", "provider-d"]

    # provider-b should be updated
    provider_b = next(p for p in result["providers"] if p["module"] == "provider-b")
    assert provider_b.get("config", {}).get("updated") is True


def test_deep_merge_handles_non_dict_modules():
    """Test that non-dict entries in module lists are handled gracefully."""
    base = {
        "providers": [
            {"module": "provider-a"},
            "invalid_entry",  # Non-dict entry
        ]
    }
    overlay = {"providers": [{"module": "provider-b"}]}

    result = deep_merge(base, overlay)

    # Should handle gracefully, keeping valid modules
    valid_modules = [p for p in result["providers"] if isinstance(p, dict) and "module" in p]
    assert len(valid_modules) == 2
    assert valid_modules[0]["module"] == "provider-a"
    assert valid_modules[1]["module"] == "provider-b"


def test_deep_merge_empty_base_module_list():
    """Test merging when base has no modules."""
    base = {"providers": [], "tools": []}
    overlay = {"providers": [{"module": "provider-a"}], "tools": [{"module": "tool-a"}]}

    result = deep_merge(base, overlay)

    assert len(result["providers"]) == 1
    assert result["providers"][0]["module"] == "provider-a"
    assert len(result["tools"]) == 1
    assert result["tools"][0]["module"] == "tool-a"


def test_deep_merge_non_module_lists_replaced():
    """Test that non-module lists are replaced, not merged."""
    base = {
        "providers": [{"module": "provider-a"}],  # Module list - should merge
        "some_list": [1, 2, 3],  # Regular list - should replace
    }
    overlay = {
        "providers": [{"module": "provider-b"}],  # Add to providers
        "some_list": [4, 5],  # Replace some_list entirely
    }

    result = deep_merge(base, overlay)

    # Module list should be merged
    assert len(result["providers"]) == 2

    # Regular list should be replaced
    assert result["some_list"] == [4, 5]
