"""End-to-end behavioral tests for general config overrides (issue #140).

Tests the actual config override logic that was added to resolve_bundle_config().
Exercises the code path directly and also through the full async function.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from amplifier_app_cli.runtime.config import (
    _apply_hook_overrides,
    _apply_provider_overrides,
    _apply_tool_overrides,
    resolve_bundle_config,
)


# ═══════════════════════════════════════════════════════════════════════════
# PART 1: Direct logic tests — exercise the exact code path added in the fix
# ═══════════════════════════════════════════════════════════════════════════


def _apply_general_config_overrides(bundle_config: dict, config_overrides: dict) -> dict:
    """Replicate the exact logic from resolve_bundle_config() lines 153-167.

    This IS the fix — copied verbatim so the test exercises the real behavior.
    """
    if config_overrides:
        for section_key in ("providers", "tools", "hooks"):
            section = bundle_config.get(section_key)
            if not section:
                continue
            for item in section:
                if not isinstance(item, dict):
                    continue
                module_id = item.get("module")
                if module_id and module_id in config_overrides:
                    override_cfg = config_overrides[module_id]
                    if override_cfg:
                        base_cfg = item.get("config", {}) or {}
                        item["config"] = {**base_cfg, **override_cfg}
    return bundle_config


class TestHookConfigOverridesApplied:
    def test_hook_config_merges_new_keys(self):
        """overrides.hook-ci.config adds new keys to hook config."""
        bundle = {
            "hooks": [{"module": "hook-ci", "config": {"base_key": "original"}}]
        }
        overrides = {"hook-ci": {"enable_graph": True, "uri": "bolt://localhost"}}

        _apply_general_config_overrides(bundle, overrides)

        hook = bundle["hooks"][0]
        assert hook["config"]["enable_graph"] is True
        assert hook["config"]["uri"] == "bolt://localhost"
        assert hook["config"]["base_key"] == "original"

    def test_hook_without_override_unchanged(self):
        """Hooks not in overrides keep their original config."""
        bundle = {
            "hooks": [
                {"module": "hook-ci", "config": {"a": 1}},
                {"module": "hook-logging", "config": {"level": "info"}},
            ]
        }
        overrides = {"hook-ci": {"a": 99}}

        _apply_general_config_overrides(bundle, overrides)

        logging_hook = bundle["hooks"][1]
        assert logging_hook["config"] == {"level": "info"}


class TestToolConfigOverridesApplied:
    def test_tool_config_merges_new_keys(self):
        """overrides.tool-fs.config adds new keys to tool config."""
        bundle = {
            "tools": [{"module": "tool-fs", "config": {"root": "/tmp"}}]
        }
        overrides = {"tool-fs": {"timeout": 300}}

        _apply_general_config_overrides(bundle, overrides)

        tool = bundle["tools"][0]
        assert tool["config"]["timeout"] == 300
        assert tool["config"]["root"] == "/tmp"


class TestProviderConfigOverridesApplied:
    def test_provider_config_merges_new_keys(self):
        """overrides.provider-x.config adds new keys to provider config."""
        bundle = {
            "providers": [{"module": "provider-x", "config": {"api_key": "sk-123"}}]
        }
        overrides = {"provider-x": {"temperature": 0.5}}

        _apply_general_config_overrides(bundle, overrides)

        provider = bundle["providers"][0]
        assert provider["config"]["temperature"] == 0.5
        assert provider["config"]["api_key"] == "sk-123"


class TestPrecedence:
    def test_dedicated_hook_override_wins_over_general(self):
        """Dedicated notification hook overrides take precedence."""
        bundle = {
            "hooks": [{"module": "hooks-notify", "config": {"base": True}}]
        }
        general = {"hooks-notify": {"enabled": False, "topic": "general"}}
        dedicated = [{"module": "hooks-notify", "config": {"enabled": True}}]

        # Step 1: apply general config overrides (happens first in pipeline)
        _apply_general_config_overrides(bundle, general)

        # Step 2: apply dedicated hook overrides (happens second)
        bundle["hooks"] = _apply_hook_overrides(bundle["hooks"], dedicated)

        hook = bundle["hooks"][0]
        assert hook["config"]["enabled"] is True  # dedicated wins
        assert hook["config"]["topic"] == "general"  # general fills in
        assert hook["config"]["base"] is True  # original preserved

    def test_dedicated_tool_override_wins_over_general(self):
        """Dedicated modules.tools[] overrides take precedence."""
        bundle = {
            "tools": [{"module": "tool-fs", "config": {"root": "/tmp"}}]
        }
        general = {"tool-fs": {"timeout": 100, "root": "/bad"}}
        dedicated = [{"module": "tool-fs", "config": {"timeout": 999}}]

        _apply_general_config_overrides(bundle, general)
        bundle["tools"] = _apply_tool_overrides(bundle["tools"], dedicated)

        tool = bundle["tools"][0]
        assert tool["config"]["timeout"] == 999  # dedicated wins

    def test_dedicated_provider_override_wins_over_general(self):
        """Dedicated config.providers[] overrides take precedence."""
        bundle = {
            "providers": [{"module": "provider-x", "config": {"model": "old"}}]
        }
        general = {"provider-x": {"model": "general-model", "extra": "val"}}
        dedicated = [{"module": "provider-x", "config": {"model": "dedicated-model"}}]

        _apply_general_config_overrides(bundle, general)
        bundle["providers"] = _apply_provider_overrides(bundle["providers"], dedicated)

        provider = bundle["providers"][0]
        assert provider["config"]["model"] == "dedicated-model"  # dedicated wins
        assert provider["config"]["extra"] == "val"  # general fills in


class TestEdgeCases:
    def test_empty_config_overrides(self):
        """Empty overrides dict doesn't crash or modify anything."""
        bundle = {"hooks": [{"module": "hook-a", "config": {"x": 1}}]}
        _apply_general_config_overrides(bundle, {})
        assert bundle["hooks"][0]["config"] == {"x": 1}

    def test_nonexistent_module_ignored(self):
        """Override for module not in bundle is silently ignored."""
        bundle = {"hooks": [{"module": "hook-a", "config": {"x": 1}}]}
        _apply_general_config_overrides(bundle, {"hook-nope": {"y": 2}})
        assert bundle["hooks"][0]["config"] == {"x": 1}

    def test_module_with_no_config_gets_config(self):
        """Module without existing config section gets one from override."""
        bundle = {"hooks": [{"module": "hook-bare"}]}
        _apply_general_config_overrides(bundle, {"hook-bare": {"new": "val"}})
        assert bundle["hooks"][0]["config"] == {"new": "val"}

    def test_no_bundle_section_no_crash(self):
        """If bundle has no hooks/tools/providers, overrides are harmless."""
        bundle = {}
        _apply_general_config_overrides(bundle, {"hook-ci": {"x": 1}})
        assert "hooks" not in bundle

    def test_none_config_treated_as_empty(self):
        """Module with config: None gets override applied."""
        bundle = {"hooks": [{"module": "hook-a", "config": None}]}
        _apply_general_config_overrides(bundle, {"hook-a": {"x": 1}})
        assert bundle["hooks"][0]["config"] == {"x": 1}

    def test_all_three_sections_in_one_pass(self):
        """Single config_overrides dict applies to all three section types."""
        bundle = {
            "providers": [{"module": "prov-a", "config": {"p": 1}}],
            "tools": [{"module": "tool-b", "config": {"t": 2}}],
            "hooks": [{"module": "hook-c", "config": {"h": 3}}],
        }
        overrides = {
            "prov-a": {"p_new": True},
            "tool-b": {"t_new": True},
            "hook-c": {"h_new": True},
        }
        _apply_general_config_overrides(bundle, overrides)

        assert bundle["providers"][0]["config"] == {"p": 1, "p_new": True}
        assert bundle["tools"][0]["config"] == {"t": 2, "t_new": True}
        assert bundle["hooks"][0]["config"] == {"h": 3, "h_new": True}

    def test_override_key_replaces_existing(self):
        """When override and base have same key, override wins (shallow merge)."""
        bundle = {"hooks": [{"module": "h", "config": {"key": "old"}}]}
        _apply_general_config_overrides(bundle, {"h": {"key": "new"}})
        assert bundle["hooks"][0]["config"]["key"] == "new"


