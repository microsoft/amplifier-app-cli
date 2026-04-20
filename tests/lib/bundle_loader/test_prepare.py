"""Tests for bundle preparation utilities.

Tests for the _build_include_source_resolver function which builds a
callback used to redirect include sources during bundle loading.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_app_cli.lib.bundle_loader.prepare import _build_include_source_resolver


class TestBuildIncludeSourceResolver:
    """Tests for _build_include_source_resolver."""

    def test_substring_match_returns_override(self):
        """Key that is a substring of source string returns the override value."""
        overrides = {
            "amplifier-bundle-superpowers": "git+https://github.com/local/superpowers@dev"
        }
        resolver = _build_include_source_resolver(overrides)

        source = "git+https://github.com/microsoft/amplifier-bundle-superpowers@main"
        result = resolver(source)

        assert result == "git+https://github.com/local/superpowers@dev"

    def test_no_match_returns_none(self):
        """When no key matches the source, resolver returns None."""
        overrides = {
            "amplifier-bundle-superpowers": "git+https://github.com/local/superpowers@dev"
        }
        resolver = _build_include_source_resolver(overrides)

        source = "git+https://github.com/microsoft/amplifier-bundle-foundation@main"
        result = resolver(source)

        assert result is None

    def test_fragment_preserved_from_original(self):
        """When original source has a fragment and override has none, fragment is preserved."""
        overrides = {
            "amplifier-bundle-superpowers": "git+https://github.com/local/superpowers@dev"
        }
        resolver = _build_include_source_resolver(overrides)

        source = "git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=behaviors/foo.yaml"
        result = resolver(source)

        assert (
            result
            == "git+https://github.com/local/superpowers@dev#subdirectory=behaviors/foo.yaml"
        )

    def test_override_with_own_fragment_uses_overrides_fragment(self):
        """When override already has a fragment, the override's fragment wins over original's."""
        overrides = {
            "amplifier-bundle-superpowers": "git+https://github.com/local/superpowers@dev#subdirectory=behaviors/custom.yaml"
        }
        resolver = _build_include_source_resolver(overrides)

        source = "git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=behaviors/original.yaml"
        result = resolver(source)

        assert (
            result
            == "git+https://github.com/local/superpowers@dev#subdirectory=behaviors/custom.yaml"
        )

    def test_empty_dict_resolver_always_returns_none(self):
        """Empty overrides dict produces a resolver that always returns None."""
        resolver = _build_include_source_resolver({})

        # Should return None for any source
        assert (
            resolver(
                "git+https://github.com/microsoft/amplifier-bundle-superpowers@main"
            )
            is None
        )
        assert (
            resolver("git+https://github.com/microsoft/amplifier-foundation@main")
            is None
        )
        assert resolver("/local/path/to/bundle") is None

    def test_no_fragment_in_original_no_fragment_appended(self):
        """When original source has no fragment, the override is returned as-is."""
        overrides = {
            "amplifier-bundle-superpowers": "git+https://github.com/local/superpowers@dev"
        }
        resolver = _build_include_source_resolver(overrides)

        source = "git+https://github.com/microsoft/amplifier-bundle-superpowers@main"
        result = resolver(source)

        assert result == "git+https://github.com/local/superpowers@dev"
        assert "#" not in result

    def test_local_path_override_with_fragment_preservation(self):
        """Local path override gets original fragment appended when override has no fragment."""
        overrides = {"amplifier-bundle-superpowers": "/home/user/dev/superpowers"}
        resolver = _build_include_source_resolver(overrides)

        source = "git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=behaviors/foo.yaml"
        result = resolver(source)

        assert result == "/home/user/dev/superpowers#subdirectory=behaviors/foo.yaml"

    # --- Issue #257: namespace:path references must not be overridden ---

    def test_namespace_path_not_overridden_when_key_substring_matches(self):
        """Issue #257: override key 'foo' must NOT redirect 'foo:behaviors/extra'.

        Namespace:path references resolve via the bundle registry's namespace
        lookup. Substring-matching them against override keys redirects the
        include and triggers false-positive cycle detection in foundation,
        silently dropping the sub-bundle and its agents.
        """
        overrides = {"foo": "git+ssh://git@github.com/foo/amplifier-bundle-foo@main"}
        resolver = _build_include_source_resolver(overrides)

        result = resolver("foo:behaviors/extra")

        assert result is None

    def test_namespace_path_not_overridden_cross_bundle_collision(self):
        """Issue #257: cross-bundle substring collision in namespace:path is skipped.

        A user with overrides for bundle 'foo' must not accidentally redirect
        includes for an unrelated namespace 'foo-team'.
        """
        overrides = {"foo": "git+ssh://example.com/foo-bundle@main"}
        resolver = _build_include_source_resolver(overrides)

        result = resolver("foo-team:behaviors/policies")

        assert result is None

    def test_uri_override_still_works_when_namespace_name_is_substring(self):
        """Issue #257 regression guard: legitimate URI overrides continue to work.

        The namespace:path guard must not affect substring matching on real URIs.
        """
        overrides = {"amplifier-bundle-foo": "/local/path/to/foo"}
        resolver = _build_include_source_resolver(overrides)

        # This is a URI (git+https://...), not namespace:path.
        result = resolver("git+https://github.com/org/amplifier-bundle-foo@main")

        assert result == "/local/path/to/foo"

    def test_file_uri_override_still_matches(self):
        """file:// URIs are not namespace:path and must still match by substring."""
        overrides = {"my-bundle": "/local/override"}
        resolver = _build_include_source_resolver(overrides)

        result = resolver("file:///path/to/my-bundle/bundle.md")

        assert result == "/local/override"

    def test_git_ssh_uri_override_still_matches(self):
        """git+ssh:// URIs are not namespace:path and must still match by substring."""
        overrides = {"amplifier-bundle-foo": "/local/foo"}
        resolver = _build_include_source_resolver(overrides)

        result = resolver("git+ssh://git@github.com/org/amplifier-bundle-foo@main")

        assert result == "/local/foo"

    def test_plain_local_path_override_still_matches(self):
        """Plain local paths (no ':' at all) are not namespace:path and still match."""
        overrides = {"my-bundle": "/override"}
        resolver = _build_include_source_resolver(overrides)

        result = resolver("/home/user/dev/my-bundle")

        assert result == "/override"


