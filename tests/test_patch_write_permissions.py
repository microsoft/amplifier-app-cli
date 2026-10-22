"""Operator filesystem write permissions reach only the canonical patch module.

These tests exercise configuration resolution with synthetic dictionaries.
They do not load tool implementations, operator settings, or providers.
"""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import pytest

from amplifier_app_cli.agent_config import merge_configs
from amplifier_app_cli.lib.merge_utils import merge_tool_configs
from amplifier_app_cli.lib.settings import AppSettings, NotificationFlags, SettingsPaths
from amplifier_app_cli.runtime.config import (
    _apply_tool_overrides,
    resolve_bundle_config,
)


@pytest.fixture
def patch_tool():
    return {
        "module": "tool-apply-patch",
        "source": "./modules/patch",
        "config": {
            "engine": "function",
            "allowed_write_paths": ["."],
            "denied_write_paths": ["protected"],
        },
    }


def _entry(tools, module):
    matches = [tool for tool in tools if tool["module"] == module]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("filesystem_declared", [False, True])
def test_operator_write_paths_reach_patch_without_other_filesystem_config(
    patch_tool, filesystem_declared
):
    patch_tool["config"]["additional_allowed_write_paths"] = [
        "patch-extra",
        "shared-output",
        "patch-extra",
    ]
    tools = [patch_tool]
    if filesystem_declared:
        tools.append(
            {
                "module": "tool-filesystem",
                "config": {
                    "allowed_write_paths": ["bundle-output"],
                    "denied_write_paths": ["bundle-protected"],
                },
            }
        )
    overrides = [
        {
            "module": "tool-filesystem",
            "source": "./modules/filesystem",
            "config": {
                "allowed_write_paths": ["shared-output", "shared-cache"],
                "denied_write_paths": ["shared-protected"],
                "allowed_read_paths": ["read-only"],
                "denied_read_paths": ["read-protected"],
                "engine": "filesystem-engine",
                "filesystem_only": True,
            },
        }
    ]

    result = _apply_tool_overrides(tools, overrides)

    assert _entry(result, "tool-apply-patch") == {
        "module": "tool-apply-patch",
        "source": "./modules/patch",
        "config": {
            "engine": "function",
            "allowed_write_paths": ["."],
            "additional_allowed_write_paths": [
                "patch-extra",
                "shared-output",
                "shared-cache",
            ],
            "denied_write_paths": ["protected", "shared-protected"],
        },
    }


def test_operator_deny_only_reaches_patch_without_creating_an_allow_list():
    tools = [
        {
            "module": "tool-apply-patch",
            "config": {"engine": "function", "denied_write_paths": ["protected"]},
        }
    ]
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {"denied_write_paths": ["shared-protected"]},
        }
    ]

    result = _apply_tool_overrides(tools, overrides)

    assert _entry(result, "tool-apply-patch")["config"] == {
        "engine": "function",
        "denied_write_paths": ["protected", "shared-protected"],
    }


@pytest.mark.parametrize("filesystem_override_first", [False, True])
def test_patch_override_merges_normally_then_shared_paths_union_in_stable_order(
    patch_tool, filesystem_override_first
):
    patch_tool["config"]["allowed_write_paths"].append("workspace")
    patch_tool["config"]["denied_write_paths"].append("common-protected")
    patch_tool["config"]["additional_allowed_write_paths"] = ["bundle-extra"]
    patch_override = {
        "module": "tool-apply-patch",
        "source": "./modules/custom-patch",
        "config": {
            "engine": "custom-engine",
            "allowed_write_paths": ["workspace", "patch-output"],
            "additional_allowed_write_paths": [
                "patch-extra",
                "shared-output",
                "patch-extra",
            ],
            "denied_write_paths": ["common-protected", "patch-protected"],
            "allowed_read_paths": ["patch-input"],
            "patch_only": True,
        },
    }
    filesystem_override = {
        "module": "tool-filesystem",
        "config": {
            "allowed_write_paths": [
                "shared-output",
                "workspace",
                "shared-output",
            ],
            "denied_write_paths": [
                "common-protected",
                "shared-protected",
                "shared-protected",
            ],
            "allowed_read_paths": ["filesystem-input"],
            "engine": "filesystem-engine",
        },
    }
    overrides = [patch_override, filesystem_override]
    if filesystem_override_first:
        overrides.reverse()

    result = _apply_tool_overrides([patch_tool], overrides)

    # The ordinary patch-specific merge uses an unordered permission union.
    # Sharing must append to that result, not redefine its existing order.
    expected_config = merge_tool_configs(patch_tool["config"], patch_override["config"])
    # The additive field still follows ordinary overlay replacement first.
    expected_config["additional_allowed_write_paths"] = [
        "patch-extra",
        "shared-output",
        "workspace",
    ]
    expected_config["denied_write_paths"].append("shared-protected")
    assert _entry(result, "tool-apply-patch") == {
        "module": "tool-apply-patch",
        "source": "./modules/custom-patch",
        "config": expected_config,
    }


