"""`overrides.<module-id>.config` must reach `session.context` / `session.orchestrator`.

Why this file exists
--------------------
`resolve_bundle_config()` walked `providers`, `tools` and `hooks` -- at the
bundle root and inside each agent -- and nothing else. The context manager and
the orchestrator are declared at `session.context` and `session.orchestrator`,
single module entries rather than lists, so the walk never visited them.

The consequence was total, not partial: **no** context-manager or orchestrator
setting could be overridden from `settings.yaml`. Writing

    overrides:
      context-simple:
        config:
          max_tokens: 500000

did nothing at all, silently -- no error, no warning, no effect.

That contradicted the rule the override loop is built on, stated in
`_apply_config_overrides_to_section`'s own docstring: "`overrides.<id>.config`
is keyed by module identity, not by mount location, so it must reach a module
wherever it's declared." Being a dict instead of a list is a mount-location
accident, which is precisely what that rule exists to rule out.

These tests exercise the real `_apply_config_overrides_to_entry` seam rather
than a copy of its logic, so they fail if the implementation drifts.
"""

from __future__ import annotations

from amplifier_app_cli.runtime.config import (
    _apply_config_overrides_to_entry,
    _apply_config_overrides_to_section,
)


def _context_entry(**config) -> dict:
    entry = {
        "module": "context-simple",
        "source": "git+https://github.com/microsoft/amplifier-module-context-simple@main",
    }
    if config:
        entry["config"] = config
    return entry


# ---------------------------------------------------------------------------
# 1. The defect, directly.
# ---------------------------------------------------------------------------


def test_override_reaches_a_session_context_entry():
    """The case that was silently ignored before."""
    entry = _context_entry()
    overrides = {"context-simple": {"max_tokens": 500_000}}

    result = _apply_config_overrides_to_entry(entry, overrides)

    assert result["config"]["max_tokens"] == 500_000


def test_override_reaches_an_orchestrator_entry():
    entry = {"module": "loop-streaming", "config": {"extended_thinking": True}}
    overrides = {"loop-streaming": {"budget_warn_ratio": 0.5}}

    result = _apply_config_overrides_to_entry(entry, overrides)

    assert result["config"]["budget_warn_ratio"] == 0.5
    assert result["config"]["extended_thinking"] is True


# ---------------------------------------------------------------------------
# 2. Merge semantics match the list path exactly.
# ---------------------------------------------------------------------------


def test_override_merges_with_existing_config_rather_than_replacing_it():
    entry = _context_entry(max_tokens_fallback=300_000, compact_threshold=0.8)
    overrides = {"context-simple": {"max_tokens": 500_000}}

    result = _apply_config_overrides_to_entry(entry, overrides)

    assert result["config"] == {
        "max_tokens_fallback": 300_000,
        "compact_threshold": 0.8,
        "max_tokens": 500_000,
    }


def test_override_wins_on_a_key_conflict():
    entry = _context_entry(max_tokens=300_000)
    overrides = {"context-simple": {"max_tokens": 500_000}}

    result = _apply_config_overrides_to_entry(entry, overrides)

    assert result["config"]["max_tokens"] == 500_000


def test_non_config_keys_are_preserved():
    entry = _context_entry(max_tokens=1)
    overrides = {"context-simple": {"max_tokens": 2}}

    result = _apply_config_overrides_to_entry(entry, overrides)

    assert result["module"] == "context-simple"
    assert result["source"] == entry["source"]


def test_entry_and_section_paths_agree():
    """One definition of "apply an override", reached two ways."""
    entry = _context_entry(compact_threshold=0.8)
    overrides = {"context-simple": {"max_tokens": 500_000}}

    via_entry = _apply_config_overrides_to_entry(entry, overrides)
    via_section = _apply_config_overrides_to_section([entry], overrides)[0]

    assert via_entry == via_section


# ---------------------------------------------------------------------------
# 3. Non-matches stay untouched.
# ---------------------------------------------------------------------------


def test_unrelated_override_leaves_the_entry_identical():
    entry = _context_entry(max_tokens=300_000)

    result = _apply_config_overrides_to_entry(
        entry, {"some-other-module": {"whatever": 1}}
    )

    assert result is entry


def test_empty_overrides_leave_the_entry_identical():
    entry = _context_entry(max_tokens=300_000)

    assert _apply_config_overrides_to_entry(entry, {}) is entry


def test_bare_string_entry_is_tolerated():
    """Shorthand `context: context-simple` must not crash the walk."""
    result = _apply_config_overrides_to_entry(
        "context-simple", {"context-simple": {"max_tokens": 500_000}}
    )

    assert result["module"] == "context-simple"
    assert result["config"]["max_tokens"] == 500_000
