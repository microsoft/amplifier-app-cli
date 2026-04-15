"""Tests for _get_bundle_repo_info() in commands/update.py.

Covers the URI fragment normalisation fix: behaviour bundles have a
#subdirectory=… fragment in bundle_source, while the SourceStatus returned
by GitSourceHandler.get_status() carries only the clean repo-root URI (no
fragment).  The function must match both sides after stripping the fragment.
"""

from unittest.mock import MagicMock

from amplifier_app_cli.commands.update import _get_bundle_repo_info

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_URI = (
    "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main"
)
_SUBDIRECTORY_URI = f"{_REPO_URI}#subdirectory=behaviors/env-all.yaml"
_CACHED = "abcdef1234567890"
_REMOTE = "deadbeef12345678"


def _make_source(
    source_uri: str, cached_commit=_CACHED, remote_commit=_REMOTE, has_update=False
):
    """Return a minimal SourceStatus-like mock."""
    s = MagicMock()
    s.source_uri = source_uri
    s.cached_commit = cached_commit
    s.remote_commit = remote_commit
    s.has_update = has_update
    return s


def _make_bundle_status(bundle_source, sources):
    """Return a minimal BundleStatus-like mock."""
    bs = MagicMock()
    bs.bundle_source = bundle_source
    bs.sources = sources
    return bs


# ---------------------------------------------------------------------------
# Core fix: behaviour bundle with #subdirectory= fragment
# ---------------------------------------------------------------------------


def test_subdirectory_bundle_source_matches_clean_source_uri():
    """bundle_source with #subdirectory= must match a source whose URI has no fragment."""
    source = _make_source(_REPO_URI)
    bundle_status = _make_bundle_status(_SUBDIRECTORY_URI, [source])

    result = _get_bundle_repo_info(bundle_status)

    assert result is not None, "Should match despite #subdirectory= fragment"
    assert result["cached_sha"] == _CACHED[:7]
    assert result["remote_sha"] == _REMOTE[:7]
    assert result["has_update"] is False


def test_subdirectory_bundle_returns_correct_has_update_true():
    """has_update=True is propagated correctly for subdirectory bundles."""
    source = _make_source(_REPO_URI, has_update=True)
    bundle_status = _make_bundle_status(_SUBDIRECTORY_URI, [source])

    result = _get_bundle_repo_info(bundle_status)

    assert result is not None
    assert result["has_update"] is True


def test_subdirectory_bundle_null_commits_yield_none_shas():
    """If cached/remote commit are None, sha fields are None (not a crash)."""
    source = _make_source(_REPO_URI, cached_commit=None, remote_commit=None)
    bundle_status = _make_bundle_status(_SUBDIRECTORY_URI, [source])

    result = _get_bundle_repo_info(bundle_status)

    assert result is not None
    assert result["cached_sha"] is None
    assert result["remote_sha"] is None


# ---------------------------------------------------------------------------
# Regression: plain (non-subdirectory) bundles still work
# ---------------------------------------------------------------------------


def test_plain_bundle_source_matches_identical_source_uri():
    """Plain bundles (no fragment) must continue to match exact URI."""
    source = _make_source(_REPO_URI)
    bundle_status = _make_bundle_status(_REPO_URI, [source])

    result = _get_bundle_repo_info(bundle_status)

    assert result is not None
    assert result["cached_sha"] == _CACHED[:7]
    assert result["remote_sha"] == _REMOTE[:7]


def test_plain_bundle_no_matching_source_returns_none():
    """When no source URI matches, return None (no crash, no false positive)."""
    source = _make_source("git+https://github.com/microsoft/other-repo@main")
    bundle_status = _make_bundle_status(_REPO_URI, [source])

    result = _get_bundle_repo_info(bundle_status)

    assert result is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_none_bundle_source_returns_none():
    """bundle_source=None must short-circuit to None immediately."""
    bundle_status = _make_bundle_status(None, [])

    result = _get_bundle_repo_info(bundle_status)

    assert result is None


def test_empty_sources_list_returns_none():
    """Empty sources list must return None gracefully."""
    bundle_status = _make_bundle_status(_REPO_URI, [])

    result = _get_bundle_repo_info(bundle_status)

    assert result is None


def test_first_matching_source_wins_when_multiple_sources():
    """When multiple sources exist, the first matching one is returned."""
    unrelated = _make_source("git+https://github.com/microsoft/unrelated@main")
    matching = _make_source(_REPO_URI, cached_commit="1111111", remote_commit="2222222")
    bundle_status = _make_bundle_status(_REPO_URI, [unrelated, matching])

    result = _get_bundle_repo_info(bundle_status)

    assert result is not None
    assert result["cached_sha"] == "1111111"


def test_sha_truncated_to_7_chars():
    """Commit SHAs are truncated to 7 characters in the returned dict."""
    source = _make_source(_REPO_URI, cached_commit="a" * 40, remote_commit="b" * 40)
    bundle_status = _make_bundle_status(_REPO_URI, [source])

    result = _get_bundle_repo_info(bundle_status)

    assert result is not None
    assert len(result["cached_sha"]) == 7
    assert len(result["remote_sha"]) == 7
