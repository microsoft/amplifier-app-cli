from __future__ import annotations

import json
import logging
import sys
from decimal import Decimal
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from amplifier_app_cli.runtime import amplifier_compat


class _ModelWithDecimal:
    def model_dump(self, **_kwargs):
        return {"cost": Decimal("0.32")}


def test_missing_optional_hook_module_needs_no_compatibility(monkeypatch) -> None:
    importer = MagicMock(
        side_effect=ModuleNotFoundError(
            "hooks logging unavailable",
            name="amplifier_module_hooks_logging",
        )
    )
    monkeypatch.setattr(amplifier_compat, "import_module", importer)

    assert amplifier_compat.install_hook_serialization_compatibility() is False
    importer.assert_called_once_with("amplifier_module_hooks_logging")


def test_missing_transitive_hook_dependency_is_not_hidden(monkeypatch) -> None:
    failure = ModuleNotFoundError(
        "hook dependency unavailable",
        name="hooks_logging_dependency",
    )
    monkeypatch.setattr(
        amplifier_compat,
        "import_module",
        MagicMock(side_effect=failure),
    )

    with pytest.raises(ModuleNotFoundError) as raised:
        amplifier_compat.install_hook_serialization_compatibility()

    assert raised.value is failure


def test_malformed_hook_import_is_not_hidden(monkeypatch) -> None:
    failure = ImportError("broken hooks logging module")
    monkeypatch.setattr(
        amplifier_compat,
        "import_module",
        MagicMock(side_effect=failure),
    )

    with pytest.raises(ImportError) as raised:
        amplifier_compat.install_hook_serialization_compatibility()

    assert raised.value is failure


def test_hook_module_without_private_serializer_is_left_untouched(monkeypatch) -> None:
    module = ModuleType("amplifier_module_hooks_logging")
    monkeypatch.setitem(sys.modules, "amplifier_module_hooks_logging", module)

    assert amplifier_compat.install_hook_serialization_compatibility() is False


def test_broken_hook_serializer_is_probed_patched_and_warned_once(
    monkeypatch,
    caplog,
) -> None:
    module = ModuleType("amplifier_module_hooks_logging")
    module._sanitize_for_json = lambda value: value
    monkeypatch.setitem(sys.modules, "amplifier_module_hooks_logging", module)
    amplifier_compat._patched_modules.discard(id(module))

    with caplog.at_level(logging.WARNING):
        assert amplifier_compat.install_hook_serialization_compatibility() is True
        assert amplifier_compat.install_hook_serialization_compatibility() is True

    safe = module._sanitize_for_json({"model": _ModelWithDecimal()})
    assert safe == {"model": {"cost": "0.32"}}
    assert json.dumps(safe)
    assert (
        sum("compatibility adapter" in record.message for record in caplog.records) == 1
    )


def test_json_safe_hook_serializer_is_left_untouched(monkeypatch) -> None:
    module = ModuleType("amplifier_module_hooks_logging")
    serializer = amplifier_compat.json_safe_value
    module._sanitize_for_json = serializer
    monkeypatch.setitem(sys.modules, "amplifier_module_hooks_logging", module)
    amplifier_compat._patched_modules.discard(id(module))

    assert amplifier_compat.install_hook_serialization_compatibility() is False
    assert module._sanitize_for_json is serializer
