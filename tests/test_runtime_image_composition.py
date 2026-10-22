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
@pytest.mark.parametrize("section", ["overrides", "modules", "config"])
async def test_ordinary_tool_settings_preserve_backend_and_paid_denial(
    composition, tmp_path, section
):
    config = {"backend": "settings-backend", "allow_paid": False}
    body = (
        {"overrides": {"tool-image": {"config": config}}}
        if section == "overrides"
        else {section: {"tools": [{"module": "tool-image", "config": config}]}}
    )
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", _settings(tmp_path, body)
    )
    assert plan["tools"][0]["config"] == {"model_role": "image-gen", **config}
    assert prepared.mount_plan["tools"] == plan["tools"]
    # Foundation children inherit the same effective tool settings.
    assert prepared.bundle.tools == plan["tools"]


@pytest.mark.asyncio
async def test_module_introduced_image_still_inherits_shared_pins_and_denial(
    composition, tmp_path
):
    composition.declarations[IMAGE_BEHAVIOR] = Bundle(name="empty-image-fixture")
    shared = {
        "backend": "saved-backend",
        "provider_preferences": [{"provider": "saved-account", "model": "saved-model"}],
        "allow_paid": False,
    }
    settings = _settings(
        tmp_path,
        {
            "config": {"tools": [{"module": "tool-image", "config": shared}]},
            "modules": {"tools": [_image_tool(allow_paid=True)]},
        },
    )
    plan, prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    assert plan["tools"] == [_image_tool(**shared)]
    assert prepared.bundle.tools == plan["tools"]
    assert composition.root.tools == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", "project", "local"])
@pytest.mark.parametrize("session_override", [False, True])
async def test_shared_legacy_off_survives_every_later_paid_grant(
    composition, tmp_path, scope, session_override
):
    settings = _settings(tmp_path)
    off_path = getattr(settings.paths, f"{scope}_settings")
    off_path.write_text(
        yaml.safe_dump({"image_generation": {"mode": "off"}}), encoding="utf-8"
    )
    session_path = (
        tmp_path / "amplifier-home" / "projects" / "fixture-project"
        / "sessions" / "fixture-session" / "settings.yaml"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        yaml.safe_dump(
            {
                "image_generation": {"mode": "automatic"},
                "config": {"tools": [_image_tool(allow_paid=True)]},
                "overrides": {"tool-image": {"config": {"allow_paid": True}}},
                "modules": {"tools": [_image_tool(allow_paid=True)]},
            }
        ),
        encoding="utf-8",
    )
    before = (off_path.read_bytes(), session_path.read_bytes())
    kwargs = (
        {"session_id": "fixture-session", "project_slug": "fixture-project"}
        if session_override
        else {}
    )
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", settings, **kwargs
    )
    assert plan["tools"][0]["config"]["allow_paid"] is False
    assert prepared.mount_plan["tools"] == plan["tools"]
    assert prepared.bundle.tools == plan["tools"]
    assert (off_path.read_bytes(), session_path.read_bytes()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", [False, None, "true", 1])
async def test_shared_tool_denial_cannot_be_overridden_by_modules(
    composition, tmp_path, grant
):
    settings = _settings(
        tmp_path,
        {
            "config": {"tools": [_image_tool(allow_paid=grant)]},
            "modules": {"tools": [_image_tool(allow_paid=True)]},
        },
    )
    before = settings.paths.global_settings.read_bytes()
    plan, prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    assert plan["tools"][0]["config"]["allow_paid"] is False
    assert prepared.bundle.tools == plan["tools"]
    assert settings.paths.global_settings.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["overrides", "modules", "config"])
