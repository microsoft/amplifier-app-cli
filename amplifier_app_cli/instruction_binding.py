"""Optional host binding for the next outer execution.

The streaming loop consumes this app-owned capability when it supports the
instruction v1 contract. Older loops simply ignore it.
"""

from __future__ import annotations

import uuid
from typing import Any


EXECUTION_INPUT_CAPABILITY = "execution.input.v1"


def bind_execution_input(coordinator: Any, *, origin: str) -> str | None:
    """Bind one fresh human or delegated input immediately before ``execute``."""
    register_capability = getattr(coordinator, "register_capability", None)
    if not callable(register_capability):
        return None

    input_id = str(uuid.uuid4())
    register_capability(
        EXECUTION_INPUT_CAPABILITY,
        {
            "version": 1,
            "input_id": input_id,
            "origin": origin,
        },
    )
    return input_id