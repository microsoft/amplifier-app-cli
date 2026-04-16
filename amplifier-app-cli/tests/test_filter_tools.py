"""Tests for _filter_tools in session_spawner.

Regression tests for the delegate-tool exclusion name/module mismatch bug.

Root cause (confirmed from session ac0a19aa, 170 contexts / 22 levels deep):
  _filter_tools() compares t.get("module") against exclude_tools.
  The tool's module name is "tool-delegate" but agents.yaml historically
  passed "delegate" (the tool *name*) — these strings never matched, so
  the delegate tool was never excluded from child sessions.

Fix: both agents.yaml and the fallback default in DelegateTool.__init__
     now use "tool-delegate" (the module name).
"""

from amplifier_app_cli.session_spawner import _filter_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(*module_names: str) -> dict:
    """Build a minimal session config with tools named by module."""
    return {
        "tools": [
            {"module": name, "source": f"git+https://example.com/{name}"}
            for name in module_names
        ]
    }


def _tool_modules(config: dict) -> list[str]:
    """Return the list of module names from config tools."""
    return [t["module"] for t in config.get("tools", [])]


# ---------------------------------------------------------------------------
# Core exclusion behaviour
# ---------------------------------------------------------------------------


class TestExcludeToolsBlocklist:
    def test_correct_module_name_excludes_delegate_tool(self):
        """The fix: 'tool-delegate' (module name) correctly strips the delegate tool."""
        config = _make_config("tool-delegate", "tool-filesystem", "tool-bash")
        result = _filter_tools(config, {"exclude_tools": ["tool-delegate"]})
        modules = _tool_modules(result)

        assert "tool-delegate" not in modules, (
            "tool-delegate should be excluded when exclude_tools=['tool-delegate']"
        )
        # Other tools must survive
        assert "tool-filesystem" in modules
        assert "tool-bash" in modules

    def test_wrong_tool_name_does_not_exclude_delegate_tool(self):
        """Regression: 'delegate' (tool *name*, not module name) does NOT exclude tool-delegate.

        This documents the pre-fix broken behaviour so we catch any future
        attempt to revert to the old pattern.  The filter compares module names;
        passing 'delegate' instead of 'tool-delegate' is a no-op.
        """
        config = _make_config("tool-delegate", "tool-filesystem")
        result = _filter_tools(config, {"exclude_tools": ["delegate"]})
        modules = _tool_modules(result)

        assert "tool-delegate" in modules, (
            "Using the wrong name 'delegate' (not the module name 'tool-delegate') "
            "should leave the tool in place — this is the pre-fix broken behaviour."
        )

    def test_empty_exclude_list_keeps_all_tools(self):
        """Empty exclusion list is a no-op — all tools inherited."""
        config = _make_config("tool-delegate", "tool-filesystem", "tool-bash")
        result = _filter_tools(config, {"exclude_tools": []})
        assert _tool_modules(result) == [
            "tool-delegate",
            "tool-filesystem",
            "tool-bash",
        ]

    def test_no_tools_key_returns_config_unchanged(self):
        """Config without a 'tools' key is returned as-is."""
        config = {"providers": [{"module": "provider-anthropic"}]}
        result = _filter_tools(config, {"exclude_tools": ["tool-delegate"]})
        assert result is config  # Same object — no copying


# ---------------------------------------------------------------------------
# Explicit agent tool override
# ---------------------------------------------------------------------------


class TestExplicitAgentToolsPreserved:
    def test_explicit_tool_survives_exclusion(self):
        """Agent that explicitly declares tool-delegate gets it even when it's excluded.

        This is the intentional override mechanism: agents that need delegation
        capability declare module: tool-delegate in their own tools list.
        """
        config = _make_config("tool-delegate", "tool-filesystem")
        result = _filter_tools(
            config,
            {"exclude_tools": ["tool-delegate"]},
            agent_explicit_tools=["tool-delegate"],
        )
        modules = _tool_modules(result)

        assert "tool-delegate" in modules, (
            "Explicitly declared tool must survive exclusion (the override mechanism)"
        )
        assert "tool-filesystem" in modules

    def test_non_explicit_tools_still_excluded(self):
        """Exclusion still works for tools not in the explicit list."""
        config = _make_config("tool-delegate", "tool-filesystem", "tool-bash")
        result = _filter_tools(
            config,
            {"exclude_tools": ["tool-delegate", "tool-bash"]},
            agent_explicit_tools=["tool-delegate"],  # only re-allows delegate
        )
        modules = _tool_modules(result)

        assert "tool-delegate" in modules  # explicitly kept
        assert "tool-bash" not in modules  # excluded and not in explicit list
        assert "tool-filesystem" in modules  # never excluded

    def test_none_explicit_tools_treated_as_empty(self):
        """agent_explicit_tools=None is the same as an empty list."""
        config = _make_config("tool-delegate")
        result = _filter_tools(
            config,
            {"exclude_tools": ["tool-delegate"]},
            agent_explicit_tools=None,
        )
        assert "tool-delegate" not in _tool_modules(result)


# ---------------------------------------------------------------------------
# Allowlist (inherit_tools) mode
# ---------------------------------------------------------------------------


class TestInheritToolsAllowlist:
    def test_allowlist_keeps_only_specified_tools(self):
        """inherit_tools allowlist strips everything not named."""
        config = _make_config("tool-delegate", "tool-filesystem", "tool-bash")
        result = _filter_tools(config, {"inherit_tools": ["tool-filesystem"]})
        modules = _tool_modules(result)

        assert modules == ["tool-filesystem"]

    def test_allowlist_explicit_tool_included_even_if_not_in_allowlist(self):
        """Explicit agent tools survive allowlist mode too."""
        config = _make_config("tool-delegate", "tool-filesystem", "tool-bash")
        result = _filter_tools(
            config,
            {"inherit_tools": ["tool-filesystem"]},
            agent_explicit_tools=["tool-delegate"],
        )
        modules = _tool_modules(result)

        assert "tool-filesystem" in modules  # in allowlist
        assert "tool-delegate" in modules  # explicit override
        assert "tool-bash" not in modules  # stripped


# ---------------------------------------------------------------------------
# No-filter / passthrough
# ---------------------------------------------------------------------------


class TestNoFiltering:
    def test_empty_inheritance_dict_returns_config_unchanged(self):
        """No exclude or inherit key means no filtering at all."""
        config = _make_config("tool-delegate", "tool-filesystem")
        result = _filter_tools(config, {})
        assert result is config  # Same object — no copying

    def test_empty_tools_list_returns_config_unchanged(self):
        """Config with empty tools list is returned as-is."""
        config = {"tools": []}
        result = _filter_tools(config, {"exclude_tools": ["tool-delegate"]})
        assert result is config
