"""Regression tests: no-active-bundle default must be 'anchors' (not 'foundation')."""
from unittest.mock import patch

# Import the module directly: commands/__init__ re-exports the `tool` click Group,
# which would shadow the submodule under `from ... import tool`.
import importlib
tool_cmd = importlib.import_module("amplifier_app_cli.commands.tool")


def test_should_use_bundle_defaults_to_anchors():
    with patch.object(tool_cmd, "_get_active_bundle_name", return_value=None):
        use_bundle, bundle_name, _ = tool_cmd._should_use_bundle()
    assert use_bundle is True
    assert bundle_name == "anchors"


def test_should_use_bundle_respects_explicit_active():
    with patch.object(tool_cmd, "_get_active_bundle_name", return_value="foundation"):
        use_bundle, bundle_name, _ = tool_cmd._should_use_bundle()
    assert use_bundle is True
    assert bundle_name == "foundation"
