"""Tests for the runtime credential-fallback hardening.

Covers `_validate_provider_credentials()` (amplifier_app_cli.runtime.config),
added alongside the reuse-or-separate multi-instance credential wizard
(docs/designs/provider-instance-credentials.md). Without this guard, an
unresolved *separate* credential placeholder expands to "" at runtime and
several provider modules treat that as "not configured", silently falling
back to their own canonical ambient env var -- routing the "separate"
instance through the WRONG account's key. This must fail loudly instead.
"""

from pathlib import Path
import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_app_cli.runtime.config import _validate_provider_credentials


def _provider_info(env_var: str = "OPENAI_API_KEY", required: bool = True) -> dict:
    return {
        "display_name": "OpenAI",
        "config_fields": [
            {
                "id": "api_key",
                "display_name": "API Key",
                "field_type": "secret",
                "prompt": "Enter your API key",
                "env_var": env_var,
                "required": required,
            }
        ],
    }


class TestValidateProviderCredentials:
    @pytest.mark.asyncio
    async def test_unset_separate_binding_fails_loud(self, monkeypatch):
        """The core hardening case: a required secret field whose ${VAR}
        placeholder resolves to nothing must raise BEFORE session mount,
        instead of silently letting expand_env_vars turn it into "" (which
        would let the provider module fall back to its own ambient var --
        e.g. a *different* account's OPENAI_API_KEY)."""
        monkeypatch.delenv("OPENAI_WORK_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-shared-account-key")

        providers = [
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "${OPENAI_WORK_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
        ), pytest.raises(ValueError, match="OPENAI_WORK_API_KEY"):
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )

    @pytest.mark.asyncio
    async def test_set_credential_does_not_raise(self, monkeypatch):
        """A required secret field whose env var IS set must pass silently."""
        monkeypatch.setenv("OPENAI_WORK_API_KEY", "sk-distinct-value")

        providers = [
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "${OPENAI_WORK_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
        ):
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )  # must not raise

    @pytest.mark.asyncio
    async def test_shared_binding_with_set_var_does_not_raise(self, monkeypatch):
        """Two instances sharing the SAME credential env var (the "reuse"
        path) must not raise as long as that shared var is set -- this is
        the normal, intended shared-credential configuration."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared-value")

        providers = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-opus",
                "config": {"api_key": "${ANTHROPIC_API_KEY}"},
            },
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "config": {"api_key": "${ANTHROPIC_API_KEY}"},
            },
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="ANTHROPIC_API_KEY"),
        ):
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )  # must not raise

    @pytest.mark.asyncio
    async def test_optional_keyless_secret_field_unset_does_not_raise(self, monkeypatch):
        """A secret field declared `required=False` (e.g. a local/keyless
        Chat Completions server) must be left alone even when unset --
        the fail-loud guard is scoped to REQUIRED credential fields only."""
        monkeypatch.delenv("LOCAL_SERVER_API_KEY", raising=False)

        providers = [
            {
                "module": "provider-chat-completions",
                "id": "local-server",
                "config": {"api_key": "${LOCAL_SERVER_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="LOCAL_SERVER_API_KEY", required=False),
        ):
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )  # must not raise

    @pytest.mark.asyncio
    async def test_missing_provider_metadata_skips_validation(self, monkeypatch):
        """When provider metadata can't be loaded (custom/removed provider,
        import error, etc.) validation is skipped for that entry rather
        than blocking session start -- consistent with how the rest of
        this module treats a missing get_provider_info() result."""
        monkeypatch.delenv("SOME_UNSET_VAR", raising=False)

        providers = [
            {
                "module": "provider-custom-thing",
                "id": "custom",
                "config": {"api_key": "${SOME_UNSET_VAR}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=None,
        ):
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )  # must not raise

    @pytest.mark.asyncio
    async def test_literal_value_is_not_validated(self, monkeypatch):
        """A literal (non-placeholder) config value is untouched by this
        guard -- it isn't an unresolved env var reference at all."""
        providers = [
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "sk-literal-value-not-a-placeholder"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
        ):
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )  # must not raise

    @pytest.mark.asyncio
    async def test_inline_default_placeholder_is_not_validated(self, monkeypatch):
        """A placeholder carrying an inline default (${VAR:default}) already
        has its own "unset" handling via expand_env_vars -- this guard only
        concerns itself with bare ${VAR} placeholders."""
        monkeypatch.delenv("OPENAI_WORK_API_KEY", raising=False)

        providers = [
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "${OPENAI_WORK_API_KEY:not-needed}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
        ):
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )  # must not raise

    @pytest.mark.asyncio
    async def test_multiple_providers_identifies_the_failing_instance(self, monkeypatch):
        """With several configured providers, the error must name the
        instance and env var actually at fault, not a generic message."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fine")
        monkeypatch.delenv("OPENAI_WORK_API_KEY", raising=False)

        providers = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-opus",
                "config": {"api_key": "${ANTHROPIC_API_KEY}"},
            },
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "${OPENAI_WORK_API_KEY}"},
            },
        ]

        def _info(module_id: str):
            if module_id == "provider-anthropic":
                return _provider_info(env_var="ANTHROPIC_API_KEY")
            return _provider_info(env_var="OPENAI_WORK_API_KEY")

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            side_effect=_info,
        ), pytest.raises(ValueError) as exc_info:
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )

        assert "OPENAI_WORK_API_KEY" in str(exc_info.value)
        assert "openai-work" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_dict_or_non_module_entries_are_skipped(self):
        """Malformed provider entries must not crash the guard."""
        providers = [
            "not-a-dict",
            {"config": {"api_key": "${SOMETHING}"}},  # no "module"
            {"module": 123, "config": {"api_key": "${SOMETHING}"}},  # bad module type
            {"module": "provider-x", "config": "not-a-dict"},  # bad config type
        ]
        await _validate_provider_credentials(
            providers, prepared_resolver=MagicMock()
        )  # must not raise

    @pytest.mark.asyncio
    async def test_source_with_set_credential_skips_resolution_and_metadata(
        self, monkeypatch
    ):
        """Configured sources stay lazy when no unresolved value needs checking."""
        monkeypatch.setenv("EXAMPLE_API_KEY", "set")
        resolver = MagicMock()
        resolver.async_resolve = AsyncMock()
        providers = [
            {
                "module": "provider-example",
                "source": "configured-source",
                "config": {"api_key": "${EXAMPLE_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info"
        ) as get_info:
            await _validate_provider_credentials(
                providers, prepared_resolver=resolver
            )

        resolver.async_resolve.assert_not_awaited()
        get_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_source_with_unset_credential_uses_canonical_metadata(
        self, monkeypatch, tmp_path
    ):
        """A configured source is resolved before its required key is checked."""
        monkeypatch.delenv("EXAMPLE_WORK_API_KEY", raising=False)
        source = MagicMock()
        source.resolve.return_value = tmp_path
        resolver = MagicMock()
        resolver.async_resolve = AsyncMock(return_value=source)
        providers = [
            {
                "module": "provider-example",
                "id": "example-work",
                "source": "configured-source",
                "config": {"api_key": "${EXAMPLE_WORK_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="EXAMPLE_WORK_API_KEY"),
        ) as get_info, pytest.raises(ValueError, match="EXAMPLE_WORK_API_KEY"):
            await _validate_provider_credentials(
                providers, prepared_resolver=resolver
            )

        resolver.async_resolve.assert_awaited_once_with(
            "provider-example", source_hint="configured-source"
        )
        get_info.assert_called_once_with("provider-example", source_path=tmp_path)

    @pytest.mark.asyncio
    async def test_source_metadata_failure_does_not_fall_back_to_ambient(
        self, monkeypatch, tmp_path
    ):
        """A source-specific metadata failure is actionable instead of fail-soft."""
        monkeypatch.delenv("EXAMPLE_WORK_API_KEY", raising=False)
        source = MagicMock()
        source.resolve.return_value = tmp_path
        resolver = MagicMock()
        resolver.async_resolve = AsyncMock(return_value=source)
        providers = [
            {
                "module": "provider-example",
                "source": "configured-source",
                "config": {"api_key": "${EXAMPLE_WORK_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            side_effect=ImportError("loaded from a different source"),
        ) as get_info, pytest.raises(
            ValueError, match="Could not load configured provider source"
        ):
            await _validate_provider_credentials(
                providers, prepared_resolver=resolver
            )

        get_info.assert_called_once_with("provider-example", source_path=tmp_path)

    @pytest.mark.asyncio
    async def test_no_source_preserves_fail_soft_metadata_lookup(
        self, monkeypatch
    ):
        """Legacy entries look up metadata without a source-path argument."""
        monkeypatch.delenv("EXAMPLE_WORK_API_KEY", raising=False)
        providers = [
            {
                "module": "provider-example",
                "config": {"api_key": "${EXAMPLE_WORK_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info", return_value=None
        ) as get_info:
            await _validate_provider_credentials(
                providers, prepared_resolver=MagicMock()
            )

        get_info.assert_called_once_with("provider-example")

    @pytest.mark.asyncio
    async def test_optional_unset_secret_is_allowed_after_source_metadata(
        self, monkeypatch, tmp_path
    ):
        """Optional secret fields remain allowed after canonical source lookup."""
        monkeypatch.delenv("OPTIONAL_EXAMPLE_API_KEY", raising=False)
        source = MagicMock()
        source.resolve.return_value = tmp_path
        resolver = MagicMock()
        resolver.async_resolve = AsyncMock(return_value=source)
        providers = [
            {
                "module": "provider-example",
                "source": "configured-source",
                "config": {"api_key": "${OPTIONAL_EXAMPLE_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(
                env_var="OPTIONAL_EXAMPLE_API_KEY", required=False
            ),
        ) as get_info:
            await _validate_provider_credentials(
                providers, prepared_resolver=resolver
            )

        resolver.async_resolve.assert_awaited_once()
        get_info.assert_called_once_with("provider-example", source_path=tmp_path)


# ============================================================
# Integration: the guard actually runs inside resolve_bundle_config(),
# before expand_env_vars() would otherwise silently launder the unset
# placeholder into "".
# ============================================================


class TestValidateProviderCredentialsIntegration:
    @pytest.mark.asyncio
    async def test_resolve_bundle_config_raises_for_unset_separate_credential(
        self, monkeypatch
    ):
        from amplifier_app_cli.runtime.config import resolve_bundle_config

        monkeypatch.delenv("OPENAI_WORK_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-shared-account-key")

        mount_plan = {
            "providers": [
                {
                    "module": "provider-openai",
                    "id": "openai-work",
                    "config": {"api_key": "${OPENAI_WORK_API_KEY}"},
                }
            ],
        }
        mock_prepared = MagicMock()
        mock_prepared.mount_plan = mount_plan
        mock_prepared.bundle.load_agent_metadata = MagicMock()

        settings = MagicMock()
        settings.get_config_overrides.return_value = {}
        settings.get_provider_overrides.return_value = []
        settings.get_tool_overrides.return_value = []
        settings.get_notification_hook_overrides.return_value = []
        settings.get_routing_config.return_value = None
        settings.get_source_overrides.return_value = {}
        settings.get_module_sources.return_value = {}
        settings.get_bundle_sources.return_value = {}

        with (
            patch(
                "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
                new_callable=AsyncMock,
                return_value=mock_prepared,
            ),
            patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
            patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
            patch(
                "amplifier_app_cli.runtime.config.get_provider_info",
                return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
            ),pytest.raises(ValueError, match="OPENAI_WORK_API_KEY")
        ):
            await resolve_bundle_config(bundle_name="test", app_settings=settings)


def _write_standard_provider(source_root: Path, *, required: bool) -> None:
    """Write a source-root provider package accepted by core's real validator."""
    package = source_root / "amplifier_module_provider_example"
    package.mkdir(parents=True)
    package.joinpath("__init__.py").write_text(
        f"""
import os
from pathlib import Path

from amplifier_core import ConfigField, ProviderInfo

__amplifier_module_type__ = "provider"


class ExampleProvider:
    name = "example"

    def get_info(self):
        return ProviderInfo(
            id="example",
            display_name="Example",
            config_fields=[
                ConfigField(
                    id="api_key",
                    display_name="API key",
                    field_type="secret",
                    prompt="API key",
                    env_var="EXAMPLE_WORK_API_KEY",
                    required={required!r},
                )
            ],
        )

    async def list_models(self):
        return []

    async def complete(self, request):
        raise AssertionError("No provider request allowed in this test")

    def parse_tool_calls(self, response):
        return []


async def mount(coordinator, config):
    Path(os.environ["SOURCE_MARKER"]).write_text("mounted")
    await coordinator.mount("providers", ExampleProvider(), name="example")
""".lstrip(),
        encoding="utf-8",
    )