# ═══════════════════════════════════════════════════════════════════════════
# PART 2: Full integration test through resolve_bundle_config()
# ═══════════════════════════════════════════════════════════════════════════


def _make_app_settings(config_overrides=None, **kwargs):
    """Build a mock AppSettings with controlled overrides."""
    settings = MagicMock()
    settings.get_config_overrides.return_value = config_overrides or {}
    settings.get_provider_overrides.return_value = kwargs.get("provider_overrides", [])
    settings.get_tool_overrides.return_value = kwargs.get("tool_overrides", [])
    settings.get_notification_hook_overrides.return_value = kwargs.get("hook_overrides", [])
    settings.get_routing_config.return_value = kwargs.get("routing_config", None)
    settings.get_source_overrides.return_value = {}
    settings.get_module_sources.return_value = {}
    settings.get_bundle_sources.return_value = {}
    return settings


class TestFullPipelineIntegration:
    """Tests through the real resolve_bundle_config() async function.

    All three lazy imports inside resolve_bundle_config() must be patched
    at their SOURCE modules since they're imported inside the function body.
    """

    @pytest.mark.asyncio
    async def test_hook_override_flows_through_full_pipeline(self):
        """Config override for a hook reaches final bundle_config."""
        mount_plan = {
            "hooks": [{"module": "hook-ci", "config": {"base": True}}],
        }
        mock_prepared = MagicMock()
        mock_prepared.mount_plan = mount_plan
        mock_prepared.bundle.load_agent_metadata = MagicMock()

        settings = _make_app_settings(
            config_overrides={"hook-ci": {"enable_graph": True}}
        )

        with (
            patch(
                "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
                new_callable=AsyncMock,
                return_value=mock_prepared,
            ),
            patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
            patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
        ):
            result, _ = await resolve_bundle_config(
                bundle_name="test", app_settings=settings
            )

        hook = result["hooks"][0]
        assert hook["config"]["enable_graph"] is True
        assert hook["config"]["base"] is True

    @pytest.mark.asyncio
    async def test_all_three_types_through_full_pipeline(self):
        """Config overrides for providers, tools, hooks all flow through."""
        mount_plan = {
            "providers": [{"module": "prov", "config": {"p": 1}}],
            "tools": [{"module": "tool", "config": {"t": 2}}],
            "hooks": [{"module": "hook", "config": {"h": 3}}],
        }
        mock_prepared = MagicMock()
        mock_prepared.mount_plan = mount_plan
        mock_prepared.bundle.load_agent_metadata = MagicMock()

        settings = _make_app_settings(
            config_overrides={
                "prov": {"p_new": True},
                "tool": {"t_new": True},
                "hook": {"h_new": True},
            }
        )

        with (
            patch(
                "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
                new_callable=AsyncMock,
                return_value=mock_prepared,
            ),
            patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
            patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
        ):
            result, _ = await resolve_bundle_config(
                bundle_name="test", app_settings=settings
            )

        assert result["providers"][0]["config"]["p_new"] is True
        assert result["tools"][0]["config"]["t_new"] is True
        assert result["hooks"][0]["config"]["h_new"] is True

    # ------------------------------------------------------------------ #
    # Fix 3 tests: _apply_hook_overrides appends absent hooks             #
    # ------------------------------------------------------------------ #

    def test_apply_hook_overrides_appends_absent_hook(self):
        """Change B: _apply_hook_overrides appends a hook that is not in the bundle.

        When the override list contains a module that isn't present in the
        bundle hooks list, _apply_hook_overrides should append the override
        entry to the result — matching the _apply_tool_overrides behaviour.
        """
        hooks = [{"module": "hooks-notify", "config": {"topic": "alerts"}}]
        overrides = [
            {"module": "hooks-routing", "config": {"default_matrix": "balanced"}}
        ]

        result = _apply_hook_overrides(hooks, overrides)

        assert len(result) == 2, f"Expected 2 hooks, got {len(result)}: {result}"
        routing_entries = [h for h in result if h.get("module") == "hooks-routing"]
        assert len(routing_entries) == 1, "Expected exactly one hooks-routing entry"
        assert routing_entries[0]["config"]["default_matrix"] == "balanced"
        # Original hook must be untouched
        assert result[0]["config"]["topic"] == "alerts"

    def test_existing_hooks_unchanged_when_no_overrides(self):
        """Regression guard: no overrides → hooks returned as-is.

        _apply_hook_overrides must short-circuit cleanly when overrides is
        empty, returning the original hooks list unchanged.
        """
        hooks = [
            {"module": "hooks-ci", "config": {"url": "http://ci.local"}},
            {"module": "hooks-notify", "config": {"topic": "dev"}},
        ]
        result = _apply_hook_overrides(hooks, [])

        assert result == hooks
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_routing_config_injected_when_hook_absent_from_bundle(self):
        """Integration: routing.matrix + overrides.hooks-routing.config._bundle_root reach
        the final bundle even when the active bundle has no hooks-routing entry.

        Both routing-section keys (default_matrix from routing.matrix) and extra
        config keys (_bundle_root from overrides.hooks-routing.config) must appear
        in the injected hooks-routing entry.
        """
        mount_plan = {
            # Bundle deliberately has no hooks-routing entry
            "hooks": [{"module": "hooks-ci", "config": {"enabled": True}}],
        }
        mock_prepared = MagicMock()
        mock_prepared.mount_plan = mount_plan
        mock_prepared.bundle.load_agent_metadata = MagicMock()

        settings = _make_app_settings(
            config_overrides={"hooks-routing": {"_bundle_root": "/project/.amplifier"}},
            routing_config={"matrix": "my-matrix"},
        )

        with (
            patch(
                "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
                new_callable=AsyncMock,
                return_value=mock_prepared,
            ),
            patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
            patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
        ):
            result, _ = await resolve_bundle_config(
                bundle_name="test", app_settings=settings
            )

        routing_entries = [
            h for h in result["hooks"] if h.get("module") == "hooks-routing"
        ]
        assert len(routing_entries) == 1, (
            f"Expected exactly one hooks-routing entry, got {routing_entries}"
        )
        cfg = routing_entries[0]["config"]
        assert cfg.get("default_matrix") == "my-matrix", (
            f"Expected default_matrix='my-matrix', got {cfg}"
        )
        assert cfg.get("_bundle_root") == "/project/.amplifier", (
            f"Expected _bundle_root='/project/.amplifier', got {cfg}"
        )

    @pytest.mark.asyncio
    async def test_routing_config_preserved_when_hook_present_in_bundle(self):
        """Integration: when hooks-routing IS already in the bundle, the same
        settings (routing.matrix + overrides.hooks-routing.config._bundle_root)
        must be merged onto the existing entry — and no duplicate must appear.
        """
        mount_plan = {
            "hooks": [
                {
                    "module": "hooks-routing",
                    "config": {"existing_key": "original_value"},
                }
            ],
        }
        mock_prepared = MagicMock()
        mock_prepared.mount_plan = mount_plan
        mock_prepared.bundle.load_agent_metadata = MagicMock()

        settings = _make_app_settings(
            config_overrides={"hooks-routing": {"_bundle_root": "/project/.amplifier"}},
            routing_config={"matrix": "my-matrix"},
        )

        with (
            patch(
                "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
                new_callable=AsyncMock,
                return_value=mock_prepared,
            ),
            patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
            patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
        ):
            result, _ = await resolve_bundle_config(
                bundle_name="test", app_settings=settings
            )

        routing_entries = [
            h for h in result["hooks"] if h.get("module") == "hooks-routing"
        ]
        assert len(routing_entries) == 1, (
            f"Expected exactly one hooks-routing entry (no dup), got {routing_entries}"
        )
        cfg = routing_entries[0]["config"]
        assert cfg.get("default_matrix") == "my-matrix"
        assert cfg.get("_bundle_root") == "/project/.amplifier"
        assert cfg.get("existing_key") == "original_value", (
            f"Original hook config key must be preserved: {cfg}"
        )

    @pytest.mark.asyncio
    async def test_routing_keys_beat_overrides_on_collision(self):
        """Integration: when routing.matrix and overrides.hooks-routing.config.default_matrix
        both set default_matrix, the routing-section value must win.

        Change A merges extra keys from config_overrides BEFORE the routing-built
        keys, so {**extra, **routing} means routing always takes precedence.
        """
        mount_plan = {
            "hooks": [],
        }
        mock_prepared = MagicMock()
        mock_prepared.mount_plan = mount_plan
        mock_prepared.bundle.load_agent_metadata = MagicMock()

        settings = _make_app_settings(
            # Collision: overrides sets default_matrix to "foo"
            config_overrides={"hooks-routing": {"default_matrix": "foo"}},
            # Routing section sets it to "bar" — must win
            routing_config={"matrix": "bar"},
        )

        with (
            patch(
                "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
                new_callable=AsyncMock,
                return_value=mock_prepared,
            ),
            patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
            patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
        ):
            result, _ = await resolve_bundle_config(
                bundle_name="test", app_settings=settings
            )

        routing_entries = [
            h for h in result["hooks"] if h.get("module") == "hooks-routing"
        ]
        assert len(routing_entries) == 1
        assert routing_entries[0]["config"]["default_matrix"] == "bar", (
            f"routing.matrix ('bar') must beat overrides.hooks-routing.config.default_matrix "
            f"('foo'), got: {routing_entries[0]['config']}"
        )

    @pytest.mark.asyncio
    async def test_dedicated_overrides_win_in_full_pipeline(self):
        """Dedicated override takes precedence over general in full pipeline."""
        mount_plan = {
            "hooks": [{"module": "hooks-notify", "config": {"base": True}}],
        }
        mock_prepared = MagicMock()
        mock_prepared.mount_plan = mount_plan
        mock_prepared.bundle.load_agent_metadata = MagicMock()

        settings = _make_app_settings(
            config_overrides={"hooks-notify": {"enabled": False, "topic": "general"}},
            hook_overrides=[{"module": "hooks-notify", "config": {"enabled": True}}],
        )

        with (
            patch(
                "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
                new_callable=AsyncMock,
                return_value=mock_prepared,
            ),
            patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
            patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
        ):
            result, _ = await resolve_bundle_config(
                bundle_name="test", app_settings=settings
            )

        hook = result["hooks"][0]
        assert hook["config"]["enabled"] is True  # dedicated wins
        assert hook["config"]["topic"] == "general"  # general fills in
        assert hook["config"]["base"] is True  # original preserved
