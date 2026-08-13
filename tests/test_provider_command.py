"""Tests for the /provider command: pin/unpin the conversation-scope provider
mid-session via the orchestrator's 'conversation.provider_pin' capability.

See amplifier_module_loop_streaming.ConversationProviderPin for the capability
contract this command is built against. This app layer only asks and reports
-- it never selects a provider itself (REQUIRED BEHAVIORS in the task spec).
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import _make_command_processor


def _make_provider(model=None, priority=None, config_priority=None):
    """Build a mock Provider-protocol object for display-only reads.

    - model: value returned via get_info().defaults["model"]
    - priority: sets a `.priority` attribute directly (highest precedence,
      mirrors amplifier_module_loop_streaming._select_provider's own read
      order)
    - config_priority: sets `.config = {"priority": N}` instead
    """
    provider = MagicMock()
    info = SimpleNamespace(defaults={"model": model} if model else {})
    provider.get_info.return_value = info

    # MagicMock auto-creates attributes, so hasattr(provider, "priority") is
    # always True unless we explicitly remove it to test the fallback paths.
    if priority is not None:
        provider.priority = priority
    else:
        del provider.priority

    if config_priority is not None:
        provider.config = {"priority": config_priority}
    else:
        provider.config = {}

    return provider


def _make_pin(available=None, current=None, pin_side_effect=None):
    """Build a mock 'conversation.provider_pin' capability object."""
    pin = MagicMock()
    pin.available.return_value = available or []
    pin.current.return_value = current
    pin.unpin.return_value = None  # idempotent-unpin default; override per-test
    if pin_side_effect is not None:
        pin.pin.side_effect = pin_side_effect
    return pin


def _visible(text: str) -> str:
    """The text a user actually sees, with Rich markup stripped.

    The transition messages are the only /provider strings carrying markup
    (they render dim -- see CommandProcessor._dim), so assertions about
    wording and line width must measure the visible text, not the tags.
    """
    from rich.markup import render

    return render(text).plain


def _cp_with(pin=None, providers=None, orchestrator=None):
    """CommandProcessor whose coordinator returns `pin` for
    get_capability('conversation.provider_pin') and `providers` for
    get('providers'), with an optional orchestrator name in coordinator.config.
    """
    cp = _make_command_processor()
    coordinator = cp.session.coordinator

    def _get_capability(key):
        if key == "conversation.provider_pin":
            return pin
        return None

    coordinator.get_capability = _get_capability

    def _get(key):
        if key == "providers":
            return providers or {}
        return None

    coordinator.get = _get

    coordinator.config = (
        {"session": {"orchestrator": orchestrator}} if orchestrator else {}
    )
    return cp


# === Capability absent: refuse loudly, never report success ===


class TestCapabilityAbsent:
    @pytest.mark.asyncio
    async def test_status_names_orchestrator_and_says_restart_required(self):
        cp = _cp_with(pin=None, providers={}, orchestrator="loop-basic")
        result = await cp._handle_provider("")
        assert "loop-basic" in result
        assert "conversation.provider_pin" in result
        assert "not registered" in result
        assert "restarting" in result

    @pytest.mark.asyncio
    async def test_status_without_known_orchestrator_name_still_refuses(self):
        cp = _cp_with(pin=None, providers={})
        result = await cp._handle_provider("")
        assert "not supported" in result
        assert "restarting" in result

    @pytest.mark.asyncio
    async def test_use_refuses_and_never_calls_pin(self):
        cp = _cp_with(pin=None, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("use anthropic-fable")
        assert "not registered" in result
        assert "restarting" in result
        # No success language anywhere in the refusal.
        assert "Pinned" not in result

    @pytest.mark.asyncio
    async def test_auto_refuses_and_never_claims_unpin(self):
        cp = _cp_with(pin=None, providers={})
        result = await cp._handle_provider("auto")
        assert "not registered" in result
        assert "Unpinned" not in result

    @pytest.mark.asyncio
    async def test_status_still_lists_mounted_providers_when_capability_absent(self):
        """Even without pinning support, /provider is useful for seeing what's
        mounted -- that read comes straight from the kernel 'providers' mount
        point and doesn't require the capability."""
        cp = _cp_with(
            pin=None,
            providers={"anthropic-fable": _make_provider(model="claude-sonnet-4-5")},
        )
        result = await cp._handle_provider("")
        assert "anthropic-fable" in result
        assert "claude-sonnet-4-5" in result


# === Status display when capability IS present ===


