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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_foundation import load_bundle

from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery
from amplifier_app_cli.lib.bundle_loader.prepare import (
    _AGENTS_INSTRUCTION_TAIL,
    load_and_prepare_bundle,
)

pytestmark = pytest.mark.anyio

ROOT_MARKER = "ROOT-SYSTEM-MARKER-f26u"
ROOT_MENTION = "@rootbundle:context/system.md"
BEHAVIOR_MARKER = "BEHAVIOR-README-BODY-f26u"
HOME_AGENTS_MARKER = "HOME-AGENTS-MARKER"
PROJECT_AGENTS_MARKER = "PROJECT-AGENTS-MARKER"
PLAIN_CWD_AGENTS_MARKER = "PLAIN-CWD-AGENTS-MARKER"


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


async def _prepare(
    root_uri: str,
    behavior_uris: list[str],
    discovery: AppBundleDiscovery | None = None,
):
    """Run the real app-cli compose+prepare path over local bundles."""
    discovery = discovery or AppBundleDiscovery(search_paths=[])
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


async def _install_system_prompt_factory(
    prepared, session_cwd: Path, *, is_resumed: bool = False
):
    """Install foundation's real system-prompt factory over the prepared bundle.

    The session itself is a mock -- no provider, no modules -- so this exercises
    the mention-expansion path without needing a live session.
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
        await prepared.create_session(session_cwd=session_cwd, is_resumed=is_resumed)

    assert fake_context.factory is not None, (
        "foundation registered no system-prompt factory -- the bundle carried "
        "neither an instruction nor context"
    )
    return fake_context.factory, session.coordinator.hooks.emit


async def _build_system_prompt(prepared, session_cwd: Path) -> tuple[str, list]:
    """Return one render from foundation's real system-prompt factory."""
    factory, emit = await _install_system_prompt_factory(prepared, session_cwd)
    return await factory(), list(emit.await_args_list)


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


