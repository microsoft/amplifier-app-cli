"""Narrow image permissions at the CLI's fresh-session and child boundaries.

This is host policy, not image routing or a portable tool implementation. The
already-resolved mount plans remain authoritative for every non-policy setting.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


_POLICY_MODULES = {"tool-image", "tool-filesystem"}


def _module(row: Any) -> str | None:
    if isinstance(row, str):
        return row
    return row.get("module") if isinstance(row, dict) else None


def _policies(rows: Any, *, overlay: bool = False) -> list[dict[str, Any]]:
    policies = []
    for row in rows if isinstance(rows, list) else []:
        module = _module(row)
        if module not in _POLICY_MODULES:
            continue
        config = row.get("config", {}) if isinstance(row, dict) else {}
        if not isinstance(config, dict):
            raise ValueError("Image and filesystem configuration must be dictionaries.")
        policy = {
            key: value
            for key, value in config.items()
            if key in {
                "allowed_read_paths",
                "allowed_write_paths",
                "denied_read_paths",
                "denied_write_paths",
            }
        }
        # The filesystem consumer accepts null write-denies as no additional
        # restrictions. Normalize only that source contract; image policy and
        # allowlists remain strict, and other declarations' denials still union.
        if module == "tool-filesystem" and policy.get("denied_write_paths") is None:
            policy.pop("denied_write_paths", None)
        # A complete image declaration requires a literal grant. Only omission
        # in a partial agent overlay means inherit rather than disable.
        if module == "tool-image" and (not overlay or "allow_paid" in config):
            policy["allow_paid"] = config.get("allow_paid") is True
        if module == "tool-image" and config.get("routing_blocked"):
            policy["routing_blocked"] = True
        policies.append(policy)
    return policies


def image_tool_restrictions(
    *declarations: list[Any], working_dir: str | Path | None = None
) -> dict[str, Any]:
    """Intersect explicit settings restrictions without changing account pins.

    Fresh-session settings are partial overlays: an omitted paid flag inherits
    the composed behavior, but any explicit nonliteral grant denies payment.
    Call with the unmerged declarations, before normal tool merging unions
    permission lists or replaces a denial. No input is mutated.
    """
    return _intersect_policies(
        [policy for rows in declarations for policy in _policies(rows, overlay=True)],
        working_dir=working_dir,
    )


def _read_mounts(coordinator: Any) -> Any:
    getter = getattr(coordinator, "get", None)
    if not callable(getter):
        return None
    try:
        return getter("tools")
    except (AttributeError, KeyError):
        # Older coordinators/test doubles may not expose this category. That
        # is unknown, not evidence that the parent removed the image tool.
        return None


def restrict_child_images(
    parent: dict[str, Any],
    child: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
    working_dir: str | Path | None = None,
    parent_coordinator: Any = None,
    tool_inheritance: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return an independent child plan with no broader image permissions.

    Allow lists intersect by path containment; deny lists union. Parent, live
    session, spawn-replacement, overlay and already-merged child restrictions
    all apply. The image tool's fixed API name binds the policy even when an
    agent changes its module instance ID. No provider/account choice is made.

    Run after ordinary merge/filtering and before either child constructor or
    subprocess dispatch. Never add an absent tool. A real mounted-tools dict
    lacking ``image_generate`` is removal evidence (including configurator
    opt-out); missing APIs and non-dict mocks are not.
    """
    result = deepcopy(child)
    rows = result.get("tools", [])
    if not any(_module(row) == "tool-image" for row in rows):
        return result

    plans = [parent]
    live = getattr(parent_coordinator, "config", None)
    if isinstance(live, dict) and live is not parent:
        plans.append(live)
    declarations = [plan.get("tools", []) for plan in plans]
    spawns = [plan.get("spawn") or {} for plan in plans]
    declarations.extend(spawn.get("tools", []) for spawn in spawns)

    # Explicit exclusions must not be undone by an agent redeclaring images.
    # Other modules retain the CLI's ordinary merge/filter precedence.
    image_names = {"tool-image", "image_generate"}
    for tools in [*declarations, rows]:
        for row in tools if isinstance(tools, list) else []:
            if isinstance(row, dict) and _module(row) == "tool-image":
                image_names.update(
                    name
                    for name in (row.get("id"), row.get("instance_id"))
                    if isinstance(name, str)
                )
    exclusions = [spawn.get("exclude_tools", []) for spawn in spawns]
    exclusions.append((tool_inheritance or {}).get("exclude_tools", []))
    excluded = any(
        name in image_names
        for names in exclusions
        if isinstance(names, list)
        for name in names
        if isinstance(name, str)
    )
    mounts = _read_mounts(parent_coordinator)
    if excluded or (isinstance(mounts, dict) and "image_generate" not in mounts):
        result["tools"] = [row for row in rows if _module(row) != "tool-image"]
        return result

    policies = [policy for tools in declarations for policy in _policies(tools)]
    policies.extend(_policies((overlay or {}).get("tools", []), overlay=True))
    # Preserve session-specific restrictions already present in the child plan.
    policies.extend(_policies(rows))
    if isinstance(mounts, dict):
        mounted_config = getattr(mounts.get("image_generate"), "config", None)
        if isinstance(mounted_config, dict):
            policies.extend(
                _policies([{"module": "tool-image", "config": mounted_config}])
            )

    restrictions = _intersect_policies(policies, working_dir=working_dir)
    for index, row in enumerate(rows):
        if _module(row) == "tool-image":
            if isinstance(row, str):
                row = rows[index] = {"module": row}
            row.setdefault("config", {}).update(deepcopy(restrictions))
    return result


def _intersect_policies(
    policies: list[dict[str, Any]], *, working_dir: str | Path | None
) -> dict[str, Any]:
    root = Path(working_dir or Path.cwd()).expanduser().resolve()

    def paths(values: Any) -> list[Path]:
        if not isinstance(values, list) or any(
            not isinstance(value, str) for value in values
        ):
            raise ValueError("Image file-access paths must be lists of strings.")
        # Match the shared tool: relative paths bind to the session working_dir,
        # not the shell cwd or a child overlay's tool-local working_dir field.
        return [
            (path if path.is_absolute() else root / path).resolve()
            for value in values
            for path in (Path(value).expanduser(),)
        ]

    restrictions: dict[str, Any] = {}
    for kind in ("read", "write"):
        allowed, denied = f"allowed_{kind}_paths", f"denied_{kind}_paths"
        limits = [paths(policy[allowed]) for policy in policies if allowed in policy]
        if limits:
            effective = limits[0]
            for limit in limits[1:]:
                effective = list(
                    dict.fromkeys(
                        a if a.is_relative_to(b) else b
                        for a in effective
                        for b in limit
                        if a.is_relative_to(b) or b.is_relative_to(a)
                    )
                )
            restrictions[allowed] = list(dict.fromkeys(str(path) for path in effective))
        if any(denied in policy for policy in policies):
            restrictions[denied] = list(
                dict.fromkeys(
                    str(path)
                    for policy in policies
                    for path in paths(policy.get(denied, []))
                )
            )
    if any(policy.get("allow_paid") is False for policy in policies):
        restrictions["allow_paid"] = False
    if any(policy.get("routing_blocked") for policy in policies):
        restrictions["routing_blocked"] = True
    return restrictions
