"""Bounded per-(block, width) cache of rendered ANSI for transcript reflow.

Transcript blocks are frozen dataclasses, so an unchanged block re-rendered at
an unchanged width always produces the same ANSI. Caching on ``(block, width)``
lets resize reflow — and future pagers — skip re-rendering every retained
block whose width did not change. The cache is a strict LRU bounded by entry
count; unhashable keys simply bypass the cache.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

_CACHE_CAPACITY = 512


class BlockRenderCache:
    """LRU of ``(block, width) -> rendered ANSI`` for immutable blocks."""

    def __init__(self, *, capacity: int = _CACHE_CAPACITY) -> None:
        self._capacity = max(1, int(capacity))
        self._entries: OrderedDict[tuple[object, int], str] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def capacity(self) -> int:
        return self._capacity

    def get(self, block: object, width: int) -> str | None:
        """Return the cached render for one block at one width, if present."""
        try:
            text = self._entries[(block, int(width))]
        except (KeyError, TypeError):
            return None
        self._entries.move_to_end((block, int(width)))
        return text

    def put(self, block: object, width: int, text: str) -> None:
        """Retain one rendered block, evicting the least recently used entry."""
        try:
            self._entries[(block, int(width))] = str(text)
            self._entries.move_to_end((block, int(width)))
        except TypeError:
            return
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def render(
        self,
        block: object,
        width: int,
        render: Callable[[object, int], str],
    ) -> str:
        """Render through the cache, calling ``render`` only on a miss."""
        cached = self.get(block, width)
        if cached is not None:
            return cached
        text = str(render(block, int(width)))
        self.put(block, width, text)
        return text

    def clear(self) -> None:
        self._entries.clear()


__all__ = ["BlockRenderCache"]
