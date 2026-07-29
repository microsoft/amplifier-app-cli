import ast
import importlib
from pathlib import Path

import pytest


COMMAND_MODULES = (
    "command_processor",
    "command_modes",
    "command_sessions",
    "command_config",
    "command_config_flags",
    "command_config_dashboard",
    "command_admin",
)


def test_main_reexports_command_processor_and_config_parser() -> None:
    from amplifier_app_cli.main import CommandProcessor as public_processor
    from amplifier_app_cli.main import _parse_config_flags as public_parser
    from amplifier_app_cli.ui.command_config_flags import parse_config_flags
    from amplifier_app_cli.ui.command_processor import CommandProcessor

    assert public_processor is CommandProcessor
    assert public_parser is parse_config_flags


@pytest.mark.parametrize("module_name", COMMAND_MODULES)
def test_command_modules_do_not_import_main(module_name: str) -> None:
    module = importlib.import_module(f"amplifier_app_cli.ui.{module_name}")
    source_path = Path(module.__file__ or "")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert "amplifier_app_cli.main" not in imported_modules
    assert "main" not in imported_modules


@pytest.mark.parametrize("module_name", COMMAND_MODULES)
def test_command_modules_stay_focused(module_name: str) -> None:
    module = importlib.import_module(f"amplifier_app_cli.ui.{module_name}")
    source_path = Path(module.__file__ or "")

    assert len(source_path.read_text(encoding="utf-8").splitlines()) < 500
