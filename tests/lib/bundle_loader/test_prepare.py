"""Tests for bundle preparation utilities.

Tests for the _build_include_source_resolver function which builds a
callback used to redirect include sources during bundle loading.
"""

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