def _write_ambient_entry_point(ambient_root: Path) -> None:
    """Write a conflicting installed package and entry point that stay unused."""
    ambient_root.mkdir()
    package = ambient_root / "amplifier_module_provider_example"
    package.mkdir()
    package.joinpath("__init__.py").write_text(
        """
import os
from pathlib import Path

Path(os.environ["AMBIENT_MARKER"]).write_text("imported")


async def mount(coordinator, config):
    raise AssertionError("ambient entry point must not be loaded")
""".lstrip(),
        encoding="utf-8",
    )
    dist_info = ambient_root / "ambient_example-0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: ambient-example\nVersion: 0.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[amplifier.modules]\n"
        "provider-example = amplifier_module_provider_example:mount\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("required", "value", "expect_error"),
    [
        (True, "configured", False),
        (False, "${EXAMPLE_WORK_API_KEY}", False),
        (True, "${EXAMPLE_WORK_API_KEY}", True),
    ],
    ids=["set-credential", "optional-unset", "required-unset"],
)
def test_fresh_process_source_preflight_uses_configured_provider_root(
    tmp_path, required, value, expect_error
):
    """Exercise CLI preflight, Foundation resolver, and core's real module path.

    The process has both an activated configured source and an installed-looking
    conflicting entry point.  Only the configured source may provide metadata
    or mount the provider.
    """
    source_root = tmp_path / "configured-source"
    ambient_root = tmp_path / "ambient"
    source_marker = tmp_path / "source-marker"
    ambient_marker = tmp_path / "ambient-marker"
    _write_standard_provider(source_root, required=required)
    _write_ambient_entry_point(ambient_root)
    assert (ambient_root / "ambient_example-0.0.dist-info" / "METADATA").is_file()
    assert (
        ambient_root / "ambient_example-0.0.dist-info" / "entry_points.txt"
    ).is_file()

    script = r"""
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
import amplifier_app_cli.lib.bundle_loader as bundle_loader
import amplifier_app_cli.lib.bundle_loader.prepare as prepare
import amplifier_app_cli.paths as paths
from amplifier_app_cli.runtime.config import resolve_bundle_config

source_root = Path(sys.argv[1])
ambient_root = Path(sys.argv[2])
value = sys.argv[3]
sys.path.insert(0, str(ambient_root))
# This mirrors Foundation's activator contract: selected roots are already
# active ahead of installed packages when the prepared resolver is used.
sys.path.insert(0, str(source_root))

entry = {
    "module": "provider-example",
    "id": "example-work",
    "source": str(source_root),
    "config": {"api_key": value},
}
prepared = PreparedBundle(
    mount_plan={"providers": [entry]},
    resolver=BundleModuleResolver({"provider-example": source_root}),
    bundle=Bundle(name="test", base_path=source_root),
)

async def fake_load_and_prepare_bundle(*args, **kwargs):
    return prepared

prepare.load_and_prepare_bundle = fake_load_and_prepare_bundle
bundle_loader.AppBundleDiscovery = lambda **kwargs: object()
paths.get_bundle_search_paths = lambda: []

class Settings:
    def get_app_bundles(self): return []
    def get_source_overrides(self): return {}
    def get_module_sources(self): return {}
    def get_bundle_sources(self): return {}
    def get_provider_overrides(self): return []
    def get_config_overrides(self): return {}
    def get_tool_overrides(self, **kwargs): return []
    def get_notification_hook_overrides(self): return []
    def get_routing_config(self): return None
    def get_notification_flags(self):
        return SimpleNamespace(desktop_enabled=False, push_enabled=False)

async def main():
    try:
        _, configured = await resolve_bundle_config("test", Settings())
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return
    session = await configured.create_session()
    try:
        provider = session.coordinator.get("providers")["example"]
        module = importlib.import_module("amplifier_module_provider_example")
        print(json.dumps({
            "origin": str(Path(module.__file__).resolve()),
            "provider_name": provider.name,
        }))
    finally:
        await session.cleanup()

asyncio.run(main())
"""
    project_root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(None, (str(project_root), os.environ.get("PYTHONPATH")))
        ),
        "SOURCE_MARKER": str(source_marker),
        "AMBIENT_MARKER": str(ambient_marker),
        "EXAMPLE_WORK_API_KEY": "",
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(source_root), str(ambient_root), value],
        check=True,
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    outcome = json.loads(result.stdout)

    assert not ambient_marker.exists()
    if expect_error:
        assert "EXAMPLE_WORK_API_KEY" in outcome["error"]
        assert not source_marker.exists()
    else:
        assert outcome["provider_name"] == "example"
        assert Path(outcome["origin"]) == (
            source_root / "amplifier_module_provider_example" / "__init__.py"
        )
        assert source_marker.read_text(encoding="utf-8") == "mounted"