async def test_agents_tail_follows_preserved_root_instruction_after_behaviors(
    tmp_path: Path,
) -> None:
    """The final instruction retains the root body, then both CLI mentions."""
    root_uri = _write_root_bundle(tmp_path)
    behavior_uri = _write_behavior_bundle(tmp_path)
    original_root_instruction = (
        await load_bundle(root_uri, registry=AppBundleDiscovery(search_paths=[]).registry)
    ).instruction

    prepared = await _prepare(root_uri, [behavior_uri])

    assert prepared.bundle.instruction == (
        f"{original_root_instruction}\n\n{_AGENTS_INSTRUCTION_TAIL}"
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
    assert prepared.bundle.instruction.endswith(_AGENTS_INSTRUCTION_TAIL)
    assert (
        prepared.bundle.instruction.index(BEHAVIOR_MARKER)
        < prepared.bundle.instruction.index("@~/.amplifier/AGENTS.md")
        < prepared.bundle.instruction.index("@.amplifier/AGENTS.md")
    )


async def test_empty_root_without_behaviors_gets_only_agents_tail(tmp_path: Path) -> None:
    """A missing instruction still registers both optional AGENTS.md mentions."""
    prepared = await _prepare(_write_root_bundle(tmp_path, body=""), [])

    assert prepared.bundle.instruction == _AGENTS_INSTRUCTION_TAIL


async def test_agents_tail_reads_home_and_project_files_but_not_plain_cwd_file(
    tmp_path: Path, isolated_home: Path
) -> None:
    """The CLI tail uses foundation's home and session-CWD mention resolution."""
    home = isolated_home
    session_cwd = tmp_path / "project"
    (home / ".amplifier").mkdir(parents=True, exist_ok=True)
    (home / ".amplifier" / "AGENTS.md").write_text(HOME_AGENTS_MARKER, encoding="utf-8")
    (session_cwd / ".amplifier").mkdir(parents=True)
    (session_cwd / ".amplifier" / "AGENTS.md").write_text(
        PROJECT_AGENTS_MARKER, encoding="utf-8"
    )
    (session_cwd / "AGENTS.md").write_text(PLAIN_CWD_AGENTS_MARKER, encoding="utf-8")
    prepared = await _prepare(_write_root_bundle(tmp_path, body=""), [])
    prompt, _ = await _build_system_prompt(prepared, session_cwd)

    assert prompt.count(HOME_AGENTS_MARKER) == 1
    assert prompt.count(PROJECT_AGENTS_MARKER) == 1
    assert PLAIN_CWD_AGENTS_MARKER not in prompt


async def test_agents_tail_files_are_optional_and_refreshed_each_request(
    tmp_path: Path, isolated_home: Path
) -> None:
    """Missing files do not fail, then later-created and changed files are re-read."""
    home = isolated_home
    session_cwd = tmp_path / "project"
    session_cwd.mkdir()
    prepared = await _prepare(_write_root_bundle(tmp_path, body=""), [])
    factory, _ = await _install_system_prompt_factory(prepared, session_cwd)

    prompt_without_files = await factory()
    assert HOME_AGENTS_MARKER not in prompt_without_files
    assert PROJECT_AGENTS_MARKER not in prompt_without_files

    (home / ".amplifier").mkdir(parents=True, exist_ok=True)
    home_agents = home / ".amplifier" / "AGENTS.md"
    home_agents.write_text(f"{HOME_AGENTS_MARKER}-ONE", encoding="utf-8")
    (session_cwd / ".amplifier").mkdir()
    project_agents = session_cwd / ".amplifier" / "AGENTS.md"
    project_agents.write_text(f"{PROJECT_AGENTS_MARKER}-ONE", encoding="utf-8")

    prompt_after_creation = await factory()
    assert prompt_after_creation.count(f"{HOME_AGENTS_MARKER}-ONE") == 1
    assert prompt_after_creation.count(f"{PROJECT_AGENTS_MARKER}-ONE") == 1

    home_agents.write_text(f"{HOME_AGENTS_MARKER}-TWO", encoding="utf-8")
    project_agents.write_text(f"{PROJECT_AGENTS_MARKER}-TWO", encoding="utf-8")

    refreshed_prompt = await factory()
    assert refreshed_prompt.count(f"{HOME_AGENTS_MARKER}-TWO") == 1
    assert refreshed_prompt.count(f"{PROJECT_AGENTS_MARKER}-TWO") == 1
    assert f"{HOME_AGENTS_MARKER}-ONE" not in refreshed_prompt
    assert f"{PROJECT_AGENTS_MARKER}-ONE" not in refreshed_prompt


async def test_root_resume_uses_agents_tail_and_resolves_files(
    tmp_path: Path, isolated_home: Path
) -> None:
    """A root PreparedBundle creates the same dynamic prompt on resume."""
    session_cwd = tmp_path / "project"
    (isolated_home / ".amplifier").mkdir(exist_ok=True)
    (isolated_home / ".amplifier" / "AGENTS.md").write_text(
        HOME_AGENTS_MARKER, encoding="utf-8"
    )
    (session_cwd / ".amplifier").mkdir(parents=True)
    (session_cwd / ".amplifier" / "AGENTS.md").write_text(
        PROJECT_AGENTS_MARKER, encoding="utf-8"
    )

    prepared = await _prepare(_write_root_bundle(tmp_path, body=""), [])
    factory, _ = await _install_system_prompt_factory(
        prepared, session_cwd, is_resumed=True
    )
    prompt = await factory()

    assert prepared.bundle.instruction == _AGENTS_INSTRUCTION_TAIL
    assert prompt.count(HOME_AGENTS_MARKER) == 1
    assert prompt.count(PROJECT_AGENTS_MARKER) == 1


async def test_reusing_registry_does_not_accumulate_agents_tail(tmp_path: Path) -> None:
    """A registry caches loaded roots, so injection must not mutate that cache."""
    root_uri = _write_root_bundle(tmp_path)
    discovery = AppBundleDiscovery(search_paths=[])

    first = await _prepare(root_uri, [], discovery)
    second = await _prepare(root_uri, [], discovery)

    assert first.bundle.instruction == second.bundle.instruction
    assert first.bundle.instruction.count("@~/.amplifier/AGENTS.md") == 1
    assert first.bundle.instruction.count("@.amplifier/AGENTS.md") == 1
