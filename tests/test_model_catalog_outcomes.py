"""An empty recommended catalog is not a failed lookup or inference proof."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import importlib

import pytest
from click.testing import CliRunner

from amplifier_app_cli import provider_loader as loader
from amplifier_app_cli.commands import routing
from amplifier_app_cli.provider_config_utils import _prompt_model_selection
from amplifier_app_cli.provider_diagnostics import (
    ModelCatalogUnavailable,
    test_provider_connectivity as check_catalog,
)

provider_command = importlib.import_module("amplifier_app_cli.commands.provider")


@pytest.mark.parametrize("models", [[], ["Model-10"]])
def test_strict_loader_keeps_noarg_provider_defaults(monkeypatch, models):
    provider = SimpleNamespace(list_models=Mock(return_value=models))
    monkeypatch.setattr(loader, "load_provider_class", lambda _: object)
    monkeypatch.setattr(loader, "_try_instantiate_provider", lambda *_: provider)
    assert loader.get_provider_models("fixture", require_catalog=True) == models
    provider.list_models.assert_called_once_with()


@pytest.mark.parametrize("missing", ["implementation", "instance", "method"])
def test_strict_loader_distinguishes_unavailable_but_legacy_still_returns_empty(monkeypatch, missing):
    monkeypatch.setattr(loader, "load_provider_class", lambda _: None if missing == "implementation" else object)
    monkeypatch.setattr(loader, "_try_instantiate_provider", lambda *_: None if missing == "instance" else object())
    with pytest.raises(ModelCatalogUnavailable):
        loader.get_provider_models("fixture", require_catalog=True)
    assert loader.get_provider_models("fixture") == []


def test_routing_empty_and_failure_are_distinct(monkeypatch):
    monkeypatch.setattr(loader, "get_provider_models", Mock(return_value=[]))
    assert routing._list_models_for_provider("fixture") == []
    loader.get_provider_models.assert_called_once_with(
        "provider-fixture", collected_config=None, require_catalog=True
    )
    monkeypatch.setattr(loader, "get_provider_models", Mock(side_effect=RuntimeError("offline")))
    assert routing._list_models_for_provider("fixture") is None


def test_empty_catalog_no_match_unavailable_unverified_and_exact_passthrough(monkeypatch):
    settings = Mock()
    settings.get_provider_overrides.return_value = [{"module": "provider-fixture"}]
    listing = Mock(return_value=[])
    monkeypatch.setattr(routing, "_list_models_bounded", listing)
    live = routing._LiveModels(settings)
    assert live.resolve("fixture", "model-*") == ("no-match", None)
    assert live.resolve("fixture", "another-*") == ("no-match", None)
    assert live.resolve("fixture", "Exact-Unlisted") == ("active", "Exact-Unlisted")
    assert live.unverified == set()
    listing.assert_called_once()
    listing.return_value = None
    live = routing._LiveModels(settings)
    assert live.resolve("fixture", "model-*") == ("unverified", None)
    assert live.unverified == {"fixture"}


@pytest.mark.asyncio
async def test_diagnostic_empty_catalog_success_is_not_inference():
    provider = SimpleNamespace(list_models=AsyncMock(return_value=[]))
    result = await check_catalog("fixture", provider)
    assert result.ok
    assert "0 models listed" in result.detail
    assert "inference not tested" in result.detail
    provider.list_models.assert_awaited_once_with()
    missing = await check_catalog("fixture", object())
    assert not missing.ok
    assert "ModelCatalogUnavailable" in missing.detail


@pytest.mark.parametrize("answer,expected", [("", "Current-Exact"), ("Manual-Exact", "Manual-Exact")])
def test_empty_recommendations_keep_current_or_manual_entry(monkeypatch, answer, expected):
    from amplifier_app_cli import provider_config_utils

    monkeypatch.setattr(provider_config_utils.Prompt, "ask", lambda *a, **k: answer or k.get("default", ""))
    assert _prompt_model_selection("provider-fixture", default_model="Current-Exact", models=[]) == expected


def test_provider_test_describes_catalog_not_inference(monkeypatch):
    settings = Mock()
    settings.get_provider_overrides.return_value = [{"module": "provider-fixture", "config": {}}]
    monkeypatch.setattr(provider_command, "_ensure_providers_ready", lambda: None)
    monkeypatch.setattr(provider_command, "_get_settings", lambda: settings)
    listing = Mock(return_value=[])
    monkeypatch.setattr(provider_command, "get_provider_models", listing)
    result = CliRunner().invoke(provider_command.provider, ["test", "fixture"])
    assert result.exit_code == 0, result.output
    assert "Catalog accessible" in result.output
    assert "inference not tested" in result.output
    listing.assert_called_once_with("provider-fixture", collected_config={}, require_catalog=True)