def test_patch_specific_override_still_works_without_shared_permissions(patch_tool):
    overrides = [
        {
            "module": "tool-apply-patch",
            "source": "./modules/custom-patch",
            "config": {"engine": "custom-engine", "patch_only": True},
        }
    ]

    result = _apply_tool_overrides([patch_tool], overrides)

    assert _entry(result, "tool-apply-patch") == {
        "module": "tool-apply-patch",
        "source": "./modules/custom-patch",
        "config": {
            "engine": "custom-engine",
            "allowed_write_paths": ["."],
            "denied_write_paths": ["protected"],
            "patch_only": True,
        },
    }


@pytest.mark.parametrize(
    "overrides",
    [
        [],
        [{"module": "tool-other", "config": {"enabled": True}}],
        [{"module": "tool-filesystem", "config": {"engine": "custom-engine"}}],
    ],
    ids=["no-overrides", "unrelated-override", "non-write-filesystem-override"],
)
def test_filesystem_bundle_defaults_are_not_operator_permissions(patch_tool, overrides):
    expected_patch = deepcopy(patch_tool)
    tools = [
        patch_tool,
        {
            "module": "tool-filesystem",
            "config": {
                "allowed_write_paths": ["bundle-output"],
                "denied_write_paths": ["bundle-protected"],
            },
        },
    ]

    result = _apply_tool_overrides(tools, overrides)

    assert _entry(result, "tool-apply-patch") == expected_patch


@pytest.mark.parametrize(
    "module",
    [
        "apply_patch",
        "tool-apply_patch",
        "tool-apply-patch-extra",
        "Tool-Apply-Patch",
        "tool-other",
    ],
)
def test_patch_aliases_lookalikes_and_unrelated_tools_receive_no_grants(
    patch_tool, module
):
    other_tool = {
        "module": module,
        "source": "./modules/tool-apply-patch",
        "config": {"engine": "function", "allowed_write_paths": ["own-output"]},
    }
    expected_other = deepcopy(other_tool)
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {
                "allowed_write_paths": ["shared-output"],
                "denied_write_paths": ["shared-protected"],
            },
        }
    ]

    result = _apply_tool_overrides([patch_tool, other_tool], overrides)

    assert _entry(result, module) == expected_other


@pytest.mark.parametrize(
    "module", ["filesystem", "tool_filesystem", "tool-filesystem-extra"]
)
def test_noncanonical_filesystem_identity_does_not_share_permissions(patch_tool, module):
    expected_patch = deepcopy(patch_tool)
    overrides = [
        {
            "module": module,
            "source": "./modules/tool-filesystem",
            "config": {
                "allowed_write_paths": ["shared-output"],
                "denied_write_paths": ["shared-protected"],
            },
        }
    ]

    result = _apply_tool_overrides([patch_tool], overrides)

    assert _entry(result, "tool-apply-patch") == expected_patch


@pytest.mark.parametrize("tools", [[], [{"module": "tool-other", "config": {}}]])
def test_filesystem_override_does_not_create_an_absent_patch_module(tools):
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {"allowed_write_paths": ["shared-output"]},
        }
    ]

    result = _apply_tool_overrides(tools, overrides)

    assert [tool["module"] for tool in result] == [
        *(tool["module"] for tool in tools),
        "tool-filesystem",
    ]


