"""Architecture guards for the layered terminal facade and mixins."""

from pathlib import Path


LAYERED_REPL_MODULES = (
    "layered_repl.py",
    "layered_repl_agents.py",
    "layered_repl_approval.py",
    "layered_repl_config.py",
    "layered_repl_input.py",
    "layered_repl_layout.py",
    "layered_repl_lifecycle.py",
    "layered_repl_navigation.py",
    "layered_repl_status.py",
    "layered_repl_style.py",
    "layered_repl_surfaces.py",
    "layered_repl_terminal.py",
)


def test_layered_repl_modules_remain_focused() -> None:
    ui_dir = Path("amplifier_app_cli/ui")

    for name in LAYERED_REPL_MODULES:
        line_count = len((ui_dir / name).read_text(encoding="utf-8").splitlines())
        assert line_count < 500, f"{name} grew to {line_count} lines"