async def test_root_paid_denial_survives_settings_allow(
    composition, tmp_path, section
):
    composition.root.tools = [_image_tool(allow_paid=False, backend="root-pin")]
    original = deepcopy(composition.root.tools)
    body = (
        {"overrides": {"tool-image": {"config": {"allow_paid": True}}}}
        if section == "overrides"
        else {section: {"tools": [_image_tool(allow_paid=True)]}}
    )
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", _settings(tmp_path, body)
    )
    assert plan["tools"][0]["config"]["allow_paid"] is False
    assert plan["tools"][0]["config"]["backend"] == "root-pin"
    assert prepared.bundle.tools == plan["tools"]
    assert composition.root.tools == original


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_backend", [False, True])
async def test_partial_shared_pins_inherit_default_grant_without_mutating_settings(
    composition, tmp_path, monkeypatch, explicit_backend
):
    pins = {
        "model_role": "image-quality",
        "provider_preferences": [{"provider": "account-blue", "model": "pinned-model"}],
    }
    if explicit_backend:
        pins["backend"] = "account-blue-image"
    body = {"config": {"tools": [{"module": "tool-image", "config": pins}]}}
    settings = _settings(tmp_path, body)
    # Also detect in-memory aliasing, not only rewritten settings files.
    monkeypatch.setattr(settings, "get_merged_settings", lambda: body)
    before = deepcopy(body)
    before_bytes = settings.paths.global_settings.read_bytes()
    image_before = deepcopy(composition.declarations[IMAGE_BEHAVIOR].tools)
    plan, prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    assert plan["tools"][0]["config"] == {"allow_paid": True, **pins}
    assert prepared.bundle.tools == plan["tools"]
    plan["tools"][0]["config"]["provider_preferences"][0]["model"] = "changed-result"
    assert body == before
    assert settings.paths.global_settings.read_bytes() == before_bytes
    assert composition.declarations[IMAGE_BEHAVIOR].tools == image_before


@pytest.mark.asyncio
@pytest.mark.parametrize("session_override", [False, True])
async def test_cli_pins_override_shared_pins_but_not_shared_denials(
    composition, tmp_path, session_override
):
    def pinned(label, allow_paid):
        return _image_tool(
            backend=f"{label}-backend",
            model_role=f"{label}-role",
            provider_preferences=[
                {"provider": f"{label}-account", "model": f"{label}-model"}
            ],
            allow_paid=allow_paid,
        )

    settings = _settings(
        tmp_path,
        {
            "config": {"tools": [pinned("shared", False)]},
            "overrides": {"tool-image": {"config": pinned("general", True)["config"]}},
            "modules": {"tools": [pinned("module", True)]},
        },
    )
    kwargs = {}
    if session_override:
        session_path = (
            tmp_path / "amplifier-home" / "projects" / "fixture-project"
            / "sessions" / "fixture-session" / "settings.yaml"
        )
        session_path.parent.mkdir(parents=True)
        session_path.write_text(
            yaml.safe_dump({"modules": {"tools": [pinned("session", True)]}}),
            encoding="utf-8",
        )
        kwargs = {"session_id": "fixture-session", "project_slug": "fixture-project"}
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", settings, **kwargs
    )
    expected = pinned("session" if session_override else "module", False)["config"]
    assert plan["tools"][0]["config"] == expected
    assert prepared.mount_plan["tools"] == plan["tools"]
    assert prepared.bundle.tools == plan["tools"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["read", "write"])
