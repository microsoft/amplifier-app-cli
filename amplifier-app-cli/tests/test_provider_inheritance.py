"""Tests for provider inheritance from settings.yaml to child sessions.

Verifies that providers configured in settings.yaml flow correctly through:
1. resolve_bundle_config() → prepared.bundle.providers (not just mount_plan)
2. merge_configs() → child session config preserves parent providers
3. The full spawn path → child coordinator.get("providers") is non-empty

This addresses a bug where coordinator.get("providers") returned empty in
spawned agent sessions because settings.yaml providers were only injected
into prepared.mount_plan (dict) but not prepared.bundle.providers (Bundle
dataclass). PreparedBundle.spawn() reads self.bundle — not self.mount_plan —
so child sessions got zero providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from amplifier_app_cli.agent_config import merge_configs
from amplifier_app_cli.lib.merge_utils import merge_module_lists
from amplifier_app_cli.runtime.config import _sync_overrides_to_bundle


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PROVIDERS = [
    {
        "module": "provider-openai",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
        "config": {"api_key": "sk-test-key", "default_model": "gpt-4o"},
    },
    {
        "module": "provider-gemini",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-gemini@main",
        "config": {"api_key": "test-gemini-key"},
    },
    {
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {"api_key": "test-anthropic-key"},
    },
]

SAMPLE_TOOLS = [
    {"module": "tool-filesystem", "config": {"allowed_write_paths": ["."]}},
    {"module": "tool-bash", "config": {}},
]

SAMPLE_HOOKS = [
    {"module": "hooks-logging", "config": {}},
]


@dataclass
class FakeBundle:
    """Minimal Bundle dataclass stand-in for testing."""

    providers: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FakePreparedBundle:
    """Minimal PreparedBundle stand-in for testing."""

    mount_plan: dict[str, Any] = field(default_factory=dict)
    bundle: FakeBundle = field(default_factory=FakeBundle)


def _parent_config_with_providers() -> dict[str, Any]:
    """Build a realistic parent session config with providers."""
    return {
        "session": {
            "orchestrator": {"module": "loop-streaming"},
            "context": {"module": "context-persistent"},
        },
        "providers": list(SAMPLE_PROVIDERS),
        "tools": list(SAMPLE_TOOLS),
        "hooks": list(SAMPLE_HOOKS),
        "agents": {
            "explorer": {"tools": [{"module": "tool-filesystem"}]},
            "architect": {},
        },
    }


# ===========================================================================
# Tests for _sync_overrides_to_bundle
# ===========================================================================


class TestSyncOverridesToBundle:
    """Tests for the _sync_overrides_to_bundle helper."""

    def test_syncs_providers_to_bundle(self) -> None:
        """Providers from bundle_config must be copied to bundle.providers."""
        prepared = FakePreparedBundle()
        bundle_config = {"providers": list(SAMPLE_PROVIDERS)}

        _sync_overrides_to_bundle(prepared, bundle_config)

        assert len(prepared.bundle.providers) == 3
        module_names = [p["module"] for p in prepared.bundle.providers]
        assert "provider-openai" in module_names
        assert "provider-gemini" in module_names
        assert "provider-anthropic" in module_names

    def test_syncs_hooks_to_bundle(self) -> None:
        """Hooks from bundle_config must be copied to bundle.hooks."""
        prepared = FakePreparedBundle()
        bundle_config = {"hooks": list(SAMPLE_HOOKS)}

        _sync_overrides_to_bundle(prepared, bundle_config)

        assert len(prepared.bundle.hooks) == 1
        assert prepared.bundle.hooks[0]["module"] == "hooks-logging"

    def test_syncs_tools_only_when_flag_set(self) -> None:
        """Tools should only sync when sync_tools=True."""
        prepared = FakePreparedBundle()
        bundle_config = {"tools": list(SAMPLE_TOOLS)}

        # Without flag — tools NOT synced
        _sync_overrides_to_bundle(prepared, bundle_config, sync_tools=False)
        assert len(prepared.bundle.tools) == 0

        # With flag — tools synced
        _sync_overrides_to_bundle(prepared, bundle_config, sync_tools=True)
        assert len(prepared.bundle.tools) == 2

    def test_no_mutation_of_source_list(self) -> None:
        """Synced list must be a copy, not a reference to the original."""
        prepared = FakePreparedBundle()
        original_providers = list(SAMPLE_PROVIDERS)
        bundle_config = {"providers": original_providers}

        _sync_overrides_to_bundle(prepared, bundle_config)

        # Mutating the synced list should not affect the original
        prepared.bundle.providers.append({"module": "extra"})
        assert len(original_providers) == 3  # unchanged

    def test_handles_empty_providers(self) -> None:
        """Empty providers list should not sync (no-op)."""
        prepared = FakePreparedBundle(bundle=FakeBundle(providers=[{"module": "old"}]))
        bundle_config = {"providers": []}

        _sync_overrides_to_bundle(prepared, bundle_config)

        # Original providers preserved (empty list is falsy, sync skipped)
        assert len(prepared.bundle.providers) == 1
        assert prepared.bundle.providers[0]["module"] == "old"

    def test_handles_missing_bundle_attribute(self) -> None:
        """Should not crash if prepared has no bundle attribute."""
        prepared = MagicMock(spec=[])  # No attributes at all
        bundle_config = {"providers": list(SAMPLE_PROVIDERS)}

        # Should not raise
        _sync_overrides_to_bundle(prepared, bundle_config)

    def test_handles_no_providers_key(self) -> None:
        """Should not crash if bundle_config has no providers key."""
        prepared = FakePreparedBundle()
        bundle_config = {"tools": list(SAMPLE_TOOLS)}

        _sync_overrides_to_bundle(prepared, bundle_config)

        assert len(prepared.bundle.providers) == 0

    def test_overwrites_existing_bundle_providers(self) -> None:
        """Settings.yaml providers should replace whatever bundle had."""
        prepared = FakePreparedBundle(
            bundle=FakeBundle(providers=[{"module": "old-provider"}])
        )
        bundle_config = {"providers": list(SAMPLE_PROVIDERS)}

        _sync_overrides_to_bundle(prepared, bundle_config)

        assert len(prepared.bundle.providers) == 3
        assert all(p["module"] != "old-provider" for p in prepared.bundle.providers)


# ===========================================================================
# Tests for provider inheritance through merge_configs (spawn path)
# ===========================================================================


class TestProviderInheritanceViaMerge:
    """Verify providers survive the merge_configs path used by spawn_sub_session."""

    def test_child_inherits_parent_providers_when_agent_has_none(self) -> None:
        """Agent with no providers declaration should inherit all parent providers."""
        parent = _parent_config_with_providers()
        agent_overlay: dict[str, Any] = {
            "session": {
                "orchestrator": {"module": "loop-streaming"},
                "context": {"module": "context-simple"},
            },
        }

        merged = merge_configs(parent, agent_overlay)

        assert "providers" in merged
        assert len(merged["providers"]) == 3
        module_names = [p["module"] for p in merged["providers"]]
        assert "provider-openai" in module_names
        assert "provider-gemini" in module_names
        assert "provider-anthropic" in module_names

    def test_child_inherits_parent_providers_with_empty_overlay(self) -> None:
        """Empty agent overlay should preserve all parent providers."""
        parent = _parent_config_with_providers()
        agent_overlay: dict[str, Any] = {}

        merged = merge_configs(parent, agent_overlay)

        assert len(merged["providers"]) == 3

    def test_child_can_add_provider(self) -> None:
        """Agent that declares an extra provider should get parent + own."""
        parent = _parent_config_with_providers()
        agent_overlay = {
            "session": {
                "orchestrator": {"module": "loop-streaming"},
                "context": {"module": "context-simple"},
            },
            "providers": [
                {"module": "provider-ollama", "config": {"host": "localhost:11434"}},
            ],
        }

        merged = merge_configs(parent, agent_overlay)

        assert len(merged["providers"]) == 4
        module_names = [p["module"] for p in merged["providers"]]
        assert "provider-ollama" in module_names
        assert "provider-openai" in module_names

    def test_child_can_override_provider_config(self) -> None:
        """Agent that redeclares a provider should merge config (child wins)."""
        parent = _parent_config_with_providers()
        agent_overlay = {
            "session": {
                "orchestrator": {"module": "loop-streaming"},
                "context": {"module": "context-simple"},
            },
            "providers": [
                {
                    "module": "provider-openai",
                    "config": {"default_model": "gpt-5"},
                },
            ],
        }

        merged = merge_configs(parent, agent_overlay)

        # Still 3 providers (no duplication)
        assert len(merged["providers"]) == 3
        openai = next(
            p for p in merged["providers"] if p["module"] == "provider-openai"
        )
        # Child's model override wins
        assert openai["config"]["default_model"] == "gpt-5"
        # Parent's api_key preserved (deep merge)
        assert openai["config"]["api_key"] == "sk-test-key"


# ===========================================================================
# Tests for merge_module_lists (low-level provider merge)
# ===========================================================================


class TestMergeModuleListsProviders:
    """Verify merge_module_lists handles provider lists correctly."""

    def test_empty_overlay_preserves_base(self) -> None:
        """Empty overlay should return base providers unchanged."""
        result = merge_module_lists(SAMPLE_PROVIDERS, [])
        assert len(result) == 3

    def test_empty_base_returns_overlay(self) -> None:
        """Empty base should return overlay providers."""
        overlay = [{"module": "provider-openai", "config": {"model": "gpt-5"}}]
        result = merge_module_lists([], overlay)
        assert len(result) == 1
        assert result[0]["config"]["model"] == "gpt-5"

    def test_both_empty_returns_empty(self) -> None:
        """Both empty should return empty list."""
        result = merge_module_lists([], [])
        assert len(result) == 0

    def test_same_module_merges_config(self) -> None:
        """Same module ID in both should deep merge (overlay wins)."""
        base = [
            {"module": "provider-openai", "config": {"model": "gpt-4o", "key": "a"}}
        ]
        overlay = [{"module": "provider-openai", "config": {"model": "gpt-5"}}]
        result = merge_module_lists(base, overlay)
        assert len(result) == 1
        assert result[0]["config"]["model"] == "gpt-5"

    def test_different_modules_combine(self) -> None:
        """Different module IDs should be combined."""
        base = [{"module": "provider-openai"}]
        overlay = [{"module": "provider-gemini"}]
        result = merge_module_lists(base, overlay)
        assert len(result) == 2