@pytest.mark.parametrize(
    "config",
    [
        None,
        {"engine": "function"},
        {"engine": "function", "working_dir": "workspace"},
    ],
    ids=["no-config", "implicit-cwd", "configured-working-dir"],
)
def test_nonempty_shared_grant_defers_omitted_patch_default_until_mount(config):
    tool = {"module": "tool-apply-patch"}
    if config is not None:
        tool["config"] = config
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {"allowed_write_paths": ["shared-output"]},
        }
    ]

    result = _apply_tool_overrides([tool], overrides)

    resolved = _entry(result, "tool-apply-patch")["config"]
    assert "allowed_write_paths" not in resolved
    assert resolved["additional_allowed_write_paths"] == ["shared-output"]
    assert ("working_dir" in resolved) == ("working_dir" in (config or {}))
    if config is not None:
        for key, value in config.items():
            assert resolved[key] == value


@pytest.mark.parametrize(
    "config",
    [
        {"engine": "function"},
        {"engine": "function", "working_dir": "workspace"},
        {"engine": "function", "allowed_write_paths": []},
        {
            "engine": "function",
            "allowed_write_paths": ["."],
            "denied_write_paths": ["protected"],
        },
    ],
    ids=["implicit-cwd", "configured-working-dir", "explicit-empty", "explicit-policy"],
)
@pytest.mark.parametrize("empty_operator_override", [False, True])
def test_no_shared_grants_leave_patch_policy_and_implicit_defaults_unchanged(
    config, empty_operator_override
):
    tool = {"module": "tool-apply-patch", "config": deepcopy(config)}
    overrides = []
    if empty_operator_override:
        overrides.append(
            {
                "module": "tool-filesystem",
                "config": {"allowed_write_paths": []},
            }
        )

    result = _apply_tool_overrides([tool], overrides)

    assert _entry(result, "tool-apply-patch") == {
        "module": "tool-apply-patch",
        "config": config,
    }


@pytest.mark.parametrize("working_dir", [None, "workspace"])
def test_explicit_empty_patch_allow_list_gets_grant_but_no_implicit_scope(working_dir):
    config = {"engine": "function", "allowed_write_paths": []}
    if working_dir is not None:
        config["working_dir"] = working_dir
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {"allowed_write_paths": ["shared-output"]},
        }
    ]

    result = _apply_tool_overrides(
        [{"module": "tool-apply-patch", "config": config}], overrides
    )

    resolved = _entry(result, "tool-apply-patch")["config"]
    assert resolved == {**config, "additional_allowed_write_paths": ["shared-output"]}


def test_resolution_does_not_mutate_cached_tools_or_nested_operator_lists(patch_tool):
    patch_tool["config"]["options"] = {"labels": ["bundle-label"]}
    patch_tool["config"]["additional_allowed_write_paths"] = ["bundle-extra"]
    tools = [
        patch_tool,
        {
            "module": "tool-filesystem",
            "config": {
                "allowed_write_paths": ["bundle-output"],
                "options": {"labels": ["filesystem-label"]},
            },
        },
    ]
    overrides = [
        {
            "module": "tool-apply-patch",
            "config": {
                "allowed_write_paths": ["patch-output"],
                "denied_write_paths": ["patch-protected"],
                "additional_allowed_write_paths": ["patch-extra"],
                "options": {"labels": ["operator-label"]},
            },
        },
        {
            "module": "tool-filesystem",
            "config": {
                "allowed_write_paths": ["shared-output", "shared-output"],
                "denied_write_paths": ["shared-protected", "shared-protected"],
                "allowed_read_paths": ["read-only"],
                "options": {"labels": ["operator-filesystem-label"]},
            },
        },
    ]
    original_tools = deepcopy(tools)
    original_overrides = deepcopy(overrides)

    first = _apply_tool_overrides(tools, overrides)
    first_snapshot = deepcopy(first)
    repeated = _apply_tool_overrides(tools, overrides)
    reapplied = _apply_tool_overrides(first, overrides)

    assert tools == original_tools
    assert overrides == original_overrides
    assert first == first_snapshot
    assert repeated == first
    # Reapplying the ordinary patch override may reorder permission sets.
    config = _entry(first, "tool-apply-patch")["config"]
    reapplied_patch = deepcopy(_entry(reapplied, "tool-apply-patch"))
    for field, expected in (
        ("allowed_write_paths", {".", "patch-output"}),
        ("denied_write_paths", {"protected", "patch-protected", "shared-protected"}),
        ("additional_allowed_write_paths", {"patch-extra", "shared-output"}),
    ):
        assert set(config[field]) == expected
        assert len(config[field]) == len(expected)
        assert set(reapplied_patch["config"][field]) == expected
        assert len(reapplied_patch["config"][field]) == len(expected)
        reapplied_patch["config"][field] = config[field]
    assert reapplied_patch == _entry(first, "tool-apply-patch")
    assert config["additional_allowed_write_paths"] == ["patch-extra", "shared-output"]
    for field in (
        "allowed_write_paths",
        "denied_write_paths",
        "additional_allowed_write_paths",
    ):
        config[field].append("result-only")
    assert tools == original_tools
    assert overrides == original_overrides