class TestStatusDisplay:
    @pytest.mark.asyncio
    async def test_no_providers_mounted(self):
        cp = _cp_with(pin=_make_pin(), providers={})
        result = await cp._handle_provider("")
        assert "none mounted" in result

    @pytest.mark.asyncio
    async def test_shows_model_and_priority_for_each_provider(self):
        providers = {
            "anthropic-fable": _make_provider(model="claude-sonnet-4-5", priority=1),
            "openai-gpt5": _make_provider(model="gpt-5", priority=2),
        }
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert "anthropic-fable" in result
        assert "claude-sonnet-4-5" in result
        assert "priority=1" in result
        assert "openai-gpt5" in result
        assert "gpt-5" in result
        assert "priority=2" in result

    @pytest.mark.asyncio
    async def test_unpinned_marks_priority_winner_active_and_states_automatic(self):
        providers = {
            "anthropic-fable": _make_provider(model="claude-sonnet-4-5", priority=1),
            "openai-gpt5": _make_provider(model="gpt-5", priority=2),
        }
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert "automatic" in result
        assert "active by priority" in result
        # The lower-priority-number provider is the one marked active.
        lines = result.splitlines()
        winner_line = next(line_ for line_ in lines if "anthropic-fable" in line_)
        assert "active by priority" in winner_line
        loser_line = next(line_ for line_ in lines if "openai-gpt5" in line_)
        assert "active by priority" not in loser_line

    @pytest.mark.asyncio
    async def test_pinned_marks_pinned_provider_active_not_priority_winner(self):
        providers = {
            "anthropic-fable": _make_provider(model="claude-sonnet-4-5", priority=1),
            "openai-gpt5": _make_provider(model="gpt-5", priority=2),
        }
        # openai-gpt5 is pinned despite NOT having priority-winning rank.
        pin = _make_pin(available=list(providers), current="openai-gpt5")
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert "pinned to 'openai-gpt5'" in result
        lines = result.splitlines()
        pinned_line = next(line_ for line_ in lines if "openai-gpt5" in line_)
        assert "[pinned, active]" in pinned_line
        other_line = next(line_ for line_ in lines if "anthropic-fable" in line_)
        assert "active" not in other_line

    @pytest.mark.asyncio
    async def test_falls_back_to_config_priority_when_no_priority_attr(self):
        providers = {
            "anthropic-fable": _make_provider(
                model="claude-sonnet-4-5", config_priority=5
            ),
        }
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert "priority=5" in result

    @pytest.mark.asyncio
    async def test_defaults_priority_to_100_when_unspecified(self):
        providers = {"anthropic-fable": _make_provider(model="claude-sonnet-4-5")}
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert "priority=100" in result

    @pytest.mark.asyncio
    async def test_unknown_model_shown_when_get_info_lacks_it(self):
        providers = {"anthropic-fable": _make_provider(model=None)}
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert "model=(unknown)" in result


# === /provider use <name>: pin ===


class TestProviderUse:
    @pytest.mark.asyncio
    async def test_success_calls_pin_and_acknowledges_in_past_tense(self):
        pin = _make_pin(available=["anthropic-fable"])
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("use anthropic-fable")
        pin.pin.assert_called_once_with("anthropic-fable")
        assert "pinned: anthropic-fable" in result
        # State-descriptive only. The pin is recorded synchronously and no
        # LLM call has happened, so any forward-looking phrasing here would
        # be a prediction about a call that hasn't occurred.
        assert "now using" not in result.lower()
        assert "takes effect" not in result.lower()

    @pytest.mark.asyncio
    async def test_first_use_teaches_scope_once(self):
        """Progressive disclosure: the scope/experimental context appears on
        the FIRST /provider use of a session (see TestTransitionMicrocopy for
        the full first-vs-subsequent contract)."""
        pin = _make_pin(available=["anthropic-fable"])
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("use anthropic-fable")
        assert "scope: this conversation only" in result
        assert "/provider for details" in result

    @pytest.mark.asyncio
    async def test_invalid_name_renders_clean_error_not_traceback(self):
        def _raise(name):
            raise ValueError(
                f"cannot pin conversation provider {name!r}: it is not mounted "
                f"in this session. Mounted providers: anthropic-fable"
            )

        pin = _make_pin(available=["anthropic-fable"], pin_side_effect=_raise)
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("use nonexistent-provider")
        assert "Traceback" not in result
        assert "nonexistent-provider" in result
        assert "anthropic-fable" in result  # lists what IS available

    @pytest.mark.asyncio
    async def test_missing_name_shows_usage(self):
        pin = _make_pin(available=["anthropic-fable"])
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("use")
        assert "Usage: /provider use <name>" in result
        pin.pin.assert_not_called()


# === /provider auto: unpin ===


class TestProviderAuto:
    @pytest.mark.asyncio
    async def test_success_calls_unpin_and_reports_previous(self):
        pin = _make_pin(available=["anthropic-fable"])
        pin.unpin.return_value = "anthropic-fable"
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("auto")
        pin.unpin.assert_called_once()
        assert "unpinned (was anthropic-fable)" in result

    @pytest.mark.asyncio
    async def test_nothing_pinned_says_not_pinned_not_unpinned(self):
        """THE no-op fix: reporting 'unpinned' when nothing was pinned is the
        untrue-but-plausible confirmation this feature exists to prevent."""
        pin = _make_pin(available=["anthropic-fable"])
        pin.unpin.return_value = None
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("auto")
        assert "not pinned" in result
        assert "unpinned" not in result
        assert "(was" not in result


# === Transition microcopy (approved redesign) ===
#
# Progressive disclosure: teach once per session, acknowledge thereafter.
# These are terse because three PERSISTENT surfaces already do the
# confirming -- the prompt indicator, the per-turn footer badge, and
# /provider status. The transition line is the least important of the four.
#
# The use/auto asymmetry is DELIBERATE and asserted here so it cannot be
# "tidied" into symmetry: `use` is confirmed by an indicator APPEARING one
# line down; `auto` is confirmed by one DISAPPEARING (weaker evidence) and
# destroys the only record of what was pinned, so "(was X)" earns its place.


def _stateful_pin(mounted, start=None):
    """A pin capability backed by real state, so first-vs-subsequent and the
    no-op paths can be exercised as a user would actually hit them."""
    state = {"pinned": start}
    pin = _make_pin(available=sorted(mounted))
    pin.current.side_effect = lambda: state["pinned"]

    def _pin(name):
        if name not in mounted:
            raise ValueError(
                f"cannot pin conversation provider {name!r}: it is not "
                f"mounted in this session. Mounted providers: "
                f"{', '.join(sorted(mounted))}"
            )
        state["pinned"] = name
        return name

    def _unpin():
        previous = state["pinned"]
        state["pinned"] = None
        return previous

    pin.pin.side_effect = _pin
    pin.unpin.side_effect = _unpin
    return pin, state


