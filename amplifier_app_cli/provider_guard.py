"""Resume-time provider-mismatch guard (issue #208, Option A).

Warns when a session is resumed under a different LLM provider than the one
that last wrote to it. Cross-provider resumes can brick a session because saved
history may carry provider-specific content blocks (reasoning / thinking blocks)
that the new provider's API rejects (see #206 / #207).

The last-writing provider is derived from the session's persisted events.jsonl:
the last ``llm:response`` event records ``data.provider`` and ``data.model``.
This mirrors the memory-safe, line-by-line read in
``cost_history.sum_prior_cost_usd`` (events lines can be very large).

Provider mismatch is the dangerous axis. Model-only changes within one provider
are safe and stay silent.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_LLM_RESPONSE_EVENT = "llm:response"


def last_writing_provider(events_path: Path) -> tuple[str | None, str | None]:
    """Return (provider, model) from the LAST ``llm:response`` event.

    Returns (None, None) when the file is missing/unreadable or has no
    ``llm:response`` events. Reads one line at a time with a cheap substring
    pre-filter so huge lines are never all held in memory. Never raises.
    """
    if not events_path.is_file():
        return (None, None)
    provider: str | None = None
    model: str | None = None
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if _LLM_RESPONSE_EVENT not in line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if (
                    not isinstance(event, dict)
                    or event.get("event") != _LLM_RESPONSE_EVENT
                ):
                    continue
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                p = data.get("provider")
                if isinstance(p, str) and p:
                    provider = p
                    m = data.get("model")
                    model = m if isinstance(m, str) and m else None
    except OSError:
        logger.debug("Provider guard: could not read %s", events_path, exc_info=True)
        return (None, None)
    return (provider, model)


def _first_provider(config_data: dict) -> dict | None:
    providers = config_data.get("providers")
    if isinstance(providers, list) and providers and isinstance(providers[0], dict):
        return providers[0]
    return None


def active_provider_aliases(config_data: dict) -> set[str]:
    """Lower-cased identity aliases the resuming session will write under.

    Union of {module, module without the ``provider-`` prefix, id, instance_id}
    so the events ``data.provider`` short-name matches whichever form the
    provider self-reports. Empty set when no providers are resolved.
    """
    first = _first_provider(config_data)
    if first is None:
        return set()
    aliases: set[str] = set()
    module = first.get("module")
    if isinstance(module, str) and module:
        aliases.add(module.lower())
        aliases.add(module.removeprefix("provider-").lower())
    for key in ("instance_id", "id"):
        val = first.get(key)
        if isinstance(val, str) and val:
            aliases.add(val.lower())
    return aliases


def active_provider_display(config_data: dict) -> str:
    first = _first_provider(config_data)
    module = (first or {}).get("module")
    if isinstance(module, str) and module:
        return module.removeprefix("provider-")
    return "the active provider"


def active_model(config_data: dict) -> str | None:
    first = _first_provider(config_data)
    cfg = (first or {}).get("config")
    if isinstance(cfg, dict):
        m = cfg.get("model") or cfg.get("default_model")
        return m if isinstance(m, str) and m else None
    return None


def _short(model: str | None) -> str:
    """Match the display convention in _display_session_history (strip vendor prefix)."""
    if not model:
        return "?"
    return model.split("/")[-1]


def check_resume_provider(
    session_id: str,
    config_data: dict,
    console,
    *,
    base_dir: Path,
    is_tty: bool | None = None,
) -> bool:
    """Warn on provider mismatch at resume. Return True to proceed, False to abort.

    Decision table:
      - no last-writing provider (no llm:response events) -> True (silent)
      - active provider unknown (no resolved providers)   -> True (silent)
      - last provider matches an active alias             -> True (silent; model-only
                                                             changes are safe)
      - mismatch + non-interactive                        -> warn to log, True (never
                                                             block automation)
      - mismatch + interactive TTY                        -> warn + confirm; return answer
    """
    last_provider, last_model = last_writing_provider(
        base_dir / session_id / "events.jsonl"
    )
    if not last_provider:
        return True
    aliases = active_provider_aliases(config_data)
    if not aliases or last_provider.lower() in aliases:
        return True

    active_name = active_provider_display(config_data)
    active_mdl = _short(active_model(config_data))
    last_mdl = _short(last_model)
    if is_tty is None:
        is_tty = sys.stdin.isatty()

    console.print(
        "[yellow]⚠ Provider mismatch on resume[/yellow]\n"
        f"  Last written by:  [bold]{last_provider}[/bold] ({last_mdl})\n"
        f"  Resuming with:    [bold]{active_name}[/bold] ({active_mdl})\n"
        "  [dim]Switching providers mid-session can fail if the saved history "
        "contains provider-specific content blocks (e.g. reasoning/thinking blocks).[/dim]"
    )
    if not is_tty:
        logger.warning(
            "Provider mismatch on resume (session %s): last=%s active=%s; "
            "proceeding (non-interactive).",
            session_id,
            last_provider,
            active_name,
        )
        return True
    answer = console.input("  Proceed anyway? [y/N]: ")
    return answer.strip().lower() in ("y", "yes")


__all__ = [
    "last_writing_provider",
    "active_provider_aliases",
    "active_provider_display",
    "active_model",
    "check_resume_provider",
]
