"""Fresh-child image restrictions, through the helper and real spawn assembly.

Execution boundaries are replaced, not merge/filter/policy code. No provider,
image backend, subprocess, persisted session or neighboring checkout is used.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli import session_spawner
from amplifier_app_cli.agent_config import merge_configs
from amplifier_app_cli.image_policy import restrict_child_images


def image(**config):
    return {"module": "tool-image", "config": config}


def filesystem(**config):
    return {"module": "tool-filesystem", "config": config}


def image_configs(plan):
    return [row["config"] for row in plan.get("tools", []) if row["module"] == "tool-image"]


def coordinator(plan, mounts, root=None):
    return SimpleNamespace(
        config=plan,
        get=lambda name: mounts if name == "tools" else None,
        get_capability=lambda name: root if name == "session.working_dir" else None,
        display_system=object(),
        approval_system=object(),
    )


@pytest.mark.parametrize("kind", ["read", "write"])
def test_intersection_and_union_retain_every_policy_source(tmp_path, kind):
    allowed, denied = f"allowed_{kind}_paths", f"denied_{kind}_paths"
    parent = {
        "tools": [
            image(allow_paid=True, **{allowed: ["assets"], denied: ["assets/parent"]}),
            filesystem(**{allowed: ["assets/shared"], denied: ["assets/fs"]}),
        ],
        "spawn": {"tools": [
            image(allow_paid=True, **{allowed: ["assets/shared/job"], denied: ["assets/spawn"]}),
        ]},
    }
    overlay = {"tools": [
        image(**{allowed: ["assets/shared/job/final"], denied: ["assets/overlay"]}),
        filesystem(**{denied: ["assets/overlay-fs"]}),
    ]}
    child = merge_configs(parent, overlay)
    # A session-specific child restriction is already present when applied.
    child["tools"][0]["config"][allowed] = ["assets/shared/job/final/session"]
    child["tools"][0]["config"][denied] = ["assets/session"]
    before = deepcopy((parent, overlay, child))

    result = restrict_child_images(parent, child, overlay=overlay, working_dir=tmp_path)
    policy = image_configs(result)[0]
    assert policy[allowed] == [str(tmp_path / "assets/shared/job/final/session")]
    assert set(policy[denied]) == {
        str(tmp_path / f"assets/{name}")
        for name in ("parent", "fs", "spawn", "overlay", "overlay-fs", "session")
    }
    assert policy["allow_paid"] is True
    assert (parent, overlay, child) == before
    # Mutating one returned plan cannot change any input or a later sibling.
    policy[allowed].append("/unrelated")
    assert (parent, overlay, child) == before
    sibling = restrict_child_images(parent, child, overlay=overlay, working_dir=tmp_path)
    assert image_configs(sibling)[0][allowed] == [str(tmp_path / "assets/shared/job/final/session")]


@pytest.mark.parametrize("kind", ["read", "write"])
@pytest.mark.parametrize(
    ("parent_paths", "child_paths", "expected"),
    [
        (["art"], ["art/sub", "elsewhere"], ["art/sub"]),
        (["art/sub"], ["art"], ["art/sub"]),
        (["art"], ["artist"], []),  # not string-prefix intersection
        (["art"], [], []),
        ([], ["art"], []),
        (["art", "art"], ["art"], ["art"]),
    ],
)
def test_allow_lists_intersect_by_containment_not_union(
    tmp_path, kind, parent_paths, child_paths, expected
):
    key = f"allowed_{kind}_paths"
    parent = {"tools": [image(allow_paid=True, **{key: parent_paths})]}
    child = {"tools": [image(allow_paid=True, **{key: child_paths})]}
    result = restrict_child_images(parent, child, working_dir=tmp_path)
    assert image_configs(result)[0][key] == [str(tmp_path / value) for value in expected]


@pytest.mark.parametrize("grant", [False, None, "true", 1])
def test_nonliteral_parent_grant_cannot_be_enabled_by_overlay(tmp_path, grant):
    parent = {"tools": [image(allow_paid=grant)]}
    overlay = {"tools": [image(allow_paid=True)]}
    result = restrict_child_images(
        parent, merge_configs(parent, overlay), overlay=overlay, working_dir=tmp_path
    )
    assert image_configs(result)[0]["allow_paid"] is False


@pytest.mark.parametrize("kind", ["read", "write"])
@pytest.mark.parametrize("stage", ["parent", "spawn", "overlay", "child"])
@pytest.mark.parametrize("module", ["tool-image", "tool-filesystem"])
def test_each_individual_scope_source_can_be_the_narrowest(
    tmp_path, kind, stage, module
):
    key = f"allowed_{kind}_paths"
    parent = {"tools": [image(allow_paid=True, **{key: ["."]})]}
    child = deepcopy(parent)
    overlay = {}
    target = {"module": module, "config": {key: ["restricted"], "allow_paid": True}}
    if stage == "parent":
        parent["tools"] = [target]
    elif stage == "spawn":
        parent["spawn"] = {"tools": [target]}
    elif stage == "overlay":
        overlay["tools"] = [target]
    elif module == "tool-image":
        child["tools"] = [target]
    else:
        child["tools"].append(target)

    result = restrict_child_images(
        parent, child, overlay=overlay, working_dir=tmp_path
    )
    assert image_configs(result)[0][key] == [str(tmp_path / "restricted")]


def test_paths_bind_to_session_directory_not_shell_or_overlay(tmp_path, monkeypatch):
    shell = tmp_path / "shell"
    shell.mkdir()
    monkeypatch.chdir(shell)
    root = tmp_path / "session"
    parent = {"tools": [
        image(allow_paid=True, allowed_read_paths=["inputs"]),
        filesystem(denied_read_paths=["inputs/private"]),
    ]}
    child = {"tools": [
        image(
            allow_paid=True,
            working_dir=str(shell),
            allowed_read_paths=[str(root / "inputs" / "public")],
            denied_read_paths=[str(root / "inputs" / "private")],
        )
    ]}
    result = restrict_child_images(parent, child, working_dir=root)
    policy = image_configs(result)[0]
    assert policy["allowed_read_paths"] == [str(root / "inputs" / "public")]
    assert policy["denied_read_paths"] == [str(root / "inputs" / "private")]
    # Non-policy configuration is not rewritten.
    assert policy["working_dir"] == str(shell)


@pytest.mark.parametrize("source", ["parent", "spawn", "child", "mounted"])
def test_absent_grant_on_complete_declaration_means_disabled(tmp_path, source):
    parent = {"tools": [image(allow_paid=True)]}
    child = {"tools": [image(allow_paid=True)]}
    mounts = {"image_generate": object()}
    if source == "parent":
        parent["tools"] = [image()]
    elif source == "spawn":
        parent["spawn"] = {"tools": [image()]}
    elif source == "child":
        child["tools"] = [image()]
    else:
        mounts["image_generate"] = SimpleNamespace(config={})
    result = restrict_child_images(
        parent, child, working_dir=tmp_path,
        parent_coordinator=coordinator(parent, mounts),
    )
    assert image_configs(result)[0]["allow_paid"] is False


def test_partial_overlay_omission_inherits_grant_but_explicit_false_narrows(tmp_path):
    parent = {"tools": [image(allow_paid=True), filesystem(allow_paid=False)]}
    for overlay_config, expected in (({}, True), ({"allow_paid": False}, False)):
        overlay = {"tools": [image(**overlay_config)]}
        result = restrict_child_images(
            parent, merge_configs(parent, overlay), overlay=overlay, working_dir=tmp_path
        )
        assert image_configs(result)[0]["allow_paid"] is expected
        # Omitted path policy stays omitted: the shared tool owns workspace defaults.
        assert "allowed_write_paths" not in image_configs(result)[0]


def test_live_session_and_mounted_image_restrictions_are_retained(tmp_path):
    parent = {"tools": [image(allow_paid=True, allowed_write_paths=["."])]}
    live = {"tools": [filesystem(allowed_write_paths=["outputs"], denied_write_paths=["outputs/live"])]}
    mounted_policy = {"allow_paid": False, "allowed_write_paths": ["outputs/job"],
                      "denied_write_paths": ["outputs/mounted"]}
    coord = coordinator(live, {"image_generate": SimpleNamespace(config=mounted_policy)})
    before = deepcopy((parent, live, mounted_policy))
    result = restrict_child_images(parent, parent, working_dir=tmp_path, parent_coordinator=coord)
    policy = image_configs(result)[0]
    assert policy["allow_paid"] is False
    assert policy["allowed_write_paths"] == [str(tmp_path / "outputs/job")]
    assert set(policy["denied_write_paths"]) == {
        str(tmp_path / "outputs/live"), str(tmp_path / "outputs/mounted")
    }
    assert (parent, live, mounted_policy) == before


def test_changed_instance_id_cannot_escape_policy_or_mutate_provider_bindings(tmp_path):
    parent = {
        "tools": [dict(image(allow_paid=False, allowed_read_paths=["inputs"]), id="parent-image")],
        "providers": [
            {"module": "provider-fixture", "id": "account-blue",
             "config": {"default_model": "chat", "base_url": "https://fixture.invalid",
                        "image_generation": {"model": "image", "transport": "native"}}}
        ],
        "session": {"orchestrator": {"config": {"max_iterations": 4}}},
    }
    overlay = {"tools": [dict(image(allow_paid=True, allowed_read_paths=["."]), id="child-image")]}
    child = merge_configs(parent, overlay)
    before = deepcopy((parent, overlay, child))
    result = restrict_child_images(parent, child, overlay=overlay, working_dir=tmp_path)
    assert len(image_configs(result)) == 2
    for policy in image_configs(result):
        assert policy["allow_paid"] is False
        assert policy["allowed_read_paths"] == [str(tmp_path / "inputs")]
    assert result["providers"] == child["providers"]
    assert result["session"] == child["session"]
    result["providers"][0]["config"]["image_generation"]["model"] = "child-only"
    result["tools"][0]["config"]["allowed_read_paths"].clear()
    assert image_configs(result)[1]["allowed_read_paths"] == [str(tmp_path / "inputs")]
    assert (parent, overlay, child) == before


@pytest.mark.parametrize("unknown", [None, object(), MagicMock()])
def test_unknown_mount_evidence_does_not_remove_images(tmp_path, unknown):
    plan = {"tools": [image(allow_paid=True), {"module": "tool-unrelated"}]}
    coord = coordinator(plan, unknown)
    assert restrict_child_images(plan, plan, parent_coordinator=coord, working_dir=tmp_path) == plan
    assert restrict_child_images(plan, plan, parent_coordinator=object(), working_dir=tmp_path) == plan


def test_missing_mount_category_is_unknown_not_removal(tmp_path):
    class OldCoordinator:
        def get(self, name):
            raise KeyError(name)

    plan = {"tools": [image(allow_paid=True)]}
    result = restrict_child_images(plan, plan, parent_coordinator=OldCoordinator(), working_dir=tmp_path)
    assert result == plan


@pytest.mark.parametrize("mounts", [{}, {"read_file": object()}])
def test_real_mount_dict_without_image_filters_only_images(tmp_path, mounts):
    parent = {"tools": [image(allow_paid=True), {"module": "tool-unrelated"}]}
    overlay = {"tools": [dict(image(allow_paid=True), id="different-image")]}
    child = merge_configs(parent, overlay)
    result = restrict_child_images(
        parent, child, overlay=overlay, working_dir=tmp_path,
        parent_coordinator=coordinator(parent, mounts),
    )
    assert result["tools"] == [{"module": "tool-unrelated"}]
    assert len(parent["tools"]) == 2
    assert len(child["tools"]) == 3


def test_filtered_tool_is_never_added_back(tmp_path):
    parent = {"tools": [image(allow_paid=True)]}
    child = {"tools": []}
    assert restrict_child_images(parent, child, working_dir=tmp_path) == child


@pytest.mark.parametrize("bad", [None, "outputs", [7], {"path": "outputs"}])
@pytest.mark.parametrize("key", ["allowed_read_paths", "denied_write_paths"])
def test_malformed_file_policy_fails_instead_of_broadening(tmp_path, bad, key):
    parent = {"tools": [filesystem(**{key: bad})]}
    child = {"tools": [image(allow_paid=True)]}
    with pytest.raises(ValueError, match="lists of strings"):
        restrict_child_images(parent, child, working_dir=tmp_path)


def test_malformed_module_config_is_not_silently_dropped(tmp_path):
    parent = {"tools": [{"module": "tool-image", "config": None}]}
    with pytest.raises(ValueError, match="must be dictionaries"):
        restrict_child_images(parent, {"tools": [image(allow_paid=True)]}, working_dir=tmp_path)


@pytest.fixture(params=["core", "subprocess"])
def spawn_boundary(request, monkeypatch, tmp_path):
    """Capture actual spawner output immediately before any child can execute."""
    captured = []

    class AtCoreBoundary(Exception):
        pass

    def construct(**kwargs):
        captured.append(kwargs)
        raise AtCoreBoundary

    async def subprocess(**kwargs):
        captured.append(kwargs)
        return '{"output": "boundary captured", "status": "success"}'

    monkeypatch.setattr(session_spawner, "AmplifierSession", construct)
    monkeypatch.setattr(
        "amplifier_foundation.subprocess_runner.run_session_in_subprocess", subprocess
    )
    monkeypatch.setattr(session_spawner, "_extract_bundle_context", lambda _: None)

    async def spawn(plan, overlay=None, *, mounts=None, inheritance=None, agent="worker", coord=None):
        overlay = overlay or {}
        # Default is concrete evidence of an enabled image tool; None is not
        # mistaken for the deliberate empty-dict removal case.
        if mounts is None:
            mounts = {"image_generate": object(), "read_file": object()}
        coord = coord or coordinator(plan, mounts, str(tmp_path))
        parent = SimpleNamespace(config=plan, coordinator=coord, session_id="image-parent")
        kwargs = dict(
            agent_name=agent, instruction="Inspect child policy", parent_session=parent,
            agent_configs={"worker": overlay}, sub_session_id=f"image-child-{len(captured)}",
            tool_inheritance=inheritance, use_subprocess=request.param == "subprocess",
        )
        if request.param == "core":
            with pytest.raises(AtCoreBoundary):
                await session_spawner.spawn_sub_session(**kwargs)
        else:
            await session_spawner.spawn_sub_session(**kwargs)
            assert captured[-1]["project_path"] == str(tmp_path)
        return captured[-1]["config"]

    return spawn


@pytest.mark.asyncio
async def test_spawner_narrows_before_both_execution_boundaries(spawn_boundary, tmp_path):
    parent = {
        "tools": [image(allow_paid=False, denied_write_paths=["outputs/private"]),
                  filesystem(allowed_write_paths=["outputs"])],
        "spawn": {"tools": [image(allow_paid=True, allowed_write_paths=["outputs/job"],
                                  denied_write_paths=["outputs/spawn"])]},
    }
    overlay = {"tools": [image(allow_paid=True, allowed_write_paths=["."],
                               denied_write_paths=["outputs/overlay"], working_dir="/wrong-root")]}
    before = deepcopy((parent, overlay))
    child = await spawn_boundary(parent, overlay)
    policy = image_configs(child)[0]
    assert policy["allow_paid"] is False
    assert policy["allowed_write_paths"] == [str(tmp_path / "outputs/job")]
    assert set(policy["denied_write_paths"]) == {
        str(tmp_path / f"outputs/{name}") for name in ("private", "spawn", "overlay")
    }
    assert all(row["module"] != "tool-filesystem" for row in child["tools"])
    assert (parent, overlay) == before


@pytest.mark.asyncio
async def test_spawner_siblings_and_self_keep_independent_permissions(spawn_boundary, tmp_path):
    parent = {"tools": [image(allow_paid=True, allowed_write_paths=["outputs"])]}
    narrow = {"tools": [image(allow_paid=False, allowed_write_paths=["outputs/one"])]}
    before = deepcopy((parent, narrow))
    first = await spawn_boundary(parent, narrow)
    second = await spawn_boundary(parent, agent="self")
    assert image_configs(first)[0]["allow_paid"] is False
    assert image_configs(second)[0]["allow_paid"] is True
    assert image_configs(second)[0]["allowed_write_paths"] == [str(tmp_path / "outputs")]
    image_configs(first)[0]["allowed_write_paths"].clear()
    assert image_configs(second)[0]["allowed_write_paths"] == [str(tmp_path / "outputs")]
    assert (parent, narrow) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["spawn", "caller"])
@pytest.mark.parametrize("excluded", ["tool-image", "image_generate", "original-image"])
async def test_explicit_exclusion_beats_overlay_redeclaration(spawn_boundary, source, excluded):
    parent = {"tools": [dict(image(allow_paid=True), id="original-image"), filesystem()]}
    overlay = {"tools": [dict(image(allow_paid=True), id="new-image")]}
    inheritance = None
    if source == "spawn":
        parent["spawn"] = {"exclude_tools": [excluded]}
    else:
        inheritance = {"exclude_tools": [excluded]}
    child = await spawn_boundary(parent, overlay, inheritance=inheritance)
    assert child["tools"] == [filesystem()]


@pytest.mark.asyncio
async def test_inheritance_allowlist_does_not_resurrect_image(spawn_boundary):
    parent = {"tools": [image(allow_paid=True), filesystem()]}
    child = await spawn_boundary(parent, inheritance={"inherit_tools": ["tool-filesystem"]})
    assert child["tools"] == [filesystem()]


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_name", ["image_generate", "tool-image"])
async def test_configurator_opt_out_survives_real_spawn(spawn_boundary, tmp_path, disabled_name):
    from amplifier_foundation import Bundle
    from amplifier_foundation.configurator import SessionConfigurator

    plan = {"tools": [image(allow_paid=True), {"module": "tool-unrelated"}]}
    saved = deepcopy(plan)
    mounts = {"image_generate": object(), "ordinary_tool": object()}
    coord = coordinator(plan, mounts, str(tmp_path))
    coord.hooks = MagicMock()
    coord.hooks.list_handlers.return_value = {}
    coord.hooks.emit = AsyncMock()

    async def unmount(category, *, name):
        assert category == "tools"
        mounts.pop(name)

    coord.unmount = AsyncMock(side_effect=unmount)
    root = SimpleNamespace(config=plan, coordinator=coord)
    prepared = SimpleNamespace(bundle=Bundle(name="image-parent", tools=deepcopy(plan["tools"])))
    configurator = SessionConfigurator(root, prepared)
    await configurator.apply_saved_settings({"disabled": {"tools": [disabled_name]}})
    assert "image_generate" not in mounts
    assert plan == saved  # stale saved declaration is the original failure mode
    coord.unmount.assert_awaited_once_with("tools", name="image_generate")

    child = await spawn_boundary(plan, {"tools": [image(allow_paid=True)]}, coord=coord)
    assert child["tools"] == [{"module": "tool-unrelated"}]
    assert plan == saved