async def test_fresh_image_paths_intersect_and_denials_union_before_normal_merges(
    composition, tmp_path, kind
):
    allowed, denied = f"allowed_{kind}_paths", f"denied_{kind}_paths"

    def limits(path, label, *, module="tool-image"):
        return {
            "module": module,
            "config": {allowed: [path], denied: [f"assets/{label}"]},
        }

    composition.root.tools = [
        _image_tool(**{allowed: ["assets"], denied: ["assets/root"]}),
        limits("assets", "root-fs", module="tool-filesystem"),
    ]
    original = deepcopy(composition.root.tools)
    settings = _settings(
        tmp_path,
        {
            "config": {
                "tools": [
                    limits("assets/shared", "shared"),
                    limits("assets/shared", "shared-fs", module="tool-filesystem"),
                ]
            },
            "overrides": {
                "tool-image": {
                    "config": limits("assets/shared/general", "general")["config"]
                }
            },
            "modules": {"tools": [limits("assets/shared/general/module", "module")]},
        },
    )
    session_path = (
        tmp_path / "amplifier-home" / "projects" / "fixture-project"
        / "sessions" / "fixture-session" / "settings.yaml"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        yaml.safe_dump(
            {
                "modules": {
                    "tools": [limits("assets/shared/general/module/session", "session")]
                }
            }
        ),
        encoding="utf-8",
    )
    before = (settings.paths.global_settings.read_bytes(), session_path.read_bytes())
    plan, prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", settings,
        session_id="fixture-session", project_slug="fixture-project",
    )
    policy = next(
        row["config"] for row in plan["tools"] if row["module"] == "tool-image"
    )
    assert policy[allowed] == [str(tmp_path / "assets/shared/general/module/session")]
    assert set(policy[denied]) == {
        str(tmp_path / f"assets/{label}")
        for label in (
            "root", "root-fs", "shared", "shared-fs", "general", "module", "session"
        )
    }
    assert policy["allow_paid"] is True
    assert prepared.bundle.tools == plan["tools"]
    assert composition.root.tools == original
    assert (
        settings.paths.global_settings.read_bytes(), session_path.read_bytes()
    ) == before
    # This image-only policy must not rewrite the ordinary filesystem settings.
    filesystem = next(
        row["config"] for row in plan["tools"] if row["module"] == "tool-filesystem"
    )
    assert filesystem[denied] == ["assets/root-fs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["overrides", "modules"])
async def test_filesystem_null_write_denies_compose_with_images_and_patch(
    composition, tmp_path, section
):
    composition.root.tools = [
        _image_tool(
            allow_paid=False,
            backend="saved-image-account",
            allowed_write_paths=["outputs"],
            denied_write_paths=["outputs/private"],
        ),
        {"module": "tool-filesystem", "config": {}},
        {"module": "tool-apply-patch", "config": {
            "allowed_write_paths": [], "denied_write_paths": ["patch-private"],
        }},
    ]
    filesystem = {
        "module": "tool-filesystem",
        "config": {"allowed_write_paths": ["outputs"], "denied_write_paths": None},
    }
    body = (
        {"overrides": {"tool-filesystem": {"config": filesystem["config"]}}}
        if section == "overrides"
        else {"modules": {"tools": [filesystem]}}
    )
    settings = _settings(tmp_path, body)
    before = settings.paths.global_settings.read_bytes()
    root_before = deepcopy(composition.root.tools)
    plan, prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    tools = {row["module"]: row["config"] for row in plan["tools"]}
    assert tools["tool-image"]["allow_paid"] is False
    assert tools["tool-image"]["backend"] == "saved-image-account"
    assert tools["tool-image"]["allowed_write_paths"] == [str(tmp_path / "outputs")]
    assert tools["tool-image"]["denied_write_paths"] == [str(tmp_path / "outputs/private")]
    assert tools["tool-apply-patch"]["allowed_write_paths"] == []
    assert tools["tool-apply-patch"]["additional_allowed_write_paths"] == ["outputs"]
    assert tools["tool-apply-patch"]["denied_write_paths"] == ["patch-private"]
    assert tools["tool-filesystem"]["denied_write_paths"] is None
    assert prepared.bundle.tools == plan["tools"]
    assert composition.root.tools == root_before
    assert settings.paths.global_settings.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_paths", [[], ["artist"]])
async def test_empty_or_disjoint_image_allow_lists_never_become_a_union(
    composition, tmp_path, shared_paths
):
    composition.root.tools = [_image_tool(allowed_read_paths=["art"])]
    settings = _settings(
        tmp_path,
        {
            "config": {"tools": [_image_tool(allowed_read_paths=shared_paths)]},
            "modules": {"tools": [_image_tool(allowed_read_paths=["."])]},
        },
    )
    plan, _prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    assert plan["tools"][0]["config"]["allowed_read_paths"] == []


@pytest.mark.asyncio
async def test_session_allow_cannot_erase_module_denial_or_routing_block(
    composition, tmp_path
):
    settings = _settings(
        tmp_path,
        {"modules": {"tools": [_image_tool(allow_paid=False, routing_blocked=True)]}},
    )
    session_path = (
        tmp_path / "amplifier-home" / "projects" / "fixture-project"
        / "sessions" / "fixture-session" / "settings.yaml"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        yaml.safe_dump(
            {
                "modules": {
                    "tools": [_image_tool(allow_paid=True, routing_blocked=False)]
                }
            }
        ),
        encoding="utf-8",
    )
    plan, _prepared = await runtime_config.resolve_bundle_config(
        "chosen-root", settings,
        session_id="fixture-session", project_slug="fixture-project",
    )
    assert plan["tools"][0]["config"]["allow_paid"] is False
    assert plan["tools"][0]["config"]["routing_blocked"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["openai", "gemini"])
@pytest.mark.parametrize("backend_id", [None, "saved-image-account"])
@pytest.mark.parametrize("mode", [None, "automatic", "selected"])
async def test_one_saved_image_selection_keeps_its_backend_account_and_model(
    composition, tmp_path, module, backend_id, mode
):
    composition.root.providers = []
    image = {
        "enabled": True,
        "model": "saved-image-model",
        "transport": "saved-transport",
    }
    if backend_id is not None:
        image["id"] = backend_id
    provider = {
        "module": f"provider-{module}",
        "id": "account-blue",
        "config": {
            "api_key": "fixture-only-not-a-real-key",
            "base_url": "https://account-blue.invalid",
            "default_model": "chat-model",
            "image_generation": image,
        },
    }
    body = {"config": {"providers": [provider]}}
    if mode is not None:
        body["image_generation"] = {"mode": mode}
    original = deepcopy(body)
    settings = _settings(tmp_path, body)
    before = settings.paths.global_settings.read_bytes()
    plan, prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    assert plan["tools"][0]["config"]["backend"] == (backend_id or module)
    assert not plan["tools"][0]["config"].get("routing_blocked")
    assert plan["providers"] == [
        {
            **provider,
            "instance_id": "account-blue",
            "config": {**provider["config"], "raw": True},
        }
    ]
    assert prepared.bundle.tools == plan["tools"]
    assert body == original
    assert settings.paths.global_settings.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "multiple", "deselected", "invalid-grant", "unavailable",
        "unmounted", "missing", "invalid-id", "unknown-mode",
    ],
)
async def test_ambiguous_legacy_intent_blocks_visibly_without_a_new_account(
    composition, tmp_path, caplog, case
):
    provider = {
        "module": "provider-openai",
        "id": "saved-account",
        "config": {
            "api_key": "fixture-only-not-a-real-key",
            "image_generation": {
                "enabled": True, "id": "saved-backend", "model": "pinned"
            },
        },
    }
    providers = [provider]
    mode = "selected"
    if case == "multiple":
        mode = "automatic"
        providers.append(
            {
                "module": "provider-gemini",
                "config": {"image_generation": {"enabled": True, "id": "other"}},
            }
        )
    elif case in {"deselected", "invalid-grant"}:
        provider["config"]["image_generation"]["enabled"] = (
            False if case == "deselected" else "true"
        )
        mode = "automatic"
    elif case == "unavailable":
        provider["enabled"] = False
    elif case == "missing":
        providers = []
    elif case == "invalid-id":
        provider["config"]["image_generation"]["id"] = ""
    elif case == "unknown-mode":
        mode = "unrecognized"
    if case != "unmounted":
        composition.root.providers = []
    settings = _settings(
        tmp_path,
        {"image_generation": {"mode": mode}, "config": {"providers": providers}},
    )
    before = settings.paths.global_settings.read_bytes()
    plan, prepared = await runtime_config.resolve_bundle_config("chosen-root", settings)
    config = plan["tools"][0]["config"]
    assert config["routing_blocked"] is True
    assert "backend" not in config
    assert "Image routing blocked:" in caplog.text
    assert "fixture-only-not-a-real-key" not in caplog.text
    assert prepared.bundle.tools == plan["tools"]
    assert settings.paths.global_settings.read_bytes() == before
    if case == "unmounted":
        assert plan["providers"] == composition.root.providers
    else:
        assert len(plan.get("providers", [])) == len(providers)


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