@pytest.mark.parametrize("grant", [[], ["shared-output"]])
def test_existing_filesystem_cwd_injection_is_unchanged(grant):
    tools = [{"module": "tool-filesystem", "config": {"allowed_write_paths": []}}]
    overrides = [
        {"module": "tool-filesystem", "config": {"allowed_write_paths": grant}}
    ]

    result = _apply_tool_overrides(tools, overrides)

    paths = _entry(result, "tool-filesystem")["config"]["allowed_write_paths"]
    assert paths == [".", *grant]


@pytest.mark.asyncio
async def test_empty_bundle_tools_resolve_explicit_patch_declaration_through_helper(
    monkeypatch, patch_tool
):
    """An explicitly configured patch module must not take the empty-tools bypass."""
    overrides = [
        patch_tool,
        {
            "module": "tool-filesystem",
            "config": {
                "allowed_write_paths": ["shared-output"],
                "denied_write_paths": ["shared-protected"],
            },
        },
    ]
    original_overrides = deepcopy(overrides)
    prepared = Mock()
    prepared.mount_plan = {"tools": []}
    prepared.bundle.tools = []
    prepared.bundle.providers = []
    prepared.bundle.hooks = []
    settings = Mock(spec=AppSettings)
    settings.get_config_overrides.return_value = {}
    settings.get_provider_overrides.return_value = []
    settings.get_tool_overrides.return_value = overrides
    settings.get_notification_hook_overrides.return_value = []
    settings.get_notification_flags.return_value = NotificationFlags(
        desktop_enabled=False, push_enabled=False
    )
    settings.get_routing_config.return_value = {}
    settings.get_app_bundles.return_value = []
    settings.get_source_overrides.return_value = {}
    settings.get_module_sources.return_value = {}
    settings.get_bundle_sources.return_value = {}

    # Isolate unrelated behavior selection as well as discovery/preparation.
    # Keep tool resolution, environment expansion, and bundle sync real.
    for builder in (
        "_build_modes_behaviors",
        "_build_app_cli_behaviors",
        "_build_skills_behaviors",
        "_build_routing_behaviors",
        "_build_wayfinder_behaviors",
        "_build_notification_behaviors",
    ):
        monkeypatch.setattr(
            f"amplifier_app_cli.runtime.config.{builder}", Mock(return_value=[])
        )

    with (
        patch(
            "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
            new_callable=AsyncMock,
            return_value=prepared,
        ),
        patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
        patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
        patch(
            "amplifier_app_cli.runtime.config._apply_tool_overrides",
            wraps=_apply_tool_overrides,
        ) as apply_overrides,
        patch("amplifier_app_cli.runtime.config.get_provider_info") as provider_info,
    ):
        config, returned_prepared = await resolve_bundle_config("test-bundle", settings)

    apply_overrides.assert_called_once_with([], overrides)
    provider_info.assert_not_called()
    settings.get_tool_overrides.assert_called_once_with(session_id=None, project_slug=None)
    assert returned_prepared is prepared
    resolved_patch = _entry(config["tools"], "tool-apply-patch")
    assert resolved_patch == {
        "module": "tool-apply-patch",
        "source": "./modules/patch",
        "config": {
            "engine": "function",
            "allowed_write_paths": ["."],
            "additional_allowed_write_paths": ["shared-output"],
            "denied_write_paths": ["protected", "shared-protected"],
        },
    }
    assert prepared.mount_plan["tools"] == config["tools"]
    assert prepared.bundle.tools == config["tools"]
    assert overrides == original_overrides


