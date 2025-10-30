"""BBS-style ANSI art banners for interactive sessions.

Uses the ansiterm library for authentic 90s-style ANSI art rendering.

Philosophy alignment:
- Ruthless simplicity: Just render .ANS files, no complex generation
- Mechanism, not policy: ansiterm handles rendering, we handle file selection
- Modular design: Separate library (ansiterm) with stable interface
"""

import sys
from pathlib import Path

# Available banner styles
AVAILABLE_STYLES = ["classic", "cyber", "underground", "matrix", "retro", "amber"]


def load_banner(style: str = "classic", version: str = "0.1.0") -> None:
    """Load and display a BBS-style ANSI art banner.

    Args:
        style: Banner style (classic, cyber, underground, matrix, retro)
        version: Version string (substituted in banner if supported)

    Philosophy:
        - Use ansiterm for rendering (proper BBS art library)
        - Fail gracefully with simple fallback
        - .ANS files generated programmatically, not hand-coded

    Note:
        The version parameter is preserved for API compatibility but .ANS files
        have the version baked in at generation time.
    """
    # Validate style
    if style not in AVAILABLE_STYLES:
        style = "classic"

    # Find .ANS file
    banner_file = Path(__file__).parent / "art" / f"{style}.ans"

    if not banner_file.exists():
        # Fallback to simple banner
        _render_fallback(version)
        return

    try:
        # Use ansiterm to render authentic BBS art
        # Import here to avoid hard dependency at module load
        from ansiterm import render_file

        render_file(banner_file, use_alt_screen=False)

    except Exception as e:
        # Fallback on any error
        print(f"Warning: Could not render banner: {e}", file=sys.stderr)
        _render_fallback(version)


def _render_fallback(version: str) -> None:
    """Simple text fallback if ansiterm unavailable."""
    print("\nAmplifier Interactive Session\n")
    print(f"Version: {version}\n")
    print("Commands: /help │ Multi-line: Ctrl-J │ Exit: Ctrl-D\n")


def get_available_styles() -> list[str]:
    """Get list of available banner styles."""
    return AVAILABLE_STYLES.copy()