_TWO_PROVIDERS = ("anthropic-fable", "openai-fast")


def _cp_stateful(start=None):
    providers = {name: _make_provider() for name in _TWO_PROVIDERS}
    pin, state = _stateful_pin(set(_TWO_PROVIDERS), start=start)
    return _cp_with(pin=pin, providers=providers), pin, state


class TestTransitionMicrocopy:
    # --- first vs subsequent -------------------------------------------

    @pytest.mark.asyncio
    async def test_first_pin_renders_exactly_the_approved_two_lines(self):
        cp, _, _ = _cp_stateful()
        result = _visible(await cp._handle_provider("use anthropic-fable"))
        expected_teach = (
            "   experimental \u00b7 scope: this conversation only \u00b7 "
            "/provider for details"
        )
        assert result.splitlines() == [
            "\U0001f4cc pinned: anthropic-fable",
            expected_teach,
        ]

    @pytest.mark.asyncio
    async def test_subsequent_pin_renders_exactly_one_bare_line(self):
        cp, _, _ = _cp_stateful()
        await cp._handle_provider("use anthropic-fable")
        result = _visible(await cp._handle_provider("use openai-fast"))
        assert result.splitlines() == ["\U0001f4cc pinned: openai-fast"]

    @pytest.mark.asyncio
    async def test_teach_line_appears_only_once_across_many_pins(self):
        cp, _, _ = _cp_stateful()
        seen = []
        for name in ("anthropic-fable", "openai-fast", "anthropic-fable"):
            seen.append(_visible(await cp._handle_provider(f"use {name}")))
        assert sum("experimental" in msg for msg in seen) == 1
        assert "experimental" in seen[0]

    @pytest.mark.asyncio
    async def test_teach_line_is_not_repeated_after_an_unpin_cycle(self):
        """Unpinning does not re-arm the lesson -- it is once per SESSION."""
        cp, _, _ = _cp_stateful()
        await cp._handle_provider("use anthropic-fable")
        await cp._handle_provider("auto")
        again = _visible(await cp._handle_provider("use anthropic-fable"))
        assert "experimental" not in again

    # --- unpin ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unpin_renders_exactly_the_approved_line(self):
        cp, _, _ = _cp_stateful(start="openai-fast")
        result = _visible(await cp._handle_provider("auto"))
        assert result == "unpinned (was openai-fast)"

    @pytest.mark.asyncio
    async def test_unpin_names_what_was_pinned_because_nothing_else_can(self):
        """The '(was X)' asymmetry is load-bearing: unpinning destroys the
        prompt indicator, which was the only other record of X."""
        cp, _, _ = _cp_stateful(start="anthropic-fable")
        result = _visible(await cp._handle_provider("auto"))
        assert "anthropic-fable" in result

    # --- no-op cases (the fixes that stop them lying) -------------------

    @pytest.mark.asyncio
    async def test_auto_with_nothing_pinned_renders_not_pinned(self):
        cp, pin, _ = _cp_stateful(start=None)
        result = _visible(await cp._handle_provider("auto"))
        assert result == "not pinned"
        pin.unpin.assert_called_once()

    @pytest.mark.asyncio
    async def test_repin_same_provider_renders_already_pinned(self):
        cp, _, _ = _cp_stateful()
        await cp._handle_provider("use anthropic-fable")
        result = _visible(await cp._handle_provider("use anthropic-fable"))
        assert result == "\U0001f4cc already pinned: anthropic-fable"

    @pytest.mark.asyncio
    async def test_repin_same_provider_still_revalidates_it_is_mounted(self):
        """'already pinned' must never be reported for a provider that has
        since been unmounted -- that would be a confident lie. pin() is
        still called, so the normal loud ValueError wins."""
        cp, _, state = _cp_stateful(start="anthropic-fable")
        state["pinned"] = "ghost-provider"  # pinned, but no longer mounted
        result = await cp._handle_provider("use ghost-provider")
        assert "already pinned" not in result
        assert result.startswith("\u2717 ")
        assert "not mounted" in result

    # --- styling and tense ----------------------------------------------

    @pytest.mark.asyncio
    async def test_every_transition_line_is_dim(self):
        """Colour carries weight that would otherwise be paid for in words:
        a dim fragment reads as a receipt, not as content to parse."""
        cp, _, _ = _cp_stateful()
        messages = [
            await cp._handle_provider("use anthropic-fable"),  # first (2 lines)
            await cp._handle_provider("use openai-fast"),  # subsequent
            await cp._handle_provider("use openai-fast"),  # already pinned
            await cp._handle_provider("auto"),  # unpin
            await cp._handle_provider("auto"),  # not pinned
        ]
        for message in messages:
            for line in message.splitlines():
                assert line.startswith("[dim]"), f"not dim: {line!r}"
                assert line.endswith("[/dim]"), f"not dim: {line!r}"

    @pytest.mark.asyncio
    async def test_no_transition_uses_forward_looking_tense(self):
        """Every string must be past-tense or state-descriptive -- true at
        the instant it prints. The pin is recorded synchronously and no LLM
        call has happened, so a forward-looking claim would be a prediction
        about a call that has not occurred."""
        cp, _, _ = _cp_stateful()
        messages = [
            await cp._handle_provider("use anthropic-fable"),
            await cp._handle_provider("use openai-fast"),
            await cp._handle_provider("use openai-fast"),
            await cp._handle_provider("auto"),
            await cp._handle_provider("auto"),
        ]
        forbidden = (
            "takes effect",
            "will use",
            "will be",
            "now using",
            "switched to",
            "next turn",
            "from now on",
            "going forward",
        )
        for message in messages:
            lowered = _visible(message).lower()
            for phrase in forbidden:
                assert phrase not in lowered, (
                    f"forward-looking tense {phrase!r} in {lowered!r}"
                )

    @pytest.mark.asyncio
    async def test_provider_name_with_markup_cannot_break_rendering(self):
        """These are the only /provider strings carrying markup, so a mount
        name containing '[' must not be able to open a style tag."""
        mounted = {"weird[bold]name"}
        pin, _ = _stateful_pin(mounted)
        cp = _cp_with(pin=pin, providers={name: _make_provider() for name in mounted})
        result = await cp._handle_provider("use weird[bold]name")
        assert "weird[bold]name" in _visible(result)

    # --- flag degradation ------------------------------------------------

    @pytest.mark.asyncio
    async def test_lost_flag_degrades_to_an_extra_line_never_a_wrong_message(
        self,
    ):
        """The flag gates ONLY whether the teaching line is appended -- never
        which message is chosen, never whether the pin happened. So broken
        session state costs at most one redundant line."""

        class _BrokenState:
            def get(self, *args, **kwargs):
                raise RuntimeError("session state unavailable")

            def __setitem__(self, *args):
                raise RuntimeError("session state unavailable")

        cp, pin, _ = _cp_stateful()
        cp.session.coordinator.session_state = _BrokenState()

        first = _visible(await cp._handle_provider("use anthropic-fable"))
        second = _visible(await cp._handle_provider("use openai-fast"))

        # Degrades to teaching every time -- an extra line, never a wrong one.
        for message, expected_pin in (
            (first, "anthropic-fable"),
            (second, "openai-fast"),
        ):
            lines = message.splitlines()
            assert lines[0] == f"\U0001f4cc pinned: {expected_pin}"
            assert len(lines) == 2
            assert "scope: this conversation only" in lines[1]

        # And the pin itself still happened, both times.
        assert pin.pin.call_count == 2

    @pytest.mark.asyncio
    async def test_flag_is_session_scoped_not_process_scoped(self):
        """A second session teaches again -- the lesson lives in that
        session's state, not in a module-level global."""
        cp_a, _, _ = _cp_stateful()
        first_a = _visible(await cp_a._handle_provider("use anthropic-fable"))
        assert "experimental" in first_a

        cp_b, _, _ = _cp_stateful()
        first_b = _visible(await cp_b._handle_provider("use anthropic-fable"))
        assert "experimental" in first_b


