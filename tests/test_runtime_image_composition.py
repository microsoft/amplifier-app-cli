"""Fresh image composition, ordinary overrides, opt-out and snapshot boundaries.

Foundation composition and CLI configuration run for real. Bundle fetching,
module installation and session construction are replaced with inert fixtures;
these tests neither call a provider nor establish live image entitlement.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from amplifier_foundation import Bundle

from amplifier_app_cli.lib.bundle_loader import prepare as bundle_prepare
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.runtime import config as runtime_config


IMAGE_ROOT = "git+https://github.com/microsoft/amplifier-bundle-imagegen@main"
IMAGE_BEHAVIOR = f"{IMAGE_ROOT}#subdirectory=behaviors/imagegen.yaml"
APP_BEHAVIOR = "file:///fixture/user-app.yaml"
ROOT_URI = "file:///fixture/chosen-root"
ROOT_INSTRUCTION = "@chosen-root:context/system.md"


def _image_tool(**config):
    return {
        "module": "tool-image",
        "source": f"{IMAGE_ROOT}#subdirectory=modules/tool-image",
        "config": {"model_role": "image-gen", "allow_paid": True, **config},
    }


def _settings(tmp_path: Path, body: dict | None = None) -> AppSettings:
    paths = SettingsPaths(
        global_settings=tmp_path / "settings.yaml",
        project_settings=tmp_path / "project.yaml",
        local_settings=tmp_path / "local.yaml",
    )
    paths.global_settings.write_text(yaml.safe_dump(body or {}), encoding="utf-8")
    return AppSettings(paths=paths)


@pytest.fixture
def composition(tmp_path, monkeypatch, isolated_home):
    """Use the real prepare loop without discovery writes or module installs."""
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier-home"))
    monkeypatch.chdir(tmp_path)
    root = Bundle(
        name="chosen-root",
        version="1.0",
        instruction=ROOT_INSTRUCTION,
        includes=["chosen-root:behaviors/own.yaml"],
        namespace_root=".",
        base_path=tmp_path,
        session={
            "orchestrator": {"module": "loop-fixture"},
            "context": {"module": "context-fixture"},
        },
        providers=[
            {
                "module": "provider-fixture",
                "config": {"model": "chat-model", "priority": 0},
            }
        ],
    )
    image = Bundle(
        name="imagegen",
        tools=[_image_tool()],
        context={"imagegen:guidance": tmp_path / "image-guidance.md"},
    )
    declarations = {ROOT_URI: root, IMAGE_BEHAVIOR: image}
    loaded = []

    async def load(uri, **_kwargs):
        loaded.append(uri)
        # Other automatic CLI behaviors are inert here: their implementations
        # have their own tests; only ordering and image composition matter.
        return declarations.get(uri, Bundle(name="", version=""))

    async def prepare(bundle, **_kwargs):
        return SimpleNamespace(
            bundle=bundle,
            mount_plan=bundle.to_mount_plan(),
            resolver=MagicMock(),
        )

    discovery = SimpleNamespace(
        find=lambda _name: ROOT_URI,
        registry=MagicMock(),
    )
    monkeypatch.setattr(
        "amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery",
        lambda **_kwargs: discovery,
    )
    monkeypatch.setattr("amplifier_app_cli.paths.get_bundle_search_paths", lambda: [])
    monkeypatch.setattr(bundle_prepare, "load_bundle", load)
    monkeypatch.setattr(Bundle, "prepare", prepare)
    monkeypatch.setattr(runtime_config, "_validate_provider_credentials", AsyncMock())
    return SimpleNamespace(root=root, declarations=declarations, loaded=loaded)


def test_image_builder_is_external_behavior_only():
    assert runtime_config._build_imagegen_behaviors() == [IMAGE_BEHAVIOR]
    assert IMAGE_ROOT not in runtime_config._build_imagegen_behaviors()
    assert not any("bundle.md" in uri for uri in runtime_config._build_imagegen_behaviors())


@pytest.mark.asyncio
async def test_fresh_resolution_composes_default_before_explicit_apps(
    composition, tmp_path
):
    composition.declarations[APP_BEHAVIOR] = Bundle(
        name="explicit-app",
        tools=[_image_tool(backend="explicit-backend", allow_paid=False)],
        instruction="An app body must not replace the root.",
    )
    settings = _settings(tmp_path, {"bundle": {"app": [APP_BEHAVIOR]}})
    plan, prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)

    assert composition.loaded[0] == ROOT_URI
    assert composition.loaded.count(IMAGE_BEHAVIOR) == 1
    assert composition.loaded.index(IMAGE_BEHAVIOR) < composition.loaded.index(APP_BEHAVIOR)
    assert IMAGE_ROOT not in composition.loaded
    assert plan["session"] == composition.root.session
    assert plan["providers"] == composition.root.providers
    assert prepared.bundle.instruction == (
        f"{ROOT_INSTRUCTION}\n\n{bundle_prepare._AGENTS_INSTRUCTION_TAIL}"
    )
    assert [t["module"] for t in plan["tools"]] == ["tool-image"]
    assert plan["tools"][0]["config"]["backend"] == "explicit-backend"
    assert plan["tools"][0]["config"]["allow_paid"] is False
    assert composition.root.tools == []
    assert composition.root.instruction == ROOT_INSTRUCTION


@pytest.mark.asyncio
async def test_default_preserves_root_identity_and_existing_image_configuration(
    composition, tmp_path
):
    composition.root.tools = [_image_tool(allow_paid=False, backend="root-backend")]
    composition.root.context = {"imagegen:guidance": tmp_path / "image-guidance.md"}
    original = deepcopy(composition.root)
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", _settings(tmp_path)
    )

    assert [t["module"] for t in plan["tools"]] == ["tool-image"]
    assert plan["tools"][0]["config"]["allow_paid"] is False
    assert plan["tools"][0]["config"]["backend"] == "root-backend"
    assert prepared.bundle.name == original.name
    assert prepared.bundle.includes == original.includes
    assert prepared.bundle.namespace_root == original.namespace_root
    assert prepared.bundle.base_path == original.base_path
    assert list(prepared.bundle.context) == ["imagegen:guidance"]
    assert composition.root.tools == original.tools
    assert composition.root.instruction == original.instruction


@pytest.mark.asyncio
async def test_explicit_same_behavior_is_not_also_added_as_a_default(
    composition, tmp_path
):
    settings = _settings(tmp_path, {"bundle": {"app": [IMAGE_BEHAVIOR]}})
    plan, _prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    assert composition.loaded.count(IMAGE_BEHAVIOR) == 1
    # Keep the explicit app position rather than silently moving it earlier.
    assert composition.loaded[-1] == IMAGE_BEHAVIOR
    assert [t["module"] for t in plan["tools"]] == ["tool-image"]


@pytest.mark.asyncio
async def test_unavailable_default_is_reported_and_does_not_replace_chat(
    composition, tmp_path, monkeypatch, caplog
):
    original_load = bundle_prepare.load_bundle

    async def load(uri, **kwargs):
        if uri == IMAGE_BEHAVIOR:
            raise RuntimeError("fixture image behavior unavailable")
        return await original_load(uri, **kwargs)

    monkeypatch.setattr(bundle_prepare, "load_bundle", load)
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", _settings(tmp_path)
    )
    assert "fixture image behavior unavailable" in caplog.text
    assert plan.get("tools", []) == []
    assert plan["session"] == composition.root.session
    assert plan["providers"] == composition.root.providers
    assert prepared.bundle.instruction.startswith(ROOT_INSTRUCTION)


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["overrides", "modules"])
async def test_ordinary_tool_settings_preserve_backend_and_paid_denial(
    composition, tmp_path, section
):
    config = {"backend": "settings-backend", "allow_paid": False}
    body = (
        {"overrides": {"tool-image": {"config": config}}}
        if section == "overrides"
        else {"modules": {"tools": [{"module": "tool-image", "config": config}]}}
    )
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", _settings(tmp_path, body)
    )
    assert plan["tools"][0]["config"] == {"model_role": "image-gen", **config}
    assert prepared.mount_plan["tools"] == plan["tools"]
    # Foundation children inherit the same effective tool settings.
    assert prepared.bundle.tools == plan["tools"]


@pytest.mark.asyncio
async def test_specific_tool_overrides_win_over_general_overrides(
    composition, tmp_path
):
    settings = _settings(
        tmp_path,
        {
            "overrides": {
                "tool-image": {"config": {"backend": "general", "allow_paid": True}}
            },
            "modules": {
                "tools": [
                    {
                        "module": "tool-image",
                        "config": {"backend": "specific", "allow_paid": False},
                    }
                ]
            },
        },
    )
    plan, _prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    assert plan["tools"][0]["config"]["backend"] == "specific"
    assert plan["tools"][0]["config"]["allow_paid"] is False


@pytest.mark.asyncio
async def test_session_scoped_tool_denial_wins(composition, tmp_path):
    settings = _settings(
        tmp_path,
        {"modules": {"tools": [_image_tool(backend="global", allow_paid=True)]}},
    )
    session_path = (
        tmp_path
        / "amplifier-home"
        / "projects"
        / "fixture-project"
        / "sessions"
        / "fixture-session"
        / "settings.yaml"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        yaml.safe_dump(
            {"modules": {"tools": [_image_tool(backend="session", allow_paid=False)]}}
        ),
        encoding="utf-8",
    )
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root",
        settings,
        session_id="fixture-session",
        project_slug="fixture-project",
    )
    assert plan["tools"][0]["config"]["backend"] == "session"
    assert plan["tools"][0]["config"]["allow_paid"] is False
    assert prepared.bundle.tools == plan["tools"]


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_name", ["image_generate", "tool-image"])
async def test_existing_configurator_opt_out_unmounts_image_before_execution(
    composition, tmp_path, monkeypatch, disabled_name
):
    from amplifier_app_cli import session_runner

    settings = _settings(
        tmp_path,
        {"configurator": {"disabled": {"tools": [disabled_name]}}},
    )
    plan, prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    image_tool = object()
    ordinary_tool = object()
    mounted = {"image_generate": image_tool, "ordinary_tool": ordinary_tool}
    coordinator = MagicMock()
    coordinator.config = plan
    coordinator.get_capability.return_value = None
    coordinator.get.side_effect = lambda key: mounted if key == "tools" else None
    coordinator.hooks.list_handlers.return_value = {}
    coordinator.hooks.emit = AsyncMock()

    async def unmount(category, *, name):
        assert category == "tools"
        mounted.pop(name)

    coordinator.unmount = AsyncMock(side_effect=unmount)
    session = SimpleNamespace(config=plan, coordinator=coordinator)
    monkeypatch.setattr(
        session_runner, "_create_bundle_session", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(AppSettings, "with_session", lambda _self, *_args: settings)
    monkeypatch.setattr(
        "amplifier_app_cli.commands.init.check_first_run", lambda: False
    )
    monkeypatch.setattr("amplifier_app_cli.ui.CLIApprovalSystem", MagicMock())
    monkeypatch.setattr("amplifier_app_cli.ui.CLIDisplaySystem", MagicMock())
    initialized = await session_runner.create_initialized_session(
        session_runner.SessionConfig(
            config=plan,
            search_paths=[],
            verbose=False,
            session_id="fixture-session",
            prepared_bundle=prepared,
            shared_root=False,
        ),
        MagicMock(),
    )

    assert initialized.configurator is not None
    assert mounted == {"ordinary_tool": ordinary_tool}
    coordinator.unmount.assert_awaited_once_with("tools", name="image_generate")
    # Reapplication does not resurrect the default or disturb unrelated tools.
    await initialized.configurator.apply_saved_settings(
        settings.get_merged_settings()["configurator"]
    )
    assert mounted == {"ordinary_tool": ordinary_tool}


@pytest.mark.asyncio
@pytest.mark.parametrize("has_image", [False, True])
async def test_saved_subsession_plan_is_not_freshly_composed(
    composition, tmp_path, monkeypatch, has_image
):
    from amplifier_app_cli import session_spawner

    snapshot = {
        "session": {"orchestrator": "saved-loop", "context": "saved-context"},
        "tools": [_image_tool(allow_paid=False, backend="saved")] if has_image else [],
        "providers": [
            {"module": "provider-fixture", "config": {"model": "saved-chat"}}
        ],
    }
    before = deepcopy(snapshot)
    metadata = {
        "session_id": "saved-child",
        "parent_id": "saved-parent",
        "agent_name": "fixture-agent",
        "config": snapshot,
        "agent_overlay": {"instruction": "Saved instruction"},
    }
    store = MagicMock()
    store.exists.return_value = True
    store.load.return_value = ([], metadata)
    monkeypatch.setattr(
        "amplifier_app_cli.session_store.SessionStore", lambda: store
    )
    settings = _settings(
        tmp_path,
        {
            "bundle": {"app": [IMAGE_BEHAVIOR]},
            "overrides": {
                "tool-image": {"config": {"allow_paid": True, "backend": "new"}}
            },
        },
    )
    monkeypatch.setattr(
        "amplifier_app_cli.lib.settings.AppSettings", lambda: settings
    )
    build = MagicMock(side_effect=AssertionError("snapshot must not compose defaults"))
    monkeypatch.setattr(runtime_config, "_build_imagegen_behaviors", build)
    monkeypatch.setattr("amplifier_app_cli.ui.CLIApprovalSystem", MagicMock())
    monkeypatch.setattr("amplifier_app_cli.ui.CLIDisplaySystem", MagicMock())

    class MountBoundaryReached(Exception):
        pass

    # Stop at the actual core construction boundary: inspect the plan handed
    # to Core without mounting modules or executing a resumed conversation.
    construct = MagicMock(side_effect=MountBoundaryReached)
    monkeypatch.setattr(session_spawner, "AmplifierSession", construct)
    with pytest.raises(MountBoundaryReached):
        await session_spawner.resume_sub_session("saved-child", "continue")

    build.assert_not_called()
    assert composition.loaded == []
    assert construct.call_args.kwargs["config"] == before
    assert snapshot == before
    store.save.assert_not_called()