"""
Collection utilities - Search paths and helpers.

CRITICAL (KERNEL_PHILOSOPHY): Search paths are APP LAYER POLICY, not kernel mechanism.

Per KERNEL_PHILOSOPHY:
- "Could two teams want different behavior?" → YES → APP LAYER
- Collection search order is POLICY (different apps might want different precedence)
- Kernel doesn't know about collections at all

Per AGENTS.md: Ruthless simplicity - direct filesystem operations.
"""

from pathlib import Path


def get_collection_search_paths() -> list[Path]:
    """
    Get collection search paths in precedence order (APP LAYER POLICY).

    Search order (lowest to highest precedence):
    1. Bundled collections (shipped with package) - lowest precedence
    2. User global collections (~/.amplifier/collections/)
    3. Project local collections (.amplifier/collections/) - highest precedence

    This is POLICY, not mechanism. Different applications could use:
    - Different search orders
    - Different directory names
    - Different precedence rules

    The kernel doesn't know about collections - this is app-layer decision.

    Returns:
        List of Path objects in search order (lowest to highest precedence)
    """
    # Bundled data directory (package_dir/data)
    # Same pattern as ProfileLoader uses
    package_dir = Path(__file__).parent.parent  # amplifier_app_cli package
    bundled_collections = package_dir / "data" / "collections"

    return [
        bundled_collections,  # 1. Bundled (lowest precedence)
        Path.home() / ".amplifier/collections",  # 2. User global
        Path.cwd() / ".amplifier/collections",  # 3. Project local (highest precedence)
    ]