# === Unknown subcommand ===


class TestUnknownSubcommand:
    @pytest.mark.asyncio
    async def test_unknown_subcommand_shows_usage(self):
        pin = _make_pin(available=["anthropic-fable"])
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("frobnicate")
        assert "Unknown /provider subcommand" in result
        assert "/provider use <name>" in result
        assert "/provider auto" in result


# === Registration / discoverability ===


class TestCommandRegistration:
    def test_provider_registered_in_commands_dict(self):
        from amplifier_app_cli.main import CommandProcessor

        assert "/provider" in CommandProcessor.COMMANDS
        assert CommandProcessor.COMMANDS["/provider"]["action"] == "handle_provider"

    @pytest.mark.asyncio
    async def test_help_output_includes_provider_command(self):
        cp = _make_command_processor()
        help_text = cp._format_help()
        assert "/provider" in help_text

    @pytest.mark.asyncio
    async def test_handle_command_dispatches_to_handle_provider(self):
        pin = _make_pin(available=["anthropic-fable"])
        cp = _cp_with(pin=pin, providers={})
        result = await cp.handle_command("handle_provider", {"args": "auto"})
        assert "not pinned" in _visible(result)


# === (experimental) tagging ===
#
# The tag goes where the user sees the feature working -- help, status, and
# the confirmations -- and deliberately NOT on the error paths, which are
# already loud and would only be diluted by it.


class TestExperimentalTag:
    def test_help_entry_is_tagged(self):
        from amplifier_app_cli.main import CommandProcessor

        assert "(experimental)" in CommandProcessor.COMMANDS["/provider"]["description"]

    @pytest.mark.asyncio
    async def test_help_output_shows_tag(self):
        cp = _make_command_processor()
        help_text = cp._format_help()
        provider_line = next(
            line for line in help_text.splitlines() if line.startswith("  /provider")
        )
        assert "(experimental)" in provider_line

    @pytest.mark.asyncio
    async def test_status_header_is_tagged(self):
        providers = {"anthropic-fable": _make_provider(model="claude-sonnet-4-5")}
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert result.splitlines()[0] == "Conversation providers (experimental):"

    @pytest.mark.asyncio
    async def test_status_header_tagged_even_with_no_providers(self):
        cp = _cp_with(pin=_make_pin(), providers={})
        result = await cp._handle_provider("")
        assert result.splitlines()[0] == "Conversation providers (experimental):"

    @pytest.mark.asyncio
    async def test_first_use_carries_experimental_in_the_teach_line(self):
        """The (experimental) PREFIX is gone from the confirmations by
        design -- the microcopy redesign moved that context into the
        one-time teaching line, which is where it now lives."""
        pin = _make_pin(available=["anthropic-fable"])
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("use anthropic-fable")
        assert "experimental" in result
        assert not result.startswith("(experimental) ")

    @pytest.mark.asyncio
    async def test_subsequent_transitions_are_not_tagged(self):
        """Once taught, the acknowledgements are bare -- repeating the tag
        on every pin is exactly the glossed-over noise this redesign
        removed."""
        pin = _make_pin(available=["anthropic-fable", "openai-fast"])
        cp = _cp_with(
            pin=pin,
            providers={
                "anthropic-fable": _make_provider(),
                "openai-fast": _make_provider(),
            },
        )
        await cp._handle_provider("use anthropic-fable")  # spends the hint
        result = await cp._handle_provider("use openai-fast")
        assert "experimental" not in result

    @pytest.mark.asyncio
    async def test_capability_absent_error_is_NOT_tagged(self):
        cp = _cp_with(pin=None, providers={}, orchestrator="loop-basic")
        result = await cp._handle_provider("use anthropic-fable")
        assert "(experimental)" not in result

    @pytest.mark.asyncio
    async def test_unmounted_provider_error_is_NOT_tagged(self):
        def _raise(name):
            raise ValueError(
                f"cannot pin conversation provider {name!r}: it is not mounted "
                f"in this session. Mounted providers: anthropic-fable"
            )

        pin = _make_pin(available=["anthropic-fable"], pin_side_effect=_raise)
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("use nonexistent-provider")
        assert "(experimental)" not in result