@pytest.mark.parametrize(
    ("field", "malformed"),
    [
        (field, malformed)
        for field in ("allowed_write_paths", "denied_write_paths")
        for malformed in (
            "private-value",
            {"path": "private-value"},
            [None],
            [1],
            [["private-value"]],
        )
    ]
    + [("allowed_write_paths", None)],
)
def test_malformed_shared_policy_rejects_assembly_before_grants(
    patch_tool, field, malformed
):
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {
                "allowed_write_paths": ["shared-output"],
                "denied_write_paths": ["protected-output"],
                field: malformed,
            },
        }
    ]
    original = deepcopy(patch_tool)
    original_overrides = deepcopy(overrides)
    with pytest.raises(ValueError, match=f"tool-filesystem {field}") as exc:
        _apply_tool_overrides([patch_tool], overrides)
    assert str(exc.value) == f"tool-filesystem {field} must be a list of path strings"
    assert "private-value" not in str(exc.value)
    assert patch_tool == original
    assert overrides == original_overrides


@pytest.mark.parametrize("include_patch", [False, True])
@pytest.mark.parametrize("shared_grants", [[], ["shared-output"]])
def test_null_shared_denies_preserve_filesystem_contract_without_clearing_patch_denies(
    patch_tool, include_patch, shared_grants
):
    tools = [patch_tool] if include_patch else []
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {
                "allowed_write_paths": shared_grants,
                "denied_write_paths": None,
            },
        }
    ]
    original_tools = deepcopy(tools)
    original_overrides = deepcopy(overrides)

    resolved = _apply_tool_overrides(tools, overrides)

    assert _entry(resolved, "tool-filesystem")["config"]["denied_write_paths"] is None
    if include_patch:
        config = _entry(resolved, "tool-apply-patch")["config"]
        assert config["denied_write_paths"] == ["protected"]
        assert config["allowed_write_paths"] == ["."]
        if shared_grants:
            assert config["additional_allowed_write_paths"] == shared_grants
        else:
            assert "additional_allowed_write_paths" not in config
    else:
        assert not any(tool.get("module") == "tool-apply-patch" for tool in resolved)
    assert tools == original_tools
    assert overrides == original_overrides


@pytest.mark.parametrize(
    "field",
    ["allowed_write_paths", "denied_write_paths", "additional_allowed_write_paths"],
)
@pytest.mark.parametrize(
    "malformed",
    [None, "/private-value", {"path": "private-value"}, [None], [1], [["private-value"]]],
)
@pytest.mark.parametrize("shared_grant", [False, True], ids=["deny-only", "grant-and-deny"])
def test_malformed_patch_policy_rejects_all_present_fields_without_mutation(
    patch_tool, field, malformed, shared_grant
):
    patch_tool["config"][field] = malformed
    shared = {"denied_write_paths": ["shared-protected"]}
    if shared_grant:
        shared["allowed_write_paths"] = ["shared-output"]
    overrides = [{"module": "tool-filesystem", "config": shared}]
    tools = [patch_tool]
    original_tools = deepcopy(tools)
    original_overrides = deepcopy(overrides)

    with pytest.raises(ValueError) as exc:
        _apply_tool_overrides(tools, overrides)

    assert str(exc.value) == f"tool-apply-patch {field} must be a list of path strings"
    assert "private-value" not in str(exc.value)
    assert tools == original_tools
    assert overrides == original_overrides


def test_all_declared_patch_entries_receive_shared_policy(patch_tool):
    second = deepcopy(patch_tool)
    second["id"] = "second-patch"
    second["config"]["allowed_write_paths"] = ["second-output"]
    tools = [patch_tool, second]
    original = deepcopy(tools)
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {
                "allowed_write_paths": ["shared-output"],
                "denied_write_paths": ["shared-protected"],
            },
        }
    ]
    result = _apply_tool_overrides(tools, overrides)
    patches = [tool for tool in result if tool["module"] == "tool-apply-patch"]
    assert len(patches) == 2
    for tool in patches:
        assert tool["config"]["denied_write_paths"] == [
            "protected",
            "shared-protected",
        ]
        assert tool["config"]["additional_allowed_write_paths"] == ["shared-output"]
    assert patches[0]["config"]["allowed_write_paths"] == ["."]
    assert patches[1]["config"]["allowed_write_paths"] == ["second-output"]
    assert tools == original


