"""Narrow, probed compatibility adapters for older Amplifier components."""

from __future__ import annotations

from importlib import import_module
import json
import logging
from decimal import Decimal
from importlib import metadata
from typing import Any

from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

_HOOKS_LOGGING_DISTRIBUTION = "amplifier-module-hooks-logging"
_HOOKS_LOGGING_MODULE = "amplifier_module_hooks_logging"
_KNOWN_JSON_SAFE_VERSION = Version("1.0.0")
_patched_modules: set[int] = set()


def install_hook_serialization_compatibility() -> bool:
    """Patch a known-old hook serializer only when a runtime probe fails.

    Returns ``True`` when the compatibility adapter is active. Current
    releases pass the probe and remain untouched.
    """
    try:
        hooks_logging = import_module(_HOOKS_LOGGING_MODULE)
    except ModuleNotFoundError as error:
        if error.name == _HOOKS_LOGGING_MODULE:
            return False
        raise

    module_id = id(hooks_logging)
    if module_id in _patched_modules:
        return True
    serializer = getattr(hooks_logging, "_sanitize_for_json", None)
    if not callable(serializer) or _serializer_is_json_safe(serializer):
        return False

    installed = _distribution_version(_HOOKS_LOGGING_DISTRIBUTION)
    release_note = (
        "unexpected regression in a nominally compatible release"
        if installed is not None and installed >= _KNOWN_JSON_SAFE_VERSION
        else "legacy serializer behavior"
    )
    logger.warning(
        "Activating Amplifier hook serialization compatibility adapter for %s "
        "(%s; %s). Upgrade the hooks-logging module and remove this adapter "
        "once its public serializer contract is JSON-safe.",
        installed or "unknown version",
        _HOOKS_LOGGING_DISTRIBUTION,
        release_note,
    )
    setattr(hooks_logging, "_sanitize_for_json", json_safe_value)
    _patched_modules.add(module_id)
    return True


def json_safe_value(value: Any, *, _seen: set[int] | None = None) -> Any:
    """Convert nested provider/accounting payloads into JSON-safe values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)

    if _seen is None:
        _seen = set()
    value_id = id(value)
    if value_id in _seen:
        return "<cycle>"
    _seen.add(value_id)

    try:
        if isinstance(value, dict):
            return {
                str(key): json_safe_value(item, _seen=_seen)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [json_safe_value(item, _seen=_seen) for item in value]
        if hasattr(value, "model_dump"):
            try:
                return json_safe_value(value.model_dump(mode="json"), _seen=_seen)
            except TypeError:
                return json_safe_value(value.model_dump(), _seen=_seen)
        if hasattr(value, "__dict__"):
            return json_safe_value(vars(value), _seen=_seen)
        return str(value)
    finally:
        _seen.discard(value_id)


def _serializer_is_json_safe(serializer: Any) -> bool:
    class ProbeModel:
        def model_dump(self, **_kwargs: Any) -> dict[str, Decimal]:
            return {"cost": Decimal("0.01")}

    try:
        json.dumps(serializer({"model": ProbeModel()}))
    except (TypeError, ValueError):
        return False
    return True


def _distribution_version(name: str) -> Version | None:
    try:
        return Version(metadata.version(name))
    except (metadata.PackageNotFoundError, InvalidVersion):
        return None


__all__ = ["install_hook_serialization_compatibility", "json_safe_value"]