# === Usage-figure caveat ===
#
# PLACEMENT DECISION (locked in by these tests): the caveat lives in the
# /provider STATUS view only, never on the per-use confirmations. The
# confirmations are already 320 and 284 chars -- 5 and 4 wrapped lines at
# 80 cols -- and the condition being disclaimed is a property of the CLI's
# usage display, not of pinning, so repeating it on every pin would grow
# the noisiest string for the least benefit.
#
# ACCURACY: per-vendor rates are correct and never cross-applied. The caveat
# must stay framed as precision/rounding/coverage, never "costs are wrong".


def _caveat() -> str:
    from amplifier_app_cli.main import CommandProcessor

    return CommandProcessor._PROVIDER_USAGE_CAVEAT


class TestUsageCaveat:
    @pytest.mark.asyncio
    async def test_status_unpinned_shows_caveat(self):
        providers = {"anthropic-fable": _make_provider(model="claude-sonnet-4-5")}
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert _caveat() in result

    @pytest.mark.asyncio
    async def test_status_pinned_shows_caveat(self):
        providers = {"anthropic-fable": _make_provider(model="claude-sonnet-4-5")}
        pin = _make_pin(available=list(providers), current="anthropic-fable")
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert _caveat() in result

    @pytest.mark.asyncio
    async def test_capability_absent_status_does_NOT_show_caveat(self):
        """When pinning isn't supported the refusal is the whole message --
        a cost footnote there dilutes it, same principle that keeps the
        (experimental) tag off the error paths."""
        providers = {"anthropic-fable": _make_provider(model="claude-sonnet-4-5")}
        cp = _cp_with(pin=None, providers=providers, orchestrator="loop-basic")
        result = await cp._handle_provider("")
        assert _caveat() not in result
        assert "Usage figures:" not in result
        # The refusal itself must still be fully intact.
        assert "not registered" in result
        assert "restarting" in result

    @pytest.mark.asyncio
    async def test_no_providers_mounted_does_NOT_show_caveat(self):
        """No providers means no usage figures to disclaim."""
        cp = _cp_with(pin=_make_pin(), providers={})
        result = await cp._handle_provider("")
        assert "Usage figures:" not in result

    @pytest.mark.asyncio
    async def test_use_confirmation_does_NOT_carry_caveat(self):
        pin = _make_pin(available=["anthropic-fable"])
        cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
        result = await cp._handle_provider("use anthropic-fable")
        assert "Usage figures:" not in result
        assert "billing-grade" not in result

    @pytest.mark.asyncio
    async def test_auto_confirmations_do_NOT_carry_caveat(self):
        for unpin_result in ("anthropic-fable", None):
            pin = _make_pin(available=["anthropic-fable"])
            pin.unpin.return_value = unpin_result
            cp = _cp_with(pin=pin, providers={"anthropic-fable": _make_provider()})
            result = await cp._handle_provider("auto")
            assert "Usage figures:" not in result
            assert "billing-grade" not in result

    @pytest.mark.asyncio
    async def test_transitions_stay_within_their_tightened_line_budget(self):
        """Bound TIGHTENED by the microcopy redesign: these used to wrap to
        5 (use) and 4 (auto) lines at 80 cols. The new strings are one line
        each -- two on the first pin only, for the teaching line -- and the
        budget now says so, so any regression toward paragraphs fails here.

        Proxy guard: textwrap on markup-stripped text, not a real terminal.
        """
        import textwrap

        providers = {
            "anthropic-fable": _make_provider(),
            "openai-fast": _make_provider(),
        }
        pin = _make_pin(available=list(providers))
        cp = _cp_with(pin=pin, providers=providers)

        first = _visible(await cp._handle_provider("use anthropic-fable"))
        assert len(first.splitlines()) == 2, "first pin is exactly two lines"
        for line in first.splitlines():
            assert len(textwrap.wrap(line, 80)) <= 1, (
                f"first-pin line exceeds 80 cols: {line!r}"
            )

        pin.current.return_value = "anthropic-fable"
        later = _visible(await cp._handle_provider("use openai-fast"))
        assert len(textwrap.wrap(later, 80)) <= 1, (
            "/provider use acknowledgement grew past one line at 80 cols"
        )

        pin2 = _make_pin(available=list(providers))
        pin2.unpin.return_value = "anthropic-fable"
        cp2 = _cp_with(pin=pin2, providers=providers)
        auto_msg = _visible(await cp2._handle_provider("auto"))
        assert len(textwrap.wrap(auto_msg, 80)) <= 1, (
            "/provider auto acknowledgement grew past one line at 80 cols"
        )

    def test_caveat_does_not_claim_rates_are_wrong_or_cross_applied(self):
        """Measured across four providers against raw events.jsonl:
        per-vendor rates ARE correct and are never cross-applied. Saying
        otherwise would be inaccurate and needlessly alarming."""
        caveat = _caveat().lower()
        for forbidden in (
            "wrong",
            "incorrect",
            "inaccurate",
            "cross-appl",
            "mispriced",
            "overcharg",
            "unreliable",
        ):
            assert forbidden not in caveat, (
                f"caveat overstates the defect with {forbidden!r} -- the "
                f"honest framing is precision/rounding/coverage"
            )

    def test_caveat_states_rates_themselves_are_correct(self):
        """The anti-alarm clause is load-bearing: without it the caveat
        reads as 'costs are wrong', which is not what was measured."""
        caveat = _caveat().lower()
        assert "rates" in caveat
        assert "correct" in caveat

    def test_caveat_covers_the_four_measured_defect_classes(self):
        """Counts (over- and under-reporting), rounding, and missing
        coverage for the newest models."""
        caveat = _caveat().lower()
        assert "over- or under-reported" in caveat
        assert "rounded" in caveat
        assert "newest models" in caveat

    def test_caveat_stays_short(self):
        """It is a footnote, not a paragraph. Kept under 250 chars so the
        status view stays scannable."""
        assert len(_caveat()) <= 250, (
            f"usage caveat grew to {len(_caveat())} chars -- keep it a footnote"
        )

    @pytest.mark.asyncio
    async def test_caveat_is_the_last_line_of_status(self):
        """Reads as a footnote under the Selection line, not as a banner
        competing with the provider table."""
        providers = {"anthropic-fable": _make_provider(model="claude-sonnet-4-5")}
        pin = _make_pin(available=list(providers), current=None)
        cp = _cp_with(pin=pin, providers=providers)
        result = await cp._handle_provider("")
        assert result.splitlines()[-1] == _caveat()


