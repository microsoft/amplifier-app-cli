"""Merge utilities for tool configurations.

This module provides app-level policy for how tool configs should be merged.
The key policy decision: permission fields (allowed_write_paths, allowed_read_paths,
denied_write_paths) should be UNIONED rather than replaced, so user/session settings
ADD to bundle defaults.
"""

from typing import Any

# Fields that should be unioned (combined) rather than replaced during merge.
# These are permission/capability fields where the user expectation is "add to"
# rather than "replace".
UNION_CONFIG_FIELDS = frozenset({
    "allowed_write_paths",
    "allowed_read_paths",
    "denied_write_paths",
})


def merge_tool_configs(base_config: dict[str, Any], overlay_config: dict[str, Any]) -> dict[str, Any]:
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
            base_list = base_config[field] if isinstance(base_config[field], list) else []
            overlay_list = overlay_config[field] if isinstance(overlay_config[field], list) else []
            merged[field] = list(set(base_list) | set(overlay_list))

    return merged
