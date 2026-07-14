"""Canonical app-level cleanup observability event names."""

CLEANUP_RENDER_BEGIN = "cleanup:render_begin"
CLEANUP_RENDER_END = "cleanup:render_end"
CLEANUP_STORE_BEGIN = "cleanup:store_begin"
CLEANUP_STORE_END = "cleanup:store_end"
CLEANUP_FINALLY_BEGIN = "cleanup:finally_begin"
CLEANUP_FINALLY_END = "cleanup:finally_end"

ALL_CLEANUP_EVENTS: tuple[str, ...] = (
    CLEANUP_RENDER_BEGIN,
    CLEANUP_RENDER_END,
    CLEANUP_STORE_BEGIN,
    CLEANUP_STORE_END,
    CLEANUP_FINALLY_BEGIN,
    CLEANUP_FINALLY_END,
)

__all__ = [
    "ALL_CLEANUP_EVENTS",
    "CLEANUP_FINALLY_BEGIN",
    "CLEANUP_FINALLY_END",
    "CLEANUP_RENDER_BEGIN",
    "CLEANUP_RENDER_END",
    "CLEANUP_STORE_BEGIN",
    "CLEANUP_STORE_END",
]