# === Prompt indicator ===
#
# Approved mockup (mode leftmost, pin second, caret last):
#
#   unpinned:          >
#   pinned:            [PIN anthropic-haiku]>
#   pinned + mode:     [plan][PIN anthropic-haiku]>
#
# ("PIN" above is the U+1F4CC pushpin glyph.)

_PUSHPIN = "\U0001f4cc"


def _rendered(message):
    """The visible text of a prompt_toolkit HTML message, markup stripped.

    Also proves HTML() actually parses -- a markup error would raise here.
    """
    from prompt_toolkit.formatted_text import to_formatted_text

    return "".join(fragment[1] for fragment in to_formatted_text(message))


class TestPromptIndicator:
    def test_unpinned_no_mode_renders_bare_caret(self):
        from amplifier_app_cli.main import _build_prompt_message

        message = _build_prompt_message(lambda: None, lambda: None)
        assert _rendered(message) == "\n> "

    def test_unpinned_no_mode_markup_is_byte_for_byte_unchanged(self):
        """The pre-pin prompt markup, exactly. If this drifts, every user
        who never touches /provider sees a changed prompt."""
        from amplifier_app_cli.main import _build_prompt_message

        message = _build_prompt_message(lambda: None, lambda: None)
        assert message.value == "\n<ansigreen><b>></b></ansigreen> "

    def test_no_getters_at_all_renders_bare_caret(self):
        """Absent getters (the pre-feature call signature) must behave
        exactly like getters that return None."""
        from amplifier_app_cli.main import _build_prompt_message

        message = _build_prompt_message()
        assert message.value == "\n<ansigreen><b>></b></ansigreen> "

    def test_mode_only_markup_is_byte_for_byte_unchanged(self):
        from amplifier_app_cli.main import _build_prompt_message

        message = _build_prompt_message(lambda: "plan", lambda: None)
        assert (
            message.value
            == "\n<ansicyan>[plan]</ansicyan><ansigreen><b>></b></ansigreen> "
        )
        assert _rendered(message) == "\n[plan]> "

    def test_pinned_only_matches_mockup(self):
        from amplifier_app_cli.main import _build_prompt_message

        message = _build_prompt_message(lambda: None, lambda: "anthropic-haiku")
        assert _rendered(message) == f"\n[{_PUSHPIN} anthropic-haiku]> "

    def test_pinned_plus_mode_matches_mockup_with_mode_leftmost(self):
        from amplifier_app_cli.main import _build_prompt_message

        message = _build_prompt_message(lambda: "plan", lambda: "anthropic-haiku")
        assert _rendered(message) == f"\n[plan][{_PUSHPIN} anthropic-haiku]> "

    def test_provider_name_is_never_truncated(self):
        """Several mount names share a model family (anthropic-fable /
        -opus / -sonnet / -haiku); shortening destroys the distinction the
        indicator exists to show."""
        from amplifier_app_cli.main import _build_prompt_message

        long_name = "anthropic-fable-experimental-long-mount-name"
        message = _build_prompt_message(lambda: None, lambda: long_name)
        assert long_name in _rendered(message)

    def test_pin_getter_raising_does_not_break_prompt(self):
        """The prompt callable runs on every keystroke -- it must never
        raise, or the session becomes unusable."""
        from amplifier_app_cli.main import _build_prompt_message

        def _boom():
            raise RuntimeError("capability exploded")

        message = _build_prompt_message(lambda: None, _boom)
        assert _rendered(message) == "\n> "

    def test_mode_getter_raising_does_not_suppress_pin_indicator(self):
        """Each indicator degrades independently -- one broken getter must
        not take the other's indicator down with it."""
        from amplifier_app_cli.main import _build_prompt_message

        def _boom():
            raise RuntimeError("mode lookup exploded")

        message = _build_prompt_message(_boom, lambda: "anthropic-haiku")
        assert _rendered(message) == f"\n[{_PUSHPIN} anthropic-haiku]> "

    def test_markup_special_characters_in_name_do_not_raise(self):
        """A '<' or '&' in a name would otherwise make HTML() raise."""
        from amplifier_app_cli.main import _build_prompt_message

        message = _build_prompt_message(lambda: None, lambda: "weird<&>name")
        assert _rendered(message) == f"\n[{_PUSHPIN} weird<&>name]> "

    def test_empty_string_pin_is_treated_as_unpinned(self):
        from amplifier_app_cli.main import _build_prompt_message

        message = _build_prompt_message(lambda: None, lambda: "")
        assert message.value == "\n<ansigreen><b>></b></ansigreen> "


