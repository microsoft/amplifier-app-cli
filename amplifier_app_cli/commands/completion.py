"""Shell completion installation helpers for the top-level CLI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from amplifier_app_cli.console import console


def detect_shell() -> str | None:
    """Return a supported shell name from ``$SHELL``."""
    shell_name = Path(os.environ.get("SHELL", "")).name.lower()
    for candidate in ("bash", "zsh", "fish"):
        if candidate in shell_name:
            return candidate
    return None


def shell_config_file(shell: str) -> Path:
    """Return the standard completion configuration path for a shell."""
    home = Path.home()
    if shell == "bash":
        bashrc = home / ".bashrc"
        return bashrc if bashrc.exists() else home / ".bash_profile"
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "completions" / "amplifier.fish"
    return home / f".{shell}rc"


def completion_already_installed(config_file: Path, shell: str) -> bool:
    """Return whether the Click completion marker is already installed."""
    if not config_file.exists():
        return False
    try:
        return f"_AMPLIFIER_COMPLETE={shell}_source" in config_file.read_text(
            encoding="utf-8"
        )
    except OSError:
        return False


def can_safely_modify(config_file: Path) -> bool:
    """Return whether the completion path can be created or appended."""
    if config_file.exists():
        return os.access(config_file, os.W_OK)
    parent = config_file.parent
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
    return os.access(parent, os.W_OK)


def install_completion_to_config(config_file: Path, shell: str) -> bool:
    """Install generated completion into the selected shell configuration."""
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        if shell == "fish":
            result = subprocess.run(
                ["amplifier"],
                env={**os.environ, "_AMPLIFIER_COMPLETE": "fish_source"},
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return False
            config_file.write_text(result.stdout, encoding="utf-8")
            return True
        with config_file.open("a", encoding="utf-8") as handle:
            handle.write("\n# Amplifier shell completion\n")
            handle.write(f'eval "$(_AMPLIFIER_COMPLETE={shell}_source amplifier)"\n')
        return True
    except OSError:
        return False


def show_manual_instructions(shell: str, config_file: Path) -> None:
    """Print a manual completion fallback."""
    console.print(f"\n[yellow]Add this line to {config_file}:[/yellow]")
    if shell == "fish":
        console.print(
            f"  [cyan]_AMPLIFIER_COMPLETE=fish_source amplifier > {config_file}[/cyan]"
        )
    else:
        console.print(
            f'  [cyan]eval "$(_AMPLIFIER_COMPLETE={shell}_source amplifier)"[/cyan]'
        )
    console.print("\n[dim]Then reload your shell or start a new terminal.[/dim]")


__all__ = [
    "can_safely_modify",
    "completion_already_installed",
    "detect_shell",
    "install_completion_to_config",
    "shell_config_file",
    "show_manual_instructions",
]
