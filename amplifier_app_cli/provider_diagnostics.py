"""Shared, live-instance provider diagnostics primitives.

These are the reusable mechanics behind "does this provider answer" and
"what models does it offer" -- the SAME questions ``amplifier provider
test``/``amplifier provider models`` answer for disk-config providers (via
``provider_loader.get_provider_models``, which instantiates a throwaway
provider from settings.yaml), and that the in-session ``/provider
test``/``/provider models`` slash commands answer for this session's
already-mounted, LIVE provider instances.

The two surfaces intentionally source providers differently:

- CLI (``amplifier provider ...``): reads settings.yaml, instantiates a
  disposable provider object for the single call, and is responsible for
  closing it afterward.
- In-session (``/provider ...``): reads ``coordinator.get("providers")`` --
  the actual mounted objects still answering this conversation -- and must
  NEVER close them; they are not disposable, they keep running after the
  diagnostic completes.

What must not diverge between the two is the actual definition of
"catalog accessible" (list_models() succeeds) and how a possibly-async
``list_models()`` is invoked. That mechanic lives here, once, and both
``provider_loader.get_provider_models`` and the in-session slash commands
call through it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from amplifier_core import ModelInfo  # pyright: ignore[reportAttributeAccessIssue]


class ModelCatalogUnavailable(RuntimeError):
    """No provider catalog could be queried; distinct from a successful empty list."""


async def invoke_list_models(provider: Any) -> list["ModelInfo"]:
    """Call ``list_models()`` on an already-instantiated provider.

    Awaits it if async, calls it directly if sync. Returns ``[]`` if the
    provider has no ``list_models`` at all. Does NO cleanup and NO error
    handling of its own -- exceptions from ``list_models()`` propagate
    unchanged, and callers decide whether/how to close the instance
    afterward (a throwaway CLI instance should be closed; a session-mounted
    instance must not be).
    """
    list_models_fn = getattr(provider, "list_models", None)
    if list_models_fn is None:
        return []
    if asyncio.iscoroutinefunction(list_models_fn):
        return await list_models_fn()
    return list_models_fn()


@dataclass
class ProviderTestResult:
    """Outcome of a catalog check, not an inference or entitlement test."""

    name: str
    ok: bool
    elapsed_s: float
    detail: str


DEFAULT_TIMEOUT_S = 15.0


async def test_provider_connectivity(
    name: str, provider: Any, timeout_s: float = DEFAULT_TIMEOUT_S
) -> ProviderTestResult:
    """Catalog accessibility check: call list_models(), time it; no inference.

    Never raises -- failures (including a timeout) are captured in the
    returned result, not thrown, so a caller can run many of these
    concurrently (e.g. via ``asyncio.gather``) without one slow or failing
    provider aborting the batch or hanging an interactive session forever.

    This is the same definition of "catalog accessible" that ``amplifier
    provider test`` uses (list_models() succeeds), so the CLI and the
    in-session diagnostic cannot silently disagree on what "ok" means.
    """
    start = time.monotonic()
    try:
        if not callable(getattr(provider, "list_models", None)):
            raise ModelCatalogUnavailable("Provider has no model catalog method")
        models = await asyncio.wait_for(invoke_list_models(provider), timeout_s)
    except TimeoutError:
        elapsed = time.monotonic() - start
        detail = f"timed out after {timeout_s:.0f}s"
        return ProviderTestResult(name=name, ok=False, elapsed_s=elapsed, detail=detail)
    except Exception as e:
        elapsed = time.monotonic() - start
        detail = f"{type(e).__name__}: {e}"
        return ProviderTestResult(name=name, ok=False, elapsed_s=elapsed, detail=detail)

    elapsed = time.monotonic() - start
    count = len(models)
    detail = f"catalog accessible: {count} model{'s' if count != 1 else ''} listed; inference not tested"
    return ProviderTestResult(name=name, ok=True, elapsed_s=elapsed, detail=detail)


def _rate(value: Any) -> str:
    """Validate display rates without inventing a missing or invalid price."""
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ValueError("Missing rate")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid rate") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid rate")
    return format(amount, "f")


def _format_rates(rates: Any) -> str:
    if not isinstance(rates, dict):
        raise ValueError("Missing rates")
    labels = (
        ("input", "input"),
        ("output", "output"),
        ("cache_read", "cache read"),
        ("cache_write_5m", "cache write 5m"),
        ("cache_write_1h", "cache write 1h"),
    )
    return ", ".join(f"{label} {_rate(rates[key])}" for key, label in labels)


def format_model_pricing(model: "ModelInfo") -> str:
    """Show provider catalog estimates, never calculate request costs here."""
    details = getattr(model, "pricing_details", None)
    if details is not None:
        try:
            if (
                not isinstance(details, dict)
                or type(details.get("version")) is not int
                or details["version"] != 1
                or details.get("currency") != "USD"
                or details.get("unit") != "per_million_tokens"
                or details.get("basis") != "standard_global"
            ):
                return "Unknown"
            if details.get("kind") == "fixed":
                summary = _format_rates(details["rates"])
            elif details.get("kind") == "tiered":
                threshold = details["threshold_tokens"]
                tiers = details["tiers"]
                if (
                    type(threshold) is not int
                    or threshold <= 0
                    or details.get("threshold_basis")
                    != "input_tokens+cache_read_input_tokens+cache_creation_input_tokens"
                    or not isinstance(tiers, list)
                    or len(tiers) != 2
                    or type(tiers[0].get("up_to")) is not int
                    or type(tiers[1].get("above")) is not int
                    or tiers[0].get("up_to") != threshold
                    or tiers[1].get("above") != threshold
                ):
                    return "Unknown"
                summary = (
                    f"<= {threshold:,}: {_format_rates(tiers[0]['rates'])}; "
                    f"> {threshold:,}: {_format_rates(tiers[1]['rates'])}. "
                    "Tier uses fresh input + cache reads + cache writes, counted once; "
                    "selected rates apply to the whole request, including output"
                )
            else:
                return "Unknown"
            modifiers = details.get("modifiers", {})
            if not isinstance(modifiers, dict):
                return "Unknown"
            labels = {"us_inference": "US inference", "confirmed_fast": "confirmed fast"}
            if modifiers.keys() - labels.keys():
                return "Unknown"
            suffix = "; ".join(
                f"{labels[key]} x{_rate(value)}"
                for key, value in modifiers.items()
                if key in labels
            )
            return f"USD/M tokens (standard global): {summary}" + (
                f"; {suffix}" if suffix else ""
            )
        except (KeyError, TypeError, AttributeError, ValueError):
            return "Unknown"
    pricing = getattr(model, "pricing", None)
    if pricing is None:
        return "Unknown"
    try:
        currency = pricing.currency
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
            return "Unknown"
        summary = (
            f"input {_rate(pricing.input_per_million)}, "
            f"output {_rate(pricing.output_per_million)}"
        )
        for field, label in (
            ("cache_read_per_million", "cache read"),
            ("cache_write_per_million", "cache write"),
        ):
            value = getattr(pricing, field, None)
            if value is not None:
                summary += f", {label} {_rate(value)}"
        return f"{currency}/M tokens: {summary}"
    except (AttributeError, ValueError):
        return "Unknown"


def format_model_line(model: "ModelInfo") -> str:
    """Render one ``ModelInfo`` as a compact, single-line summary.

    Used by the in-session ``/provider models`` slash command, which
    returns a plain string (unlike the CLI's Rich ``Table``) -- kept here
    so the fields shown (id, context window, max output, capabilities)
    match what ``amplifier provider models`` already surfaces.
    """
    context = f"{model.context_window:,}" if model.context_window else "-"
    max_out = f"{model.max_output_tokens:,}" if model.max_output_tokens else "-"
    caps = ", ".join(model.capabilities) if model.capabilities else "-"
    return (
        f"{model.id:<28} context={context:<10} max_out={max_out:<8} caps={caps}"
        f"\n  Price estimate: {format_model_pricing(model)}"
    )
