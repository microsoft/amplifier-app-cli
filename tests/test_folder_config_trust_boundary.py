"""Tests for the folder-config trust boundary (security fix).

Verifies that code-introducing settings (module/bundle sources, source overrides,
app/added bundle URIs, provider sources) are only honored from trusted scopes
(global + session), never from project/local scopes that live inside the
working directory (a possibly-cloned repository).

Test layout:
    1. get_trusted_settings — merges only global+session; ignores project+local.
    2. get_module_sources(trusted_only=True) — excludes project-scope source,
       includes global-scope source; default includes both.
    3. get_added_bundles(trusted_only=True) — excludes project-scope added bundle.
    4. get_provider_overrides(trusted_only=True) — excludes project-scope provider source.
    5. get_source_overrides(trusted_only=True) — excludes project-scope overrides.<id>.source.
    6. get_config_overrides() — STILL includes project-scope overrides.<id>.config
       (regression guard: config values remain folder-honorable).
    7. Management path sanity: get_app_bundles() (default) still returns
       project-scope app bundles so list/management UX is unchanged.
"""

from pathlib import Path

import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict) -> None:
    """Write a YAML file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


def _make_settings(
    tmp_path: Path,
    *,
    global_data: dict | None = None,
    project_data: dict | None = None,
    local_data: dict | None = None,
    session_data: dict | None = None,
) -> AppSettings:
    """Build an AppSettings pointed at temp-dir files.

    Files are only created when non-None data is provided.
    """
    global_file = tmp_path / "home" / ".amplifier" / "settings.yaml"
    project_file = tmp_path / "cwd" / ".amplifier" / "settings.yaml"
    local_file = tmp_path / "cwd" / ".amplifier" / "settings.local.yaml"
    session_file = (
        tmp_path
        / "home"
        / ".amplifier"
        / "projects"
        / "slug"
        / "sessions"
        / "s1"
        / "settings.yaml"
    )

    if global_data is not None:
        _write_yaml(global_file, global_data)
    if project_data is not None:
        _write_yaml(project_file, project_data)
    if local_data is not None:
        _write_yaml(local_file, local_data)
    if session_data is not None:
        _write_yaml(session_file, session_data)

    paths = SettingsPaths(
        global_settings=global_file,
        project_settings=project_file,
        local_settings=local_file,
        session_settings=session_file if session_data is not None else None,
    )
    return AppSettings(paths=paths)


# ---------------------------------------------------------------------------
# 1. get_trusted_settings — merges only global + session; ignores project/local
# ---------------------------------------------------------------------------


class TestGetTrustedSettings:
    def test_includes_global_key(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={"key": "from-global"},
            project_data={"other": "from-project"},
        )
        result = settings.get_trusted_settings()
        assert result.get("key") == "from-global"

    def test_excludes_project_key(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={"g": 1},
            project_data={"project_only": "should-be-absent"},
        )
        result = settings.get_trusted_settings()
        assert "project_only" not in result

    def test_excludes_local_key(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={"g": 1},
            local_data={"local_only": "should-be-absent"},
        )
        result = settings.get_trusted_settings()
        assert "local_only" not in result

    def test_includes_session_key(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={"g": 1},
            session_data={"session_key": "from-session"},
        )
        result = settings.get_trusted_settings()
        assert result.get("session_key") == "from-session"

    def test_session_wins_over_global_on_conflict(self, tmp_path: Path) -> None:
        """Session (more-specific trusted scope) must override global on conflict."""
        settings = _make_settings(
            tmp_path,
            global_data={"conflict": "global-value"},
            session_data={"conflict": "session-value"},
        )
        result = settings.get_trusted_settings()
        assert result["conflict"] == "session-value"

    def test_project_wins_in_full_merge_but_not_trusted(self, tmp_path: Path) -> None:
        """Confirms the split: full merge sees project, trusted merge does not."""
        settings = _make_settings(
            tmp_path,
            global_data={"key": "global"},
            project_data={"key": "project"},
        )
        assert settings.get_merged_settings()["key"] == "project"
        assert settings.get_trusted_settings()["key"] == "global"


# ---------------------------------------------------------------------------
# 2. get_module_sources — trusted_only filters project scope
# ---------------------------------------------------------------------------


class TestGetModuleSources:
    def test_trusted_only_excludes_project_scope_source(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={
                "sources": {
                    "modules": {"tool-bash": "git+https://global.example.com/tool-bash"}
                }
            },
            project_data={
                "sources": {
                    "modules": {"tool-evil": "git+https://evil.example.com/pwned"}
                }
            },
        )
        result = settings.get_module_sources(trusted_only=True)
        assert "tool-evil" not in result, (
            "project-scope source must be excluded when trusted_only=True"
        )
        assert "tool-bash" in result, "global-scope source must be present"

    def test_default_includes_both_scopes(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={
                "sources": {
                    "modules": {"tool-bash": "git+https://global.example.com/tool-bash"}
                }
            },
            project_data={
                "sources": {
                    "modules": {"tool-extra": "git+https://project.example.com/extra"}
                }
            },
        )
        result = settings.get_module_sources()
        assert "tool-bash" in result
        assert "tool-extra" in result

    def test_trusted_only_project_override_of_global_key_is_blocked(
        self, tmp_path: Path
    ) -> None:
        """A project-scope source must not redirect a globally-defined module."""
        settings = _make_settings(
            tmp_path,
            global_data={
                "sources": {
                    "modules": {
                        "provider-anthropic": "git+https://good.example.com/anthropic"
                    }
                }
            },
            project_data={
                "sources": {
                    "modules": {
                        "provider-anthropic": "git+https://evil.example.com/anthropic"
                    }
                }
            },
        )
        trusted = settings.get_module_sources(trusted_only=True)
        full = settings.get_module_sources()
        assert trusted["provider-anthropic"] == "git+https://good.example.com/anthropic"
        assert full["provider-anthropic"] == "git+https://evil.example.com/anthropic"

    def test_session_source_is_trusted(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={},
            session_data={
                "sources": {"modules": {"tool-x": "git+https://session.example.com/x"}}
            },
        )
        result = settings.get_module_sources(trusted_only=True)
        assert "tool-x" in result


# ---------------------------------------------------------------------------
# 3. get_added_bundles — trusted_only filters project scope
# ---------------------------------------------------------------------------


class TestGetAddedBundles:
    def test_trusted_only_excludes_project_scope_bundle(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={
                "bundle": {
                    "added": {"my-bundle": "git+https://global.example.com/bundle"}
                }
            },
            project_data={
                "bundle": {
                    "added": {"evil-bundle": "git+https://evil.example.com/pwned"}
                }
            },
        )
        result = settings.get_added_bundles(trusted_only=True)
        assert "evil-bundle" not in result
        assert "my-bundle" in result

    def test_default_includes_project_scope_bundle(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={
                "bundle": {
                    "added": {"my-bundle": "git+https://global.example.com/bundle"}
                }
            },
            project_data={
                "bundle": {
                    "added": {"extra-bundle": "git+https://project.example.com/bundle"}
                }
            },
        )
        result = settings.get_added_bundles()
        assert "extra-bundle" in result


# ---------------------------------------------------------------------------
# 4. get_provider_overrides — trusted_only filters project-scope provider source
# ---------------------------------------------------------------------------


class TestGetProviderOverrides:
    def test_trusted_only_excludes_project_scope_provider_source(
        self, tmp_path: Path
    ) -> None:
        """A project-scope config.providers[].source must not reach trusted merge."""
        settings = _make_settings(
            tmp_path,
            global_data={
                "config": {
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "config": {"model": "claude-3-5-sonnet"},
                        }
                    ]
                }
            },
            project_data={
                "config": {
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "source": "git+https://evil.example.com/malicious-provider",
                        }
                    ]
                }
            },
        )
        trusted = settings.get_provider_overrides(trusted_only=True)
        full = settings.get_provider_overrides()

        # trusted must not carry the malicious source
        for provider in trusted:
            if provider.get("module") == "provider-anthropic":
                assert (
                    "source" not in provider
                    or provider["source"]
                    != "git+https://evil.example.com/malicious-provider"
                ), "project-scope provider source must be absent from trusted overrides"

        # full merge includes it
        evil_providers = [
            p
            for p in full
            if p.get("module") == "provider-anthropic"
            and p.get("source") == "git+https://evil.example.com/malicious-provider"
        ]
        assert len(evil_providers) >= 1, (
            "full merge must include the project-scope provider source"
        )

    def test_trusted_only_allows_global_provider(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={
                "config": {
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "source": "git+https://global.example.com/anthropic",
                        }
                    ]
                }
            },
        )
        result = settings.get_provider_overrides(trusted_only=True)
        assert any(p.get("module") == "provider-anthropic" for p in result)

    def test_default_includes_project_scope_provider(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={},
            project_data={
                "config": {
                    "providers": [
                        {
                            "module": "provider-x",
                            "source": "git+https://project.example.com/x",
                        }
                    ]
                }
            },
        )
        result = settings.get_provider_overrides()
        assert any(p.get("module") == "provider-x" for p in result)


# ---------------------------------------------------------------------------
# 5. get_source_overrides — trusted_only excludes project-scope overrides.<id>.source
# ---------------------------------------------------------------------------


class TestGetSourceOverrides:
    def test_trusted_only_excludes_project_scope_override_source(
        self, tmp_path: Path
    ) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={
                "overrides": {
                    "tool-bash": {"source": "git+https://global.example.com/tool-bash"}
                }
            },
            project_data={
                "overrides": {
                    "tool-evil": {"source": "git+https://evil.example.com/pwned"}
                }
            },
        )
        result = settings.get_source_overrides(trusted_only=True)
        assert "tool-evil" not in result
        assert "tool-bash" in result

    def test_default_includes_project_scope_override_source(
        self, tmp_path: Path
    ) -> None:
        settings = _make_settings(
            tmp_path,
            global_data={},
            project_data={
                "overrides": {
                    "tool-extra": {"source": "git+https://project.example.com/extra"}
                }
            },
        )
        result = settings.get_source_overrides()
        assert "tool-extra" in result


# ---------------------------------------------------------------------------
# 6. get_config_overrides — project-scope .config values are STILL honored
# ---------------------------------------------------------------------------


class TestGetConfigOverrides:
    def test_project_scope_config_values_still_included(self, tmp_path: Path) -> None:
        """Regression guard: overrides.<id>.config values are not code-introducing
        and must continue to work from project/local scopes.
        """
        settings = _make_settings(
            tmp_path,
            global_data={},
            project_data={
                "overrides": {
                    "hooks-streaming-ui": {
                        "config": {"ui": {"show_thinking_stream": False}}
                    }
                }
            },
        )
        result = settings.get_config_overrides()
        assert "hooks-streaming-ui" in result, (
            "project-scope overrides.<id>.config must remain visible in get_config_overrides()"
        )
        assert result["hooks-streaming-ui"]["ui"]["show_thinking_stream"] is False

    def test_project_scope_source_excluded_config_included_same_module(
        self, tmp_path: Path
    ) -> None:
        """When a project-scope override has both .source and .config, only .source is dropped
        (by trusted_only=True on get_source_overrides).  The .config half must still be
        present in get_config_overrides() which always reads the full merge.
        """
        settings = _make_settings(
            tmp_path,
            global_data={},
            project_data={
                "overrides": {
                    "tool-bash": {
                        "source": "git+https://evil.example.com/bash",
                        "config": {"allowed_commands": ["ls"]},
                    }
                }
            },
        )
        # source must be absent from trusted source overrides
        assert "tool-bash" not in settings.get_source_overrides(trusted_only=True)

        # config must still be present
        config_overrides = settings.get_config_overrides()
        assert "tool-bash" in config_overrides
        assert config_overrides["tool-bash"]["allowed_commands"] == ["ls"]


# ---------------------------------------------------------------------------
# 7. Management path sanity — get_app_bundles() (default) returns project scope
# ---------------------------------------------------------------------------


class TestManagementPathSanity:
    def test_get_app_bundles_default_includes_project_scope(
        self, tmp_path: Path
    ) -> None:
        """get_app_bundles() with default trusted_only=False must still expose
        project-scope app bundles so that list/management commands keep full visibility.
        """
        settings = _make_settings(
            tmp_path,
            global_data={},
            project_data={
                "bundle": {"app": ["git+https://project.example.com/app-bundle"]}
            },
        )
        result = settings.get_app_bundles()
        assert "git+https://project.example.com/app-bundle" in result

    def test_get_app_bundles_trusted_only_excludes_project_scope(
        self, tmp_path: Path
    ) -> None:
        """trusted_only=True (used at run time) must not let the folder inject app bundles."""
        settings = _make_settings(
            tmp_path,
            global_data={
                "bundle": {"app": ["git+https://global.example.com/safe-bundle"]}
            },
            project_data={
                "bundle": {"app": ["git+https://project.example.com/evil-bundle"]}
            },
        )
        result = settings.get_app_bundles(trusted_only=True)
        assert "git+https://project.example.com/evil-bundle" not in result
        assert "git+https://global.example.com/safe-bundle" in result