@pytest.mark.parametrize("scope", ["global", "project", "local", "session"])
def test_real_settings_scopes_share_write_permissions(tmp_path, patch_tool, scope):
    paths = SettingsPaths(
        global_settings=tmp_path / "global.yaml",
        project_settings=tmp_path / "project.yaml",
        local_settings=tmp_path / "local.yaml",
        session_settings=tmp_path / "session.yaml",
    )
    settings = AppSettings(paths)
    grant = str(tmp_path / scope / "output")
    deny = str(tmp_path / scope / "protected")
    settings.add_allowed_write_path(grant, scope)
    settings.add_denied_write_path(deny, scope)

    resolved = _entry(
        _apply_tool_overrides([patch_tool], settings.get_tool_overrides()),
        "tool-apply-patch",
    )["config"]
    assert resolved["allowed_write_paths"] == ["."]
    assert resolved["additional_allowed_write_paths"] == [grant]
    assert set(resolved["denied_write_paths"]) == {"protected", deny}
    assert len(resolved["allowed_write_paths"]) == 1
    assert len(resolved["denied_write_paths"]) == 2


def test_real_settings_remove_leaves_no_derived_patch_grant(tmp_path, patch_tool):
    settings = AppSettings(
        SettingsPaths(
            global_settings=tmp_path / "global.yaml",
            project_settings=tmp_path / "project.yaml",
            local_settings=tmp_path / "local.yaml",
        )
    )
    grant = str(tmp_path / "external")
    settings.add_allowed_write_path(grant)
    assert grant in _entry(
        _apply_tool_overrides([patch_tool], settings.get_tool_overrides()),
        "tool-apply-patch",
    )["config"]["additional_allowed_write_paths"]
    assert settings.remove_allowed_write_path(grant)
    reconstructed = _entry(
        _apply_tool_overrides([patch_tool], settings.get_tool_overrides()),
        "tool-apply-patch",
    )
    assert reconstructed == patch_tool


@pytest.mark.parametrize("explicit_child_allow", [False, True])
def test_actual_child_merge_preserves_deferred_grants_and_config_ownership(
    explicit_child_allow,
):
    tools = [
        {
            "module": "tool-apply-patch",
            "id": identity,
            "source": f"./modules/{identity}",
            "config": {"engine": "function"},
        }
        for identity in ("primary-patch", "secondary-patch")
    ]
    overrides = [
        {
            "module": "tool-filesystem",
            "config": {"allowed_write_paths": ["shared-output"]},
        }
    ]
    parent = {"tools": _apply_tool_overrides(tools, overrides)}
    overlay = {}
    if explicit_child_allow:
        overlay = {
            "tools": [
                {
                    "module": "tool-apply-patch",
                    "id": "primary-patch",
                    "config": {"allowed_write_paths": []},
                }
            ]
        }
    original_parent = deepcopy(parent)
    original_overlay = deepcopy(overlay)

    child = merge_configs(parent, overlay)
    sibling = merge_configs(parent, overlay)
    parent_patches = {
        tool["id"]: tool
        for tool in parent["tools"]
        if tool["module"] == "tool-apply-patch"
    }
    child_patches = {
        tool["id"]: tool
        for tool in child["tools"]
        if tool["module"] == "tool-apply-patch"
    }
    sibling_patches = {
        tool["id"]: tool
        for tool in sibling["tools"]
        if tool["module"] == "tool-apply-patch"
    }
    assert set(child_patches) == {"primary-patch", "secondary-patch"}
    for identity, tool in child_patches.items():
        config = tool["config"]
        assert tool["source"] == parent_patches[identity]["source"]
        assert tool is not parent_patches[identity]
        assert config is not parent_patches[identity]["config"]
        assert config is not sibling_patches[identity]["config"]
        assert "working_dir" not in config
        assert config["additional_allowed_write_paths"] == ["shared-output"]
        assert config["additional_allowed_write_paths"] is not (
            parent_patches[identity]["config"]["additional_allowed_write_paths"]
        )
        if explicit_child_allow and identity == "primary-patch":
            assert config["allowed_write_paths"] == []
            assert config["allowed_write_paths"] is not (
                overlay["tools"][0]["config"]["allowed_write_paths"]
            )
        else:
            assert "allowed_write_paths" not in config
    child_patches["primary-patch"]["config"]["additional_allowed_write_paths"].append(
        "child-only"
    )
    child_patches["primary-patch"]["config"]["working_dir"] = "child-workspace"
    assert child_patches["secondary-patch"]["config"] == {
        "engine": "function",
        "additional_allowed_write_paths": ["shared-output"],
    }
    assert parent == original_parent
    assert overlay == original_overlay
    assert sibling == merge_configs(original_parent, original_overlay)