# === _pinned_provider_name: the prompt's source of truth ===


class TestPinnedProviderName:
    def test_returns_none_when_capability_absent(self):
        from amplifier_app_cli.main import _pinned_provider_name

        cp = _cp_with(pin=None, providers={})
        assert _pinned_provider_name(cp.session) is None

    def test_returns_none_when_nothing_pinned(self):
        from amplifier_app_cli.main import _pinned_provider_name

        cp = _cp_with(pin=_make_pin(current=None), providers={})
        assert _pinned_provider_name(cp.session) is None

    def test_returns_pinned_name(self):
        from amplifier_app_cli.main import _pinned_provider_name

        cp = _cp_with(pin=_make_pin(current="anthropic-haiku"), providers={})
        assert _pinned_provider_name(cp.session) == "anthropic-haiku"

    def test_non_string_current_is_treated_as_unpinned(self):
        from amplifier_app_cli.main import _pinned_provider_name

        cp = _cp_with(pin=_make_pin(current=object()), providers={})
        assert _pinned_provider_name(cp.session) is None

    @pytest.mark.asyncio
    async def test_tracks_pin_and_unpin_through_the_command(self):
        """End-to-end within the app layer: the indicator source follows
        /provider use and /provider auto without any app-side copy of the
        pin state."""
        from amplifier_app_cli.main import _build_prompt_message, _pinned_provider_name

        state = {"pinned": None}
        pin = _make_pin(available=["anthropic-haiku"])
        pin.current.side_effect = lambda: state["pinned"]

        def _pin(name):
            state["pinned"] = name
            return name

        def _unpin():
            previous = state["pinned"]
            state["pinned"] = None
            return previous

        pin.pin.side_effect = _pin
        pin.unpin.side_effect = _unpin

        cp = _cp_with(pin=pin, providers={"anthropic-haiku": _make_provider()})

        def getter():
            return _pinned_provider_name(cp.session)

        assert _rendered(_build_prompt_message(lambda: None, getter)) == "\n> "

        await cp._handle_provider("use anthropic-haiku")
        assert (
            _rendered(_build_prompt_message(lambda: None, getter))
            == f"\n[{_PUSHPIN} anthropic-haiku]> "
        )

        await cp._handle_provider("auto")
        assert _rendered(_build_prompt_message(lambda: None, getter)) == "\n> "