class TestLoadAndPrepareBundleSourceOverrides:
    """Tests for bundle_source_overrides parameter in load_and_prepare_bundle."""

    @pytest.mark.asyncio
    async def test_bundle_overrides_sets_resolver_on_registry(self):
        """When bundle_source_overrides is provided, set_include_source_resolver is called on registry with a callable."""
        from amplifier_app_cli.lib.bundle_loader.prepare import load_and_prepare_bundle

        overrides = {"amplifier-bundle-superpowers": "/local/path"}

        mock_registry = MagicMock()
        mock_discovery = MagicMock()
        mock_discovery.find.return_value = "file:///path/to/bundle.yaml"
        mock_discovery.registry = mock_registry

        mock_bundle = MagicMock()
        mock_prepared = MagicMock()
        mock_bundle.prepare = AsyncMock(return_value=mock_prepared)

        with patch(
            "amplifier_app_cli.lib.bundle_loader.prepare.load_bundle",
            new_callable=AsyncMock,
        ) as mock_load_bundle:
            mock_load_bundle.return_value = mock_bundle

            await load_and_prepare_bundle(
                "my-bundle",
                mock_discovery,
                bundle_source_overrides=overrides,
            )

        # set_include_source_resolver was called once with a callable
        mock_registry.set_include_source_resolver.assert_called_once()
        call_args = mock_registry.set_include_source_resolver.call_args[0]
        assert callable(call_args[0])

    @pytest.mark.asyncio
    async def test_no_bundle_overrides_skips_resolver(self):
        """When bundle_source_overrides is None (default), set_include_source_resolver is NOT called."""
        from amplifier_app_cli.lib.bundle_loader.prepare import load_and_prepare_bundle

        mock_registry = MagicMock()
        mock_discovery = MagicMock()
        mock_discovery.find.return_value = "file:///path/to/bundle.yaml"
        mock_discovery.registry = mock_registry

        mock_bundle = MagicMock()
        mock_prepared = MagicMock()
        mock_bundle.prepare = AsyncMock(return_value=mock_prepared)

        with patch(
            "amplifier_app_cli.lib.bundle_loader.prepare.load_bundle",
            new_callable=AsyncMock,
        ) as mock_load_bundle:
            mock_load_bundle.return_value = mock_bundle

            await load_and_prepare_bundle(
                "my-bundle",
                mock_discovery,
            )

        # set_include_source_resolver was NOT called
        mock_registry.set_include_source_resolver.assert_not_called()
