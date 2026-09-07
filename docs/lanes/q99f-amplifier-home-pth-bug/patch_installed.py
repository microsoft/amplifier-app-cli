"""Apply the q99f guard to an ALREADY-INSTALLED amplifier_app_cli in a DTU."""
import glob, sys
from pathlib import Path

sp = glob.glob("/root/.local/share/uv/tools/amplifier/lib/*/site-packages")[0]
main_py = Path(sp) / "amplifier_app_cli" / "main.py"
src = main_py.read_text()
if "_guard_shared_venv_home" in src:
    print("already patched"); sys.exit(0)

anchor = "    _attach_llm_error_filter()\n    cli()"
assert anchor in src, "anchor not found in installed main.py"

guard_fn = '''def _guard_shared_venv_home() -> None:
    """Refuse to run when this environment belongs to a different AMPLIFIER_HOME."""
    from .lib.venv_home_guard import SharedVenvHomeError
    from .lib.venv_home_guard import enforce_home_ownership
    from .lib.venv_home_guard import format_conflict

    try:
        conflict = enforce_home_ownership(sys.argv[1:])
    except SharedVenvHomeError as e:
        console.print(f"[red]{escape_markup(str(e))}[/red]")
        sys.exit(1)
    except Exception as e:  # pragma: no cover
        logger.debug(f"venv home guard skipped: {e}")
        return

    if conflict is not None:
        console.print(
            f"[yellow]{escape_markup(format_conflict(conflict, override_active=True))}[/yellow]"
        )


'''

src = src.replace(anchor, "    _attach_llm_error_filter()\n    _guard_shared_venv_home()\n    cli()")
src = src.replace("def main():\n    \"\"\"Main entry point.\"\"\"", guard_fn + "def main():\n    \"\"\"Main entry point.\"\"\"", 1)
main_py.write_text(src)
print("patched", main_py)
