"""Regression tests: compose notify's BEHAVIORS, never its ROOT bundle.

Background
----------
``_build_notification_behaviors()`` used to prepend the notify ROOT URI
(``git+https://github.com/microsoft/amplifier-bundle-notify@main``) before the
two body-less behavior YAMLs, described in-code as "a minimal marker that just
identifies the repo and ensures the bundle gets cached with proper SHA metadata
(fixes the 'unknown' version issue during `amplifier update`)".

That marker was not minimal. notify's root ``bundle.md`` carries an 82-line
README body, and ``Bundle.compose()`` replaces the root instruction whenever a
composed bundle has a non-empty markdown body -- the defect fixed in #315 by
``_preserve_root_instruction()``. #315 removed the *damage*; this removes the
*trigger*. Both stay: the guard is defense in depth for every OTHER
body-carrying behavior a user might compose.

Two things are pinned here, because removing the root URI is only safe if the
second one holds:

1. Every URI this builder returns is a ``#subdirectory=behaviors/`` URI --
   never a bare root. Same rule every sibling builder already follows.
2. A behavior URI and the root URI resolve to ONE cache entry, so fetching a
   behavior populates the very entry ``amplifier update`` reads for the root.
   The root composition was never what supplied the SHA.

If amplifier-foundation ever makes its git cache key fragment-sensitive,
test (2) fails loudly here instead of silently reintroducing "unknown".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from amplifier_app_cli.lib.settings import NotificationFlags
from amplifier_app_cli.runtime.config import (
    _build_app_cli_behaviors,
    _build_modes_behaviors,
    _build_notification_behaviors,
    _build_skills_behaviors,
    _build_wayfinder_behaviors,
)

NOTIFY_ROOT_URI = "git+https://github.com/microsoft/amplifier-bundle-notify@main"

ALL_FLAG_COMBOS = [
    NotificationFlags(desktop_enabled=True, push_enabled=False),
    NotificationFlags(desktop_enabled=False, push_enabled=True),
    NotificationFlags(desktop_enabled=True, push_enabled=True),
]


# ---------------------------------------------------------------------------
# 1. Behavior URIs only -- never a bare root
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flags", ALL_FLAG_COMBOS, ids=["desktop", "push", "both"])
def test_notification_behaviors_never_return_a_bare_root_uri(flags):
    """No enablement combination may emit the notify ROOT bundle URI.

    The root bundle.md carries a README body; composing it is what made
    #315 necessary in the first place.
    """
    behaviors = _build_notification_behaviors(flags)

    assert NOTIFY_ROOT_URI not in behaviors, (
        f"_build_notification_behaviors({flags!r}) returned the bare notify ROOT "
        f"URI: {behaviors!r}. That bundle's markdown body enters compose() and "
        "is only made harmless by _preserve_root_instruction() (#315). Compose "
        "the #subdirectory=behaviors/*.yaml URIs instead -- they carry no body, "
        "and they populate the same git cache entry `amplifier update` reads."
    )


@pytest.mark.parametrize("flags", ALL_FLAG_COMBOS, ids=["desktop", "push", "both"])
def test_every_notification_uri_targets_a_behavior_subdirectory(flags):
    """Stronger than the bare-root check: every URI must name a behavior file.

    Catches a future regression that composes some *other* body-carrying
    entry point of the same repo rather than the exact root string.
    """
    behaviors = _build_notification_behaviors(flags)

    assert behaviors, "expected at least one behavior URI when a flag is enabled"
    for uri in behaviors:
        assert "#subdirectory=behaviors/" in uri, (
            f"{uri!r} does not point at a behavior file. Only body-less "
            "behavior YAMLs may be composed onto the user's root bundle."
        )
        assert uri.endswith(".yaml"), f"{uri!r} is not a behavior YAML"


def test_desktop_only_composes_exactly_the_desktop_behavior():
    behaviors = _build_notification_behaviors(
        NotificationFlags(desktop_enabled=True, push_enabled=False)
    )
    assert behaviors == [
        NOTIFY_ROOT_URI + "#subdirectory=behaviors/desktop-notifications.yaml"
    ]


def test_push_only_composes_exactly_the_push_behavior():
    behaviors = _build_notification_behaviors(
        NotificationFlags(desktop_enabled=False, push_enabled=True)
    )
    assert behaviors == [
        NOTIFY_ROOT_URI + "#subdirectory=behaviors/push-notifications.yaml"
    ]


def test_both_enabled_composes_both_behaviors_and_nothing_else():
    behaviors = _build_notification_behaviors(
        NotificationFlags(desktop_enabled=True, push_enabled=True)
    )
    assert behaviors == [
        NOTIFY_ROOT_URI + "#subdirectory=behaviors/desktop-notifications.yaml",
        NOTIFY_ROOT_URI + "#subdirectory=behaviors/push-notifications.yaml",
    ]


# ---------------------------------------------------------------------------
# 2. The pattern, not just the one instance
# ---------------------------------------------------------------------------


def test_no_always_on_behavior_builder_composes_a_bare_root_bundle():
    """Audit guard: every always-composed builder must be behavior-only.

    One root-composed bundle is a bug; the same shape appearing again is the
    finding. modes/skills/wayfinder/app-cli were already behavior-only when
    this was written -- this keeps them that way.
    """
    builders = {
        "_build_modes_behaviors": _build_modes_behaviors(),
        "_build_skills_behaviors": _build_skills_behaviors(),
        "_build_wayfinder_behaviors": _build_wayfinder_behaviors(),
        "_build_app_cli_behaviors": _build_app_cli_behaviors(),
        "_build_notification_behaviors": _build_notification_behaviors(
            NotificationFlags(desktop_enabled=True, push_enabled=True)
        ),
    }

    offenders = {
        name: [uri for uri in uris if "#subdirectory=" not in uri]
        for name, uris in builders.items()
    }
    offenders = {name: uris for name, uris in offenders.items() if uris}

    assert not offenders, (
        f"These builders compose a ROOT bundle URI: {offenders!r}. A root "
        "bundle.md with a non-empty body replaces the user's system prompt in "
        "Bundle.compose(); _preserve_root_instruction() (#315) then drops it "
        "with a warning. Compose the behavior file directly instead."
    )


# ---------------------------------------------------------------------------
# 3. The invariant that makes removing the root URI safe
# ---------------------------------------------------------------------------


def test_behavior_uri_and_root_uri_share_one_git_cache_entry():
    """The SHA-metadata motivation, preserved without composing a body.

    ``amplifier update`` resolves "notify" through WELL_KNOWN_BUNDLES to the
    ROOT URI and asks ``GitSourceHandler.get_status()`` for its cached commit.
    That lookup keys on ``sha256("<git_url>@<ref>")`` -- the ``#subdirectory=``
    fragment is not part of the key -- so a behavior fetch fills the entry the
    root's status check reads.

    Pure path arithmetic: no clone, no network, no filesystem writes.
    """
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    handler = GitSourceHandler()
    cache_dir = Path("/tmp/does-not-need-to-exist")

    behavior_uris = _build_notification_behaviors(
        NotificationFlags(desktop_enabled=True, push_enabled=True)
    )
    root_path = handler._get_cache_path(parse_uri(NOTIFY_ROOT_URI), cache_dir)

    for uri in behavior_uris:
        behavior_path = handler._get_cache_path(parse_uri(uri), cache_dir)
        assert behavior_path == root_path, (
            f"{uri} caches to {behavior_path.name} but `amplifier update` reads "
            f"{root_path.name} for the notify root. The git cache key has become "
            "fragment-sensitive, so composing behaviors no longer supplies the "
            "root's SHA metadata and `amplifier update` will report 'unknown' "
            "for notify. Restore SHA tracking WITHOUT composing the root "
            "bundle.md (its body would clobber the user's system prompt)."
        )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Builds a git+file:// URI from a filesystem path; a Windows drive-letter "
        "path ('C:/...') is not expressible in the host/path split that "
        "GitSourceHandler._build_git_url() performs. The fragment-insensitive "
        "cache-key invariant this test exercises end-to-end is also pinned "
        "platform-independently by "
        "test_behavior_uri_and_root_uri_share_one_git_cache_entry."
    ),
)
def test_behavior_only_fetch_supplies_the_root_sha_end_to_end(tmp_path):
    """Fail-before / pass-after for the original "unknown" symptom.

    Reproduces the reported symptom (root status with nothing fetched ->
    cached_commit None -> rendered "unknown"), then shows that fetching ONLY
    the behavior subdirectory URIs -- never the root -- clears it.
    """
    import asyncio

    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    repo = tmp_path / "amplifier-bundle-notify"
    (repo / "behaviors").mkdir(parents=True)
    # Shaped like the real thing: a root bundle.md that DOES carry a body.
    (repo / "bundle.md").write_text(
        "---\nname: notify\n---\n\n# Notify Bundle\n\nREADME body.\n",
        encoding="utf-8",
    )
    (repo / "behaviors" / "desktop-notifications.yaml").write_text(
        "name: notify-desktop\nhooks: []\n", encoding="utf-8"
    )

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "initial")
    real_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    root_uri = f"git+file://{repo.as_posix()}@main"
    behavior_uri = root_uri + "#subdirectory=behaviors/desktop-notifications.yaml"

    handler = GitSourceHandler()

    async def scenario():
        # FAIL-BEFORE: nothing from this repo fetched at all.
        empty_cache = tmp_path / "cache-empty"
        empty_cache.mkdir()
        before = await handler.get_status(parse_uri(root_uri), empty_cache)

        # PASS-AFTER: ONLY the behavior URI is fetched. The root never is.
        behaviors_cache = tmp_path / "cache-behaviors-only"
        behaviors_cache.mkdir()
        await handler.resolve(parse_uri(behavior_uri), behaviors_cache)
        after = await handler.get_status(parse_uri(root_uri), behaviors_cache)
        return before, after

    before, after = asyncio.run(scenario())

    assert before.cached_commit is None, (
        "Expected the original symptom: with nothing fetched, the notify root "
        f"has no cached commit and renders as 'unknown'. Got {before.cached_commit!r}."
    )
    assert after.cached_commit == real_sha, (
        "Fetching only the behavior subdirectory URI must populate the root's "
        f"cache entry. Expected {real_sha}, got {after.cached_commit!r}. Without "
        "this, removing the root URI from _build_notification_behaviors() would "
        "reintroduce the 'unknown' version in `amplifier update`."
    )