# === WIRING: the prompt callable PromptSession actually renders ===
#
# REGRESSION GUARD. Every test above this line calls _build_prompt_message
# directly. That is exactly the coverage that already existed and did NOT
# catch the real bug: _create_prompt_session() carried its own duplicate
# get_prompt() closure that handled the mode indicator but never referenced
# get_pinned_provider, and PromptSession(message=...) was wired to THAT copy.
# The composer was correct, fully unit-tested, and never called at runtime --
# 48 passing tests, zero of which touched _create_prompt_session.
#
# So these tests deliberately do NOT call the composer. They construct the
# real PromptSession through _create_prompt_session() and resolve the message
# through the constructed object, the same way prompt_toolkit does at render
# time. prompt_toolkit's own render path is:
#
#     PromptSession._get_prompt(self):
#         return to_formatted_text(self.message, style="class:prompt")
#
# (prompt_toolkit/shortcuts/prompt.py -- `self.message = message` at __init__,
# resolved via to_formatted_text at render). So `session.message` IS the seam,
# and resolving it through to_formatted_text is what the library itself does.
#
# A real PromptSession CAN be constructed headless -- no TTY needed. It emits
# "Warning: Input is not a terminal (fd=0)" and works, so there is no need to
# settle for anything short of the real object here.


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect Path.home() so constructing a real PromptSession cannot touch
    the developer's actual ~/.amplifier/projects/<slug>/repl_history."""
    from pathlib import Path as _Path

    monkeypatch.setattr(_Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _prompt_session(mode=None, pinned=None):
    """Build the REAL PromptSession through the REAL factory."""
    from amplifier_app_cli.main import _create_prompt_session

    return _create_prompt_session(
        get_active_mode=lambda: mode,
        get_pinned_provider=lambda: pinned,
    )


def _render_via_prompt_session(session) -> str:
    """Resolve the prompt the way prompt_toolkit does at render time.

    Mirrors PromptSession._get_prompt() -- to_formatted_text(self.message) --
    so this exercises the wiring, not a composer called in isolation.
    """
    from prompt_toolkit.formatted_text import to_formatted_text

    return "".join(fragment[1] for fragment in to_formatted_text(session.message))


@pytest.mark.usefixtures("isolated_home")
class TestPromptSessionWiring:
    def test_message_is_a_callable_not_a_prebuilt_value(self):
        """A static value would freeze the indicator at construction time --
        the pin must be re-read on every render."""
        session = _prompt_session(mode=None, pinned="anthropic-haiku")
        assert callable(session.message)

    def test_unpinned_renders_bare_prompt_through_session(self):
        session = _prompt_session(mode=None, pinned=None)
        assert _render_via_prompt_session(session) == "\n> "

    def test_pinned_renders_pin_indicator_through_session(self):
        """THE regression: this is the assertion the old suite never made.
        It fails against the duplicate closure that ignored the pin getter."""
        session = _prompt_session(mode=None, pinned="anthropic-haiku")
        assert (
            _render_via_prompt_session(session) == f"\n[{_PUSHPIN} anthropic-haiku]> "
        )

    def test_mode_only_renders_mode_indicator_through_session(self):
        session = _prompt_session(mode="plan", pinned=None)
        assert _render_via_prompt_session(session) == "\n[plan]> "

    def test_pinned_plus_mode_renders_both_with_mode_leftmost(self):
        session = _prompt_session(mode="plan", pinned="anthropic-haiku")
        assert (
            _render_via_prompt_session(session)
            == f"\n[plan][{_PUSHPIN} anthropic-haiku]> "
        )

    def test_prompt_toolkits_own_get_prompt_path_renders_the_indicators(self):
        """Belt and braces: drive prompt_toolkit's ACTUAL internal render
        method rather than our reimplementation of it. If PromptSession stops
        routing through _get_prompt, this skips rather than lying."""
        session = _prompt_session(mode="plan", pinned="anthropic-haiku")
        get_prompt = getattr(session, "_get_prompt", None)
        if get_prompt is None:  # pragma: no cover - prompt_toolkit API drift
            pytest.skip("prompt_toolkit no longer exposes PromptSession._get_prompt")
        rendered = "".join(fragment[1] for fragment in get_prompt())
        assert rendered == f"\n[plan][{_PUSHPIN} anthropic-haiku]> "

    def test_message_is_re_evaluated_on_every_render(self):
        """The pin can change mid-session (/provider use, /provider auto), so
        a cached first render would show a stale indicator forever."""
        from amplifier_app_cli.main import _create_prompt_session

        state: dict[str, str | None] = {"pinned": None}
        session = _create_prompt_session(
            get_active_mode=lambda: None,
            get_pinned_provider=lambda: state["pinned"],
        )
        assert _render_via_prompt_session(session) == "\n> "
        state["pinned"] = "anthropic-haiku"
        assert (
            _render_via_prompt_session(session) == f"\n[{_PUSHPIN} anthropic-haiku]> "
        )
        state["pinned"] = None
        assert _render_via_prompt_session(session) == "\n> "

    def test_every_getter_passed_to_the_factory_is_actually_consulted(self):
        """Generalized guard for the whole bug class, independent of what the
        getters return: a partial closure that never references one of them
        records zero calls on that spy and fails here. This is what makes
        'someone reintroduces a closure that ignores a getter' non-silent
        even for a getter whose value happens to be empty."""
        from amplifier_app_cli.main import _create_prompt_session

        calls = {"mode": 0, "pin": 0}

        # Both deliberately yield an EMPTY value (implicit None): the spy
        # proves consultation, so this catches an ignored getter even when
        # its value would not have shown an indicator anyway.
        def _mode():
            calls["mode"] += 1

        def _pin():
            calls["pin"] += 1

        session = _create_prompt_session(
            get_active_mode=_mode, get_pinned_provider=_pin
        )
        _render_via_prompt_session(session)

        assert calls["mode"] > 0, (
            "get_active_mode was never consulted when the prompt rendered -- "
            "PromptSession.message is not wired to the composer"
        )
        assert calls["pin"] > 0, (
            "get_pinned_provider was never consulted when the prompt rendered "
            "-- this is the exact bug: a duplicate closure that ignores it"
        )

    def test_factory_delegates_to_the_single_composer(self):
        """Structural backstop: the factory's message callable must route
        through _build_prompt_message. Patching the composer must change what
        the constructed session renders -- if it doesn't, a second
        implementation has been reintroduced somewhere in the factory."""
        from unittest.mock import patch

        from prompt_toolkit.formatted_text import HTML

        with patch(
            "amplifier_app_cli.main._build_prompt_message",
            return_value=HTML("SENTINEL"),
        ) as composer:
            session = _prompt_session(mode="plan", pinned="anthropic-haiku")
            rendered = _render_via_prompt_session(session)

        assert rendered == "SENTINEL", (
            "the constructed PromptSession did not render through "
            "_build_prompt_message -- a duplicate prompt implementation exists"
        )
        assert composer.called

    def test_factory_still_works_with_no_getters(self):
        """The pre-feature call signature must keep working -- a user who
        never touches /provider sees the byte-for-byte original prompt."""
        from amplifier_app_cli.main import _create_prompt_session

        session = _create_prompt_session()
        assert _render_via_prompt_session(session) == "\n> "

    def test_raising_getter_does_not_break_the_constructed_prompt(self):
        """Through the real wiring, not just the composer: the render path
        runs on every keystroke and must never raise."""
        from amplifier_app_cli.main import _create_prompt_session

        def _boom():
            raise RuntimeError("capability exploded")

        session = _create_prompt_session(
            get_active_mode=lambda: None, get_pinned_provider=_boom
        )
        assert _render_via_prompt_session(session) == "\n> "
