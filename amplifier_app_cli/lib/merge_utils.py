"""Merge utilities for configurations.

This module provides app-level policy for how configs should be merged:
- Tool configs: permission fields are UNIONED rather than replaced
- Profile/agent configs: module lists merged by module ID
- General: deep merge with overlay winning
"""

from typing import Any


# ===== Deep Merge Utilities =====


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts, with overlay winning conflicts.

    Arrays are replaced, not concatenated (consistent with original behavior).
    """
    result = base.copy()

    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def merge_module_lists(
    base: list[dict[str, Any]],
    overlay: list[dict[str, Any]],
    key_field: str = "module",
) -> list[dict[str, Any]]:
    """Merge module lists by module ID, with overlay configs merged into base.

    If a module appears in both lists, configs are deep-merged (overlay wins).
    Modules only in overlay are appended.
    """
    # Index base by key
    base_by_key: dict[str, dict[str, Any]] = {}
    for item in base:
        if key_field in item:
            base_by_key[item[key_field]] = item.copy()

    # Merge overlay
    for item in overlay:
        key = item.get(key_field)
        if key and key in base_by_key:
            # Merge configs
            base_by_key[key] = deep_merge(base_by_key[key], item)
        elif key:
            # New module
            base_by_key[key] = item.copy()

    return list(base_by_key.values())


def merge_module_items(
    parent_item: dict[str, Any], child_item: dict[str, Any]
) -> dict[str, Any]:
    """Deep merge a single module item (hook/tool/provider config).

    Special handling for 'config' field - deep merged rather than replaced.
    All other fields follow standard merge rules (child overrides parent).

    Args:
        parent_item: Parent module item
        child_item: Child module item

    Returns:
        Merged module item
    """
    merged = parent_item.copy()

    for key, value in child_item.items():
        if key == "config" and key in merged:
            # Deep merge configs
            if isinstance(merged["config"], dict) and isinstance(value, dict):
                merged["config"] = deep_merge(merged["config"], value)
            else:
                # Type mismatch or not dicts - child overrides
                merged["config"] = value
        else:
            # All other fields: child overrides parent (including 'source')
            merged[key] = value

    return merged


def merge_profile_dicts(
    parent: dict[str, Any], child: dict[str, Any]
) -> dict[str, Any]:
    """Deep merge child profile/agent dictionary into parent.

    Merge rules by key:
    - 'hooks', 'tools', 'providers': Merge module lists by module ID
    - Dict values: Recursive deep merge
    - Other values: Child overrides parent

    Args:
        parent: Parent dictionary
        child: Child dictionary

    Returns:
        Merged dictionary with child values taking precedence
    """
    merged = parent.copy()

    for key, child_value in child.items():
        if key not in merged:
            # New key in child - just add it
            merged[key] = child_value
        elif key in ("hooks", "tools", "providers"):
            # Module lists - merge by module ID
            merged[key] = merge_module_lists(merged[key], child_value)
        elif isinstance(child_value, dict) and isinstance(merged[key], dict):
            # Both are dicts - recursive deep merge
            merged[key] = deep_merge(merged[key], child_value)
        else:
            # Scalar or type mismatch - child overrides parent
            merged[key] = child_value

    return merged


# Fields that should be unioned (combined) rather than replaced during merge.
# These are permission/capability fields where the user expectation is "add to"
# rather than "replace".
UNION_CONFIG_FIELDS = frozenset(
    {
        "allowed_write_paths",
        "allowed_read_paths",
        "denied_write_paths",
    }
)


def merge_tool_configs(
    base_config: dict[str, Any], overlay_config: dict[str, Any]
) -> dict[str, Any]:
    """Merge tool configs with special handling for permission fields.

    For most fields, overlay replaces base (standard behavior).
    For permission fields (UNION_CONFIG_FIELDS), lists are combined via set union.

    Args:
        base_config: The base configuration (e.g., from bundle)
        overlay_config: The overlay configuration (e.g., from user settings)

    Returns:
        Merged configuration dict
    """
    merged = {**base_config, **overlay_config}

    # Union permission fields instead of replacing
    for field in UNION_CONFIG_FIELDS:
        if field in base_config and field in overlay_config:
            base_list = (
                base_config[field] if isinstance(base_config[field], list) else []
            )
            overlay_list = (
                overlay_config[field] if isinstance(overlay_config[field], list) else []
            )
            merged[field] = list(set(base_list) | set(overlay_list))

    return merged
