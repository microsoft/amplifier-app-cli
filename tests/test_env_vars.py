"""Pin the session expansion contract reused by lightweight provider loading."""

from copy import deepcopy

import pytest

from amplifier_app_cli.lib.env_vars import ENV_PATTERN, expand_env_vars


def test_runtime_retains_the_shared_expansion_exports():
    from amplifier_app_cli.runtime import config

    assert config.expand_env_vars is expand_env_vars
    assert config.ENV_PATTERN is ENV_PATTERN


@pytest.mark.parametrize(
    ("env_value", "value", "expected"),
    [
        (None, "${FIXTURE_VALUE}", ""),
        (None, "${FIXTURE_VALUE:default}", "default"),
        (None, "${FIXTURE_VALUE:}", ""),
        ("present", "${FIXTURE_VALUE:default}", "present"),
        ("", "${FIXTURE_VALUE}", ""),
        ("", "${FIXTURE_VALUE:default}", ""),
        # ':' supplies a default; shell-style ':-' includes the '-' literally.
        (None, "${FIXTURE_VALUE:-default}", "-default"),
        ("", "${FIXTURE_VALUE:-default}", ""),
        (
            None,
            "${FIXTURE_VALUE:https://example.invalid:8443/v1}",
            "https://example.invalid:8443/v1",
        ),
        ("present", "$FIXTURE_VALUE", "$FIXTURE_VALUE"),
        ("present", "${FIXTURE_VALUE", "${FIXTURE_VALUE"),
        ("present", "${}", "${}"),
        ("present", "$${FIXTURE_VALUE}", "$present"),
    ],
)
def test_expand_env_vars_placeholder_defaults_and_empty_values(
    monkeypatch, env_value, value, expected
):
    monkeypatch.delenv("FIXTURE_VALUE", raising=False)
    if env_value is not None:
        monkeypatch.setenv("FIXTURE_VALUE", env_value)
    config = {"value": value}
    original = deepcopy(config)
    assert expand_env_vars(config) == {"value": expected}
    assert config == original


def test_expand_env_vars_walks_dict_and_list_values_not_keys_or_tuples(monkeypatch):
    monkeypatch.setenv("FIXTURE_VALUE", "expanded")
    monkeypatch.setenv("FIXTURE_HOST", "example.invalid")
    monkeypatch.delenv("FIXTURE_UNKNOWN", raising=False)
    config = {
        "${FIXTURE_VALUE}": {
            "values": [
                "${FIXTURE_VALUE}",
                {"endpoint": "https://${FIXTURE_HOST}/${FIXTURE_VALUE}/v1"},
                ["${FIXTURE_UNKNOWN}", "${FIXTURE_VALUE}"],
            ]
        },
        "tuple": ("${FIXTURE_VALUE}", ["${FIXTURE_VALUE}"]),
        "bool": False,
        "int": 7,
        "float": 1.5,
        "none": None,
    }
    original = deepcopy(config)
    expanded = expand_env_vars(config)
    assert expanded == {
        "${FIXTURE_VALUE}": {
            "values": [
                "expanded",
                {"endpoint": "https://example.invalid/expanded/v1"},
                ["", "expanded"],
            ]
        },
        "tuple": ("${FIXTURE_VALUE}", ["${FIXTURE_VALUE}"]),
        "bool": False,
        "int": 7,
        "float": 1.5,
        "none": None,
    }
    assert expanded["tuple"] is config["tuple"]
    for key in ("bool", "int", "float", "none"):
        assert type(expanded[key]) is type(config[key])
    assert config == original


def test_expand_env_vars_substitutes_each_original_placeholder_only_once(monkeypatch):
    monkeypatch.setenv("FIXTURE_FIRST", "${FIXTURE_SECOND}")
    monkeypatch.setenv("FIXTURE_SECOND", "second-value")
    config = {
        "value": "prefix-${FIXTURE_FIRST}-${FIXTURE_SECOND}",
        "nested": ["${FIXTURE_FIRST}"],
    }
    original = deepcopy(config)
    assert expand_env_vars(config) == {
        "value": "prefix-${FIXTURE_SECOND}-second-value",
        "nested": ["${FIXTURE_SECOND}"],
    }
    assert config == original