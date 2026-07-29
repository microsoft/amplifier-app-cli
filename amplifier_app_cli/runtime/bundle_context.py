"""Public, serializable bundle context for delegated CLI sessions."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

BUNDLE_CONTEXT_CAPABILITY = "session.bundle_context"


class SerializedBundleContext(TypedDict):
    module_paths: dict[str, str]
    mention_mappings: dict[str, str]
    bundle_package_paths: list[str]


def build_bundle_context(
    mount_plan: Mapping[str, Any],
    resolver: object,
    *,
    bundle: object | None = None,
    bundle_package_paths: Sequence[object] = (),
    base_context: Mapping[str, object] | None = None,
) -> SerializedBundleContext:
    """Build child-session context through public bundle and resolver APIs."""
    normalized = normalize_bundle_context(base_context) or _empty_context()
    module_paths = dict(normalized["module_paths"])
    get_module_source = getattr(resolver, "get_module_source", None)
    if callable(get_module_source):
        for module_id in sorted(_module_ids(mount_plan)):
            try:
                source = get_module_source(module_id)
            except Exception:
                logger.debug(
                    "Could not serialize source for module %s",
                    module_id,
                    exc_info=True,
                )
                continue
            clean_source = _path_text(source)
            if clean_source:
                module_paths[module_id] = clean_source

    mention_mappings = dict(normalized["mention_mappings"])
    if bundle is not None:
        source_base_paths = getattr(bundle, "source_base_paths", {})
        if isinstance(source_base_paths, Mapping):
            for namespace, path in source_base_paths.items():
                clean_namespace = str(namespace).strip()
                clean_path = _path_text(path)
                if clean_namespace and clean_path:
                    mention_mappings[clean_namespace] = clean_path
        bundle_name = str(getattr(bundle, "name", "") or "").strip()
        base_path = _path_text(getattr(bundle, "base_path", None))
        if bundle_name and base_path:
            mention_mappings.setdefault(bundle_name, base_path)

    package_paths = list(normalized["bundle_package_paths"])
    for path in bundle_package_paths:
        clean_path = _path_text(path)
        if clean_path and clean_path not in package_paths:
            package_paths.append(clean_path)

    return {
        "module_paths": module_paths,
        "mention_mappings": mention_mappings,
        "bundle_package_paths": package_paths,
    }


def normalize_bundle_context(
    value: Mapping[str, object] | None,
) -> SerializedBundleContext | None:
    """Validate and copy a serialized bundle-context capability."""
    if not isinstance(value, Mapping):
        return None
    module_paths = _string_mapping(value.get("module_paths"))
    mention_mappings = _string_mapping(value.get("mention_mappings"))
    package_value = value.get("bundle_package_paths", ())
    package_paths: list[str] = []
    if isinstance(package_value, Sequence) and not isinstance(
        package_value, (str, bytes)
    ):
        for item in package_value:
            clean_path = _path_text(item)
            if clean_path and clean_path not in package_paths:
                package_paths.append(clean_path)
    return {
        "module_paths": module_paths,
        "mention_mappings": mention_mappings,
        "bundle_package_paths": package_paths,
    }


def _module_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        module_id = value.get("module")
        if isinstance(module_id, str) and module_id.strip():
            found.add(module_id.strip())
        for nested in value.values():
            found.update(_module_ids(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            found.update(_module_ids(nested))
    return found


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, path in value.items():
        clean_key = str(key).strip()
        clean_path = _path_text(path)
        if clean_key and clean_path:
            result[clean_key] = clean_path
    return result


def _path_text(value: object) -> str:
    if not isinstance(value, (str, Path)):
        return ""
    return str(value).strip()


def _empty_context() -> SerializedBundleContext:
    return {
        "module_paths": {},
        "mention_mappings": {},
        "bundle_package_paths": [],
    }


__all__ = [
    "BUNDLE_CONTEXT_CAPABILITY",
    "SerializedBundleContext",
    "build_bundle_context",
    "normalize_bundle_context",
]
