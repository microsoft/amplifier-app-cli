"""Architecture guards for the CLI entrypoint and interactive host."""

from pathlib import Path


def test_main_and_interactive_host_remain_focused_modules() -> None:
    package = Path("amplifier_app_cli")
    for relative_path in ("main.py", "runtime/interactive_host.py"):
        source = (package / relative_path).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 500, relative_path


def test_main_delegates_interactive_runtime_assembly() -> None:
    source = Path("amplifier_app_cli/main.py").read_text(encoding="utf-8")
    assert "run_interactive_host(request, dependencies)" in source
    assert "InteractiveTurnRunner(" not in source
    assert "LayeredReplApp(" not in source
