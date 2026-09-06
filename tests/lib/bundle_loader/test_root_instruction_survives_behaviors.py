"""The user's root bundle must keep its own system instruction.

Bug (model_performance-f26u): every session on the reporting host since
2026-08-26 ran with NO system prompt from its chosen bundle -- 54 of 54.

Three facts compose to produce it:

1. foundation ``Bundle.compose()`` documents *"instruction: later replaces
   earlier"* and implements exactly that::

       if other.instruction:
           result.instruction = other.instruction

2. ``runtime/config.py`` ALWAYS composes app-level behaviors (modes, skills,
   cli-expertise, wayfinder, notifications) and then the settings ``bundle.app``
   list ONTO the user's root bundle -- root is ``self``, behaviors are
   ``others``.

3. A behavior bundle whose ``bundle.md`` carries a README-style markdown body
   HAS an ``instruction``.  ``amplifier-bundle-notify``'s root ``bundle.md``
   carries a 2,988-char README body, and ``_build_notification_behaviors()``
   composes that root bundle ("a minimal marker that just identifies the
   repo").

Net: whichever always-composed bundle last had a markdown body silently
REPLACED the user's system prompt.  For ``anchors-amp-dev`` the lost body is
the single line ``@anchors-amp-dev:context/system.md`` -- so the defect has
two halves: the body is dropped, and therefore its @mention is never expanded
either.  ``mentions:resolved`` listed the 22 app-bundle context mentions and
not the root's own.

These tests cover both halves at the seam where the composition happens
(``load_and_prepare_bundle``) and at the seam where the system prompt is
actually built (foundation's own system-prompt factory, driven through
``PreparedBundle.create_session()`` with a fake context module).

Notify is only the bundle that fires today.  Any behavior or app bundle with
a body does this, which is why the fix lives in the compose loop and not in
notify.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery
from amplifier_app_cli.lib.bundle_loader.prepare import load_and_prepare_bundle

pytestmark = pytest.mark.anyio

ROOT_MARKER = "ROOT-SYSTEM-MARKER-f26u"
ROOT_MENTION = "@rootbundle:context/system.md"
BEHAVIOR_MARKER = "BEHAVIOR-README-BODY-f26u"


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    """Configure anyio to use the asyncio backend only."""
    return "asyncio"


def _write_root_bundle(tmp_path: Path, *, body: str | None = ROOT_MENTION) -> str:
    """Write a user root bundle whose body is a single @mention (like anchors-amp-dev)."""
    root = tmp_path / "rootbundle"
    (root / "context").mkdir(parents=True, exist_ok=True)
    (root / "context" / "system.md").write_text(
        f"{ROOT_MARKER}\n\nYou are configured for development OF the thing.\n",
        encoding="utf-8",
    )
    frontmatter = (
        "---\n"
        "bundle:\n"
        "  name: rootbundle\n"
        "  version: 0.1.0\n"
        "  description: The user's chosen bundle\n"
        "---\n"
    )
    (root / "bundle.md").write_text(
        frontmatter + (f"\n{body}\n" if body else ""), encoding="utf-8"
    )
    return f"file://{root}"


def _write_behavior_bundle(tmp_path: Path, name: str = "behaviorbundle") -> str:
    """Write an always-composed behavior bundle carrying a README-style body."""
    behavior = tmp_path / name
    behavior.mkdir(parents=True, exist_ok=True)
    (behavior / "bundle.md").write_text(
        "---\n"
        "bundle:\n"
        f"  name: {name}\n"
        "  version: 0.1.0\n"
        "  description: An app-level behavior bundle\n"
        "---\n"
        "\n"
        f"# {name}\n"
        "\n"
        f"{BEHAVIOR_MARKER}\n",
        encoding="utf-8",
    )
    return f"file://{behavior}"


async def _prepare(root_uri: str, behavior_uris: list[str]):
    """Run the real app-cli compose+prepare path over local bundles."""
    discovery = AppBundleDiscovery(search_paths=[])
    return await load_and_prepare_bundle(
        root_uri,
        discovery,
        install_deps=False,
        compose_behaviors=behavior_uris,
    )


class _FakeContext:
    """Stands in for context-simple: supports the factory-based system prompt."""

    def __init__(self) -> None:
        self.factory = None
        self.messages: list[dict] = []

    async def set_system_prompt_factory(self, factory) -> None:
        self.factory = factory

    async def add_message(self, message: dict) -> None:
        self.messages.append(message)


async def _build_system_prompt(prepared, session_cwd: Path) -> tuple[str, list]:
    """Drive foundation's real system-prompt factory over the prepared bundle.

    Returns (system_prompt, emitted_hook_calls).  The session itself is a mock
    -- no provider, no modules -- so this exercises the mention-expansion and
    ``mentions:resolved`` emission paths without needing a live session.
    """
    fake_context = _FakeContext()
    session = MagicMock()
    session.coordinator.mount = AsyncMock()
    session.initialize = AsyncMock()
    session.coordinator.hooks.emit = AsyncMock()
    session.coordinator.hooks.list_handlers = MagicMock(return_value={})
    session.coordinator.get = MagicMock(
        side_effect=lambda name: fake_context if name == "context" else None
    )

    with patch("amplifier_core.AmplifierSession", return_value=session):
        await prepared.create_session(session_cwd=session_cwd)

    assert fake_context.factory is not None, (
        "foundation registered no system-prompt factory -- the bundle carried "
        "neither an instruction nor context"
    )
    prompt = await fake_context.factory()
    return prompt, list(session.coordinator.hooks.emit.await_args_list)


async def test_root_instruction_survives_behavior_composition(tmp_path: Path) -> None:
    """FAIL-BEFORE: the root's body must not be replaced by a behavior's body."""
    root_uri = _write_root_bundle(tmp_path)
    behavior_uri = _write_behavior_bundle(tmp_path)

    prepared = await _prepare(root_uri, [behavior_uri])

    assert prepared.bundle.instruction is not None
    assert ROOT_MENTION in prepared.bundle.instruction, (
        "the root bundle's instruction was replaced during behavior "
        f"composition; got: {prepared.bundle.instruction!r}"
    )
    assert BEHAVIOR_MARKER not in prepared.bundle.instruction


async def test_last_behavior_with_a_body_does_not_win(tmp_path: Path) -> None:
    """Several behaviors compose in order; none of them may take the instruction."""
    root_uri = _write_root_bundle(tmp_path)
    behaviors = [
        _write_behavior_bundle(tmp_path, "behavior-one"),
        _write_behavior_bundle(tmp_path, "behavior-two"),
        _write_behavior_bundle(tmp_path, "behavior-three"),
    ]

    prepared = await _prepare(root_uri, behaviors)

    assert prepared.bundle.instruction is not None
    assert ROOT_MENTION in prepared.bundle.instruction
    assert BEHAVIOR_MARKER not in prepared.bundle.instruction


async def test_root_mention_still_expands_in_the_system_prompt(
    tmp_path: Path,
) -> None:
    """Half two: restoring the body is useless if its @mention never resolves.

    The real defect showed BOTH halves -- the marker absent from ``raw.system``
    AND the root's own mention absent from ``mentions:resolved``.
    """
    root_uri = _write_root_bundle(tmp_path)
    behavior_uri = _write_behavior_bundle(tmp_path)

    prepared = await _prepare(root_uri, [behavior_uri])
    prompt, emitted = await _build_system_prompt(prepared, tmp_path)

    assert ROOT_MARKER in prompt, (
        "the root bundle's system.md never reached the system prompt; "
        f"prompt was: {prompt[:400]!r}"
    )

    mentions_events = [
        call.args[1] for call in emitted if call.args[0] == "mentions:resolved"
    ]
    assert mentions_events, "no mentions:resolved event was emitted"
    resolved_mentions = {
        resolution.get("mention")
        for payload in mentions_events
        for resolution in payload.get("resolutions", [])
    }
    assert ROOT_MENTION in resolved_mentions, (
        "the root instruction's @mention was never expanded; "
        f"mentions:resolved carried: {sorted(m for m in resolved_mentions if m)}"
    )


async def test_dropped_behavior_body_is_reported_not_silent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A body that loses must be named in the log -- silence is the same defect.

    The fix makes the ROOT win.  That decision drops the behavior's body, so it
    must be announced: a silent drop in the other direction is the same class
    of bug this item exists to remove.
    """
    root_uri = _write_root_bundle(tmp_path)
    behavior_uri = _write_behavior_bundle(tmp_path, "noisy-behavior")

    with caplog.at_level(logging.WARNING, logger="amplifier_app_cli"):
        await _prepare(root_uri, [behavior_uri])

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("noisy-behavior" in message for message in warnings), (
        f"the dropped body was not reported; warnings were: {warnings}"
    )


async def test_behavior_body_still_used_when_root_has_none(tmp_path: Path) -> None:
    """No regression for a root bundle that carries no body of its own.

    Restoring only a NON-EMPTY root instruction means a bodyless root still
    inherits a composed body, exactly as before the fix.
    """
    root_uri = _write_root_bundle(tmp_path, body=None)
    behavior_uri = _write_behavior_bundle(tmp_path)

    prepared = await _prepare(root_uri, [behavior_uri])

    assert prepared.bundle.instruction is not None
    assert BEHAVIOR_MARKER in prepared.bundle.instruction