def test_source_path_rejects_cached_wrong_root_without_entry_point_load(
    monkeypatch, tmp_path
):
    """A cached same-name package from another root cannot satisfy a source pin."""
    from amplifier_app_cli.provider_loader import get_provider_info

    source_root = tmp_path / "configured"
    ambient_root = tmp_path / "ambient"
    _write_standard_provider(source_root, required=True)
    _write_ambient_entry_point(ambient_root)
    module_name = "amplifier_module_provider_example"
    cached_module = type(sys)(module_name)
    cached_module.__file__ = str(ambient_root / module_name / "__init__.py")
    monkeypatch.setitem(sys.modules, module_name, cached_module)

    with patch(
        "amplifier_app_cli.provider_loader.importlib.metadata.entry_points"
    ) as entry_points, pytest.raises(ImportError, match="already loaded"):
        get_provider_info("provider-example", source_path=source_root)

    assert sys.modules[module_name] is cached_module
    entry_points.assert_not_called()


def test_source_path_missing_package_never_falls_back_to_ambient(
    monkeypatch, tmp_path
):
    """An incomplete selected root fails before discovery can import ambient code."""
    from amplifier_app_cli.provider_loader import get_provider_info

    source_root = tmp_path / "configured"
    source_root.mkdir()
    ambient_root = tmp_path / "ambient"
    ambient_marker = tmp_path / "ambient-marker"
    _write_ambient_entry_point(ambient_root)
    monkeypatch.setenv("AMBIENT_MARKER", str(ambient_marker))
    monkeypatch.syspath_prepend(str(ambient_root))

    with patch(
        "amplifier_app_cli.provider_loader.importlib.metadata.entry_points"
    ) as entry_points, pytest.raises(ImportError, match="does not contain"):
        get_provider_info("provider-example", source_path=source_root)

    assert not ambient_marker.exists()
    entry_points.assert_not_called()
