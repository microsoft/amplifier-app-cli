"""Provider-owned catalog prices remain estimates, including whole-request tiers."""

from copy import deepcopy
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console
from amplifier_core import ModelInfo

from amplifier_app_cli.provider_diagnostics import format_model_line, format_model_pricing
from amplifier_app_cli.provider_loader import list_models_for_instance


def _rates(input_rate="0.10", output_rate="0.50"):
    return dict(
        input=input_rate, output=output_rate, cache_read="0.01",
        cache_write_5m="0.125", cache_write_1h="0.20",
    )


def _details(kind="fixed"):
    details = dict(
        version=1, kind=kind, currency="USD", unit="per_million_tokens",
        basis="standard_global", modifiers={"us_inference": "1.1"},
    )
    if kind == "fixed":
        details["rates"] = _rates("8", "40")
    else:
        details.update(
            threshold_tokens=100000,
            threshold_basis="input_tokens+cache_read_input_tokens+cache_creation_input_tokens",
            tiers=[
                {"up_to": 100000, "rates": _rates()},
                {"above": 100000, "rates": _rates("0.50", "2.50")},
            ],
        )
    return details


def _model(details=None, pricing=None):
    return SimpleNamespace(
        id="provider-model", display_name="Provider model", context_window=200000,
        max_output_tokens=4096, capabilities=[], pricing_details=details, pricing=pricing,
    )


def test_fixed_price_and_cache_ttls_use_provider_strings():
    model = _model(_details())
    before = deepcopy(model.__dict__)
    rendered = format_model_line(model)
    assert "Price estimate: USD/M tokens (standard global)" in rendered
    assert "input 8, output 40" in rendered
    assert "cache read 0.01, cache write 5m 0.125, cache write 1h 0.20" in rendered
    assert "US inference x1.1" in rendered
    assert model.__dict__ == before


def test_tiered_price_never_looks_like_a_universal_low_rate():
    rendered = format_model_pricing(_model(_details("tiered")))
    assert "<= 100,000: input 0.10, output 0.50" in rendered
    assert "> 100,000: input 0.50, output 2.50" in rendered
    assert "fresh input + cache reads + cache writes, counted once" in rendered
    assert "whole request, including output" in rendered


@pytest.mark.parametrize("change", [
    {"version": 2}, {"version": True}, {"unit": "per_token"},
    {"kind": "future"}, {"rates": {}}, {"rates": _rates("NaN")},
    {"rates": _rates("-1")}, {"currency": "EUR"}, {"modifiers": None},
    {"modifiers": {"future_discount": "bad"}},
])
def test_unknown_extension_does_not_fall_back_to_flat_price(change):
    details = _details()
    details.update(change)
    model = _model(details, SimpleNamespace(input_per_million=0, output_per_million=0, currency="USD"))
    assert format_model_pricing(model) == "Unknown"


def test_missing_pricing_and_known_zero_are_distinct():
    assert format_model_pricing(_model()) == "Unknown"
    zero = SimpleNamespace(input_per_million=0, output_per_million=0, currency="USD")
    assert format_model_pricing(_model(pricing=zero)) == "USD/M tokens: input 0, output 0"


@pytest.mark.parametrize("boundary", [True, 1.0])
def test_tier_boundaries_require_exact_integers(boundary):
    details = _details("tiered")
    details["threshold_tokens"] = 1
    details["tiers"][0]["up_to"] = boundary
    details["tiers"][1]["above"] = 1
    assert format_model_pricing(_model(details)) == "Unknown"


def test_loader_preserves_extension_object_identity():
    model = _model(_details("tiered"))
    provider = SimpleNamespace(list_models=lambda: [model])
    assert list_models_for_instance(provider)[0] is model


@pytest.mark.asyncio
async def test_core_compatible_subclass_extension_survives_async_loader():
    class ExtendedModelInfo(ModelInfo):
        pricing_details: dict | None = None

    model = ExtendedModelInfo(
        id="extended", display_name="Extended", context_window=200000,
        max_output_tokens=4096, pricing_details=_details("tiered"),
    )

    async def list_models():
        return [model]

    # The loader owns asyncio.run(), so exercise the shared mounted invocation here.
    from amplifier_app_cli.provider_diagnostics import invoke_list_models

    loaded = await invoke_list_models(SimpleNamespace(list_models=list_models))
    assert loaded[0] is model
    assert model.model_dump()["pricing_details"] == _details("tiered")
    assert model.defaults == {}
    assert "<= 100,000" in format_model_pricing(loaded[0])


def test_click_catalog_uses_the_same_price_formatter():
    from amplifier_app_cli.commands.provider import provider

    model = _model(_details("tiered"))
    settings = MagicMock()
    settings.get_provider_overrides.return_value = [
        {"id": "fixture", "module": "provider-anthropic", "config": {}},
    ]
    output = StringIO()
    with (
        patch("amplifier_app_cli.commands.provider._get_settings", return_value=settings),
        patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
        patch("amplifier_app_cli.commands.provider.create_config_manager"),
        patch("amplifier_app_cli.commands.provider.get_provider_models", return_value=[model]),
        patch("amplifier_app_cli.commands.provider.console", Console(file=output, width=240)),
    ):
        result = CliRunner().invoke(provider, ["models", "fixture"])
    assert result.exit_code == 0, result.exception
    rendered = output.getvalue()
    assert "Price estimates" in rendered
    assert "<= 100,000" in rendered
    assert "> 100,000" in rendered