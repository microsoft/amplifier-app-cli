"""Explicit creation provenance for native history presentation.

These declarations are not permissions and are never inferred from invocation
mode, TTY state, prompts, titles, or the process that launched this CLI.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

VISIBILITY_ENV = "AMPLIFIER_SESSION_VISIBILITY"
PURPOSE_ENV = "AMPLIFIER_SESSION_PURPOSE"


def creation_metadata(
    *, is_resume: bool, env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Capture a new root's declaration once; never relabel a resumed history.

    Unknown/missing declarations keep ordinary conversations visible. Purpose
    is optional, bounded, and recorded only for explicitly internal jobs.
    """
    if is_resume:
        return {}
    values = os.environ if env is None else env
    visibility = values.get(VISIBILITY_ENV)
    metadata = {
        "session_visibility": "internal" if visibility == "internal" else "chat"
    }
    purpose = values.get(PURPOSE_ENV, "")
    if visibility == "internal" and re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", purpose):
        metadata["session_purpose"] = purpose
    return metadata
