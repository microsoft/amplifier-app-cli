"""Pin the lean always-on head text of ``context/cli-awareness.md``.

WHY THIS FILE IS PINNED AT ALL
------------------------------
``context/cli-awareness.md`` is wired into the always-on head:
``behaviors/cli-expertise.yaml`` lists it under ``app-cli:context/cli-awareness.md``,
so its full text is paid for on **every request of every session** whether or not
the CLI-expert capability is ever used. That makes every character a recurring
cost, not a one-off.

The lean rewrite here is not a stylistic preference. It was diffed, fidelity-checked
and byte-measured upstream (``amplifier-foundation`` main, PR #372, SHA 4384805,
``docs/lanes/zc6t-lean-head-ship/patches/context-files/04-__app_cli__-cli-awareness.md.patch``),
and the full lean-head treatment measured **-13.57% $/task, CI [-22.27%, -4.86%]**
with all three pre-registered estimators excluding zero.

A prose file has no compiler and no runtime assertion. Left unpinned, the v1 wording
returns the first time somebody "improves the docs", and the saving silently
evaporates with no test going red. This file is that missing compiler.

WHAT IS PINNED, AND WHY EACH ASSERTION EXISTS
---------------------------------------------
1. ``test_text_is_byte_exact``      -- exact anti-drift pin. Any edit fails loudly.
2. ``test_char_budget``             -- the head is a budget; growth must be deliberate.
3. ``test_semantic_invariants_survive`` -- a *future* further shrink must not drop a
   load-bearing claim. Fidelity, not just size.
4. ``test_v1_prose_has_not_returned`` -- names the specific v1 phrasing that must not
   come back, so a revert reads as an intentional failure rather than a mystery.
5. ``test_no_structural_carriers_lost`` -- stock carried no fenced command, bullet,
   ``@mention`` or URL; if one ever appears it must be a deliberate addition, not a
   silent reintroduction of trimmed material.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Repo root is the parent of tests/. The file lives at the repo-root bundle overlay
# (`context/`), which pyproject's hatch force-include ships into the wheel as
# `amplifier_app_cli/_bundle/context/`. The repo root stays the single source of truth.
CLI_AWARENESS = Path(__file__).resolve().parents[1] / "context" / "cli-awareness.md"

# The measured lean text. Stock -> lean: 398 -> 338 characters (404 -> 342 bytes),
# saved 60 characters. Byte-identical to zc6t's `.lean.md` artifact.
EXPECTED = (
    "# Amplifier CLI: there is an expert for this\n"
    "You run inside the Amplifier CLI application. The CLI itself \u2014 commands, "
    "flags, config, session machinery \u2014 is a domain with a dedicated expert "
    "carrying the authoritative, version-matched docs. Your own task (writing "
    "code, debugging the user's project) is not that domain; handle it normally.\n"
)

# Measured on the lean text, newlines normalised to LF.
EXPECTED_CHARS = 338

# The four load-bearing claims the stock text made. A further shrink is allowed;
# dropping one of these is not. Each entry is (why it matters, required substrings).
SEMANTIC_INVARIANTS: list[tuple[str, tuple[str, ...]]] = [
    (
        "the reader is told an expert exists at all",
        ("expert",),
    ),
    (
        "the expert's domain is enumerated, not left vague",
        ("commands", "flags", "config", "session machinery"),
    ),
    (
        "the docs are described as authoritative AND version-matched -- "
        "'version-matched' is the whole reason not to answer from memory",
        ("authoritative", "version-matched"),
    ),
    (
        "the negative boundary: the user's own task is explicitly NOT this domain, "
        "otherwise the pointer fires on every coding question",
        ("own task", "not that domain"),
    ),
]

# Verbatim fragments unique to the v1 (stock) wording. Their return means a revert.
V1_ONLY_PROSE = (
    "You are running inside the **Amplifier CLI application**",
    "its commands, flags, config, and session machinery",
    "dedicated expert that carries the authoritative",
    "If the question is about *your own task*",
    "that is not this domain \u2014 handle it normally",
)


def _read() -> str:
    """Read the pinned file with newlines normalised.

    The repo ships no ``.gitattributes``, so a Windows CI checkout may materialise
    this file with CRLF line endings. Normalising here keeps the pin about the
    *content* rather than about the checkout's line-ending policy -- otherwise this
    test would go red on windows-latest for a reason that has nothing to do with drift.
    """
    assert CLI_AWARENESS.is_file(), f"pinned file is missing: {CLI_AWARENESS}"
    return CLI_AWARENESS.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_pinned_file_exists_where_the_behavior_expects_it() -> None:
    """`behaviors/cli-expertise.yaml` references this exact path; a move breaks the head."""
    behavior = (
        Path(__file__).resolve().parents[1] / "behaviors" / "cli-expertise.yaml"
    )
    assert behavior.is_file(), f"behavior file is missing: {behavior}"
    assert "app-cli:context/cli-awareness.md" in behavior.read_text(encoding="utf-8"), (
        "cli-expertise.yaml no longer includes app-cli:context/cli-awareness.md -- "
        "either the include was dropped or the file was moved. If that is deliberate, "
        "update this pin; if it is not, the always-on pointer just went missing."
    )


def test_text_is_byte_exact() -> None:
    """Exact anti-drift pin: the lean text is what ships, character for character."""
    actual = _read()
    assert actual == EXPECTED, (
        "context/cli-awareness.md has drifted from the measured lean text.\n"
        "This file renders into the ALWAYS-ON head -- it is paid for on every "
        "request of every session.\n"
        f"expected ({len(EXPECTED)} chars):\n{EXPECTED!r}\n"
        f"actual   ({len(actual)} chars):\n{actual!r}\n"
        "If the change is deliberate, update EXPECTED and EXPECTED_CHARS here and "
        "say what the new cost is."
    )


def test_char_budget() -> None:
    """The always-on head is a budget. Growth has to be a decision, not an accident."""
    actual = _read()
    assert len(actual) == EXPECTED_CHARS, (
        f"always-on head budget for cli-awareness.md is {EXPECTED_CHARS} characters "
        f"(stock was 398); this file is now {len(actual)}. "
        f"Delta {len(actual) - EXPECTED_CHARS:+d} characters on EVERY request."
    )


@pytest.mark.parametrize(
    "why,required",
    SEMANTIC_INVARIANTS,
    ids=[inv[0][:40] for inv in SEMANTIC_INVARIANTS],
)
def test_semantic_invariants_survive(why: str, required: tuple[str, ...]) -> None:
    """A further shrink is welcome; dropping a load-bearing claim is not.

    This is the fidelity half of the pin: it survives a rewrite that the byte-exact
    test would reject, so it still guards the next lean pass.
    """
    text = _read().lower()
    for fragment in required:
        assert fragment.lower() in text, (
            f"lean text lost a load-bearing element ({why}): {fragment!r} is gone. "
            "Fidelity failure -- a shrink may not drop a rule, constraint, command, "
            "or pointer."
        )


@pytest.mark.parametrize("fragment", V1_ONLY_PROSE)
def test_v1_prose_has_not_returned(fragment: str) -> None:
    """Name the exact v1 phrasing, so a revert fails with a legible reason."""
    assert fragment not in _read(), (
        f"pre-lean (v1) wording is back: {fragment!r}. "
        "The lean rewrite saved 60 characters on every request of every session; "
        "reverting it silently gives that back."
    )


def test_no_structural_carriers_lost() -> None:
    """Stock carried no fenced command, bullet, @mention or URL -- and lean carries none.

    Recorded explicitly so the fidelity claim is checkable rather than asserted:
    there was no structural carrier here for a shrink to drop.
    """
    text = _read()
    assert "```" not in text, "a fenced block appeared; re-run the fidelity check"
    assert "@" not in text, "an @mention appeared; re-run the fidelity check"
    assert "http://" not in text and "https://" not in text, (
        "a URL appeared; re-run the fidelity check"
    )
    assert text.count("#") == 1, "heading structure changed; re-run the fidelity check"
