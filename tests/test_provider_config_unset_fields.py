"""Tests for the wizard's "no way to leave a field unset" defect family.

Root cause: `_prompt_for_field`'s choice branch had no representation for
"unset" -- `effective_value = existing_value or default` then
`default_choice = "1"` hard-coded -- so a ConfigField with `default=None` +
`choices=[...]` silently collapsed to `choices[0]` and ALWAYS wrote a value.
This force-wrote `prompt_cache_retention=in_memory` for provider-openai even
though the field's own source comment says `default=None` means "leave
unset"; gpt-5.6 models reject that value at runtime with a per-session
warning. The boolean branch had a related latent bug: `default and
default.lower()` with `default=None` evaluates to `None`, which
`Confirm.ask(default=None)` still accepts, silently writing the literal
string "none" on Enter.

Covers:
    (a)/(b)/(c) -- choice field default=None/real-default/existing_value
    (d)         -- boolean field default=None never writes "none"
    (e)         -- _should_show_field's full show_when predicate matrix
    (f)         -- requires_model Phase-1/Phase-3 split
    (g)         -- exact key-set assertion for a full configure_provider() run
"""

from unittest.mock import MagicMock, patch

import amplifier_app_cli.provider_config_utils as pcu

# ============================================================
# (a)(b)(c): choice field default=None / real default / existing_value
# ============================================================


class TestPromptForFieldChoiceUnset:
    """Choice fields with default=None and required=False must offer, and
    default to, an explicit "leave unset" option instead of force-writing
    choices[0] -- the root cause of the in_memory prompt_cache_retention
    defect (provider-openai's real-world ConfigField)."""

    @staticmethod
    def _choice_field(**overrides):
        field = {
            "id": "prompt_cache_retention",
            "display_name": "Prompt cache retention",
            "field_type": "choice",
            "prompt": "Cache retention window",
            "choices": ["in_memory", "24h"],
            "required": False,
            "default": None,
        }
        field.update(overrides)
        return field

    def test_default_none_not_required_omits_key_on_enter(self):
        """REGRESSION (in_memory defect): default=None + required=False --
        pressing Enter (accepting the wizard's own default selection) must
        OMIT the key entirely, not force-write choices[0] ('in_memory')."""
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch("amplifier_app_cli.provider_config_utils.Prompt.ask") as mock_ask,
        ):
            # Simulate the user accepting whatever default was offered.
            mock_ask.side_effect = lambda *a, **kw: kw["default"]
            field_id, value = pcu._prompt_for_field(
                self._choice_field(), MagicMock(), {}, existing_config=None
            )

        assert field_id == "prompt_cache_retention"
        assert value is None, (
            f"Expected None (key omitted) for default=None/required=False "
            f"choice field on Enter, got {value!r}"
        )
        # The default selection offered to the user must be the unset option.
        assert mock_ask.call_args.kwargs["default"] == "1"

    def test_default_none_not_required_offers_unset_as_first_choice(self):
        """The rendered choice list must prepend an explicit unset option,
        and the real choices must still be explicitly selectable."""
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="2",  # user explicitly picks the first real choice
            ) as mock_ask,
        ):
            _, value = pcu._prompt_for_field(
                self._choice_field(), MagicMock(), {}, existing_config=None
            )

        assert value == "in_memory"
        assert mock_ask.call_args.kwargs["choices"] == ["1", "2", "3"]

    def test_default_none_required_true_keeps_old_behavior(self):
        """A choice field that IS required, even with default=None, keeps the
        original hard-coded choices[0]-default behavior (no unset option)."""
        field = self._choice_field(required=True)
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch("amplifier_app_cli.provider_config_utils.Prompt.ask") as mock_ask,
        ):
            mock_ask.side_effect = lambda *a, **kw: kw["default"]
            _, value = pcu._prompt_for_field(
                field, MagicMock(), {}, existing_config=None
            )

        assert value == "in_memory"  # choices[0], unchanged behavior
        assert mock_ask.call_args.kwargs["choices"] == ["1", "2"]  # no unset option
        assert mock_ask.call_args.kwargs["default"] == "1"

    def test_real_default_unchanged_behavior(self):
        """(b) A choice field with a real (non-None) default behaves exactly
        as before -- no unset option, default selection is the declared
        default's position."""
        field = {
            "id": "reasoning_effort",
            "display_name": "Reasoning effort",
            "field_type": "choice",
            "prompt": "Reasoning effort",
            "choices": ["none", "low", "medium", "high"],
            "required": False,
            "default": "none",
        }
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch("amplifier_app_cli.provider_config_utils.Prompt.ask") as mock_ask,
        ):
            mock_ask.side_effect = lambda *a, **kw: kw["default"]
            _, value = pcu._prompt_for_field(
                field, MagicMock(), {}, existing_config=None
            )

        assert value == "none"
        assert mock_ask.call_args.kwargs["choices"] == ["1", "2", "3", "4"]
        assert mock_ask.call_args.kwargs["default"] == "1"

    def test_existing_value_preserved_as_default_selection(self):
        """(c) When existing_value is set (re-configuring), it takes priority
        over the unset option as the default selection -- but the unset
        option remains present and the key can still be cleared."""
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch("amplifier_app_cli.provider_config_utils.Prompt.ask") as mock_ask,
        ):
            mock_ask.side_effect = lambda *a, **kw: kw["default"]
            _, value = pcu._prompt_for_field(
                self._choice_field(),
                MagicMock(),
                {},
                existing_config={"prompt_cache_retention": "24h"},
            )

        # "24h" is choices[1] -> position 2, offset by 1 for the prepended
        # unset option -> position 3.
        assert mock_ask.call_args.kwargs["default"] == "3"
        assert value == "24h", "Enter should keep the existing value"

    def test_no_choices_defined_falls_through_to_text(self):
        """Fields with field_type='choice' but an empty choices list must
        still fall through to the text-field path (pre-existing behavior,
        unaffected by this fix)."""
        field = self._choice_field(choices=[])
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="",
            ),
        ):
            _, value = pcu._prompt_for_field(
                field, MagicMock(), {}, existing_config=None
            )

        assert value is None


# ============================================================
# (d): boolean field default=None
# ============================================================


class TestPromptForFieldBooleanUnset:
    """Boolean fields with default=None and no existing value must omit the
    key on Enter, and must NEVER write the literal string 'none'."""

    @staticmethod
    def _bool_field(**overrides):
        field = {
            "id": "enable_reasoning_context",
            "display_name": "Enable reasoning context",
            "field_type": "boolean",
            "prompt": "Enable reasoning context passthrough?",
            "required": False,
            "default": None,
        }
        field.update(overrides)
        return field

    def test_default_none_omits_key_on_enter(self):
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch("amplifier_app_cli.provider_config_utils.Confirm.ask") as mock_ask,
        ):
            mock_ask.side_effect = lambda *a, **kw: kw["default"]
            _, value = pcu._prompt_for_field(
                self._bool_field(), MagicMock(), {}, existing_config=None
            )

        assert value is None, f"Expected None (key omitted), got {value!r}"
        assert value != "none", "Must never write the literal string 'none'"
        assert mock_ask.call_args.kwargs["default"] is None

    def test_default_none_explicit_true_still_works(self):
        """If the user explicitly answers instead of pressing Enter, the
        boolean value is still recorded normally."""
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask",
                return_value=True,
            ),
        ):
            _, value = pcu._prompt_for_field(
                self._bool_field(), MagicMock(), {}, existing_config=None
            )

        assert value == "true"

    def test_real_default_unchanged_behavior(self):
        """A boolean field with a real string default keeps prior behavior
        (no 'leave unset' framing when a real default is declared)."""
        field = self._bool_field(default="false")
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch("amplifier_app_cli.provider_config_utils.Confirm.ask") as mock_ask,
        ):
            mock_ask.side_effect = lambda *a, **kw: kw["default"]
            _, value = pcu._prompt_for_field(
                field, MagicMock(), {}, existing_config=None
            )

        assert value == "false"
        assert mock_ask.call_args.kwargs["default"] is False

    def test_existing_value_unaffected_by_default_none(self):
        """An existing (previously-saved) boolean value still drives the
        default even when the field's own declared default is None."""
        with (
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask",
                return_value=True,
            ) as mock_ask,
        ):
            _, value = pcu._prompt_for_field(
                self._bool_field(),
                MagicMock(),
                {},
                existing_config={"enable_reasoning_context": "true"},
            )

        assert value == "true"
        assert mock_ask.call_args.kwargs["default"] is True


# ============================================================
# (e): _should_show_field predicate matrix
# ============================================================


class TestShouldShowFieldPredicates:
    """Full coverage of _should_show_field's show_when pattern language."""

    def test_no_show_when_always_shows(self):
        assert pcu._should_show_field({}, {}) is True

    def test_literal_exact_match_case_insensitive(self):
        field = {"show_when": {"default_model": "GPT-5"}}
        assert pcu._should_show_field(field, {"default_model": "gpt-5"}) is True
        assert pcu._should_show_field(field, {"default_model": "gpt-6"}) is False

    def test_contains_pattern(self):
        field = {"show_when": {"default_model": "contains:sonnet"}}
        assert (
            pcu._should_show_field(field, {"default_model": "claude-sonnet-4-5"})
            is True
        )
        assert (
            pcu._should_show_field(field, {"default_model": "claude-opus-4-5"}) is False
        )

    def test_not_contains_pattern(self):
        field = {"show_when": {"default_model": "not_contains:sonnet"}}
        assert (
            pcu._should_show_field(field, {"default_model": "claude-opus-4-5"}) is True
        )
        assert (
            pcu._should_show_field(field, {"default_model": "claude-sonnet-4-5"})
            is False
        )

    def test_startswith_pattern(self):
        field = {"show_when": {"default_model": "startswith:gpt-5"}}
        assert pcu._should_show_field(field, {"default_model": "gpt-5.6-mini"}) is True
        assert pcu._should_show_field(field, {"default_model": "gpt-4o"}) is False

    def test_not_startswith_pattern(self):
        field = {"show_when": {"default_model": "not_startswith:gpt-5"}}
        assert pcu._should_show_field(field, {"default_model": "gpt-4o"}) is True
        assert pcu._should_show_field(field, {"default_model": "gpt-5.6-mini"}) is False

    def test_missing_key_treated_as_empty_string(self):
        field = {"show_when": {"default_model": "contains:sonnet"}}
        assert pcu._should_show_field(field, {}) is False

    def test_multiple_conditions_all_must_match(self):
        field = {
            "show_when": {
                "default_model": "contains:gpt",
                "base_url": "not_contains:azure",
            }
        }
        collected = {"default_model": "gpt-5.6", "base_url": "https://api.openai.com"}
        assert pcu._should_show_field(field, collected) is True

        collected_fails = {
            "default_model": "gpt-5.6",
            "base_url": "https://azure.example.com",
        }
        assert pcu._should_show_field(field, collected_fails) is False


# ============================================================
# (f): requires_model Phase-1/Phase-3 split
# ============================================================


def _make_mock_model(model_id, display_name=None, capabilities=None):
    model = MagicMock()
    model.id = model_id
    model.display_name = display_name or model_id
    model.capabilities = capabilities or []
    return model


class TestRequiresModelPhaseSplit:
    """A config_field with requires_model=True is only ever prompted in
    Phase 3, after model selection (Phase 2) -- so its show_when can
    reference default_model."""

    @staticmethod
    def _provider_info():
        return {
            "display_name": "Test Provider",
            "config_fields": [
                {
                    "id": "api_key",
                    "display_name": "API Key",
                    "field_type": "secret",
                    "prompt": "API key",
                    "env_var": "TESTPROV_API_KEY",
                    "required": True,
                },
                {
                    "id": "reasoning_effort",
                    "display_name": "Reasoning effort",
                    "field_type": "choice",
                    "prompt": "Reasoning effort",
                    "choices": ["none", "low", "medium"],
                    "required": False,
                    "default": "none",
                    "requires_model": True,
                    "show_when": {"default_model": "contains:gpt"},
                },
            ],
        }

    def test_requires_model_field_evaluated_after_model_selected(self, monkeypatch):
        """Proof of the Phase-1/Phase-3 split: reasoning_effort's show_when
        must be evaluated with default_model ALREADY present in
        collected_config. If requires_model fields ran in Phase 1 (before
        model selection), default_model would not yet exist."""
        monkeypatch.setenv("TESTPROV_API_KEY", "sk-existing")
        mock_model = _make_mock_model("gpt-5.6-mini")

        seen_at_reasoning_check: dict = {}
        original_should_show = pcu._should_show_field

        def _spy_should_show(field, collected_config):
            if field.get("id") == "reasoning_effort":
                seen_at_reasoning_check.update(collected_config)
            return original_should_show(field, collected_config)

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=self._provider_info(),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                return_value=[mock_model],
            ),
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                side_effect=lambda *a, **kw: kw.get(
                    "default", (kw.get("choices") or [""])[0]
                ),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils._should_show_field",
                side_effect=_spy_should_show,
            ),
        ):
            result = pcu.configure_provider("test-provider", MagicMock())

        assert result is not None
        assert "default_model" in seen_at_reasoning_check, (
            "reasoning_effort's show_when was evaluated before default_model "
            "was set -- requires_model fields must run in Phase 3, after "
            "model selection"
        )
        assert seen_at_reasoning_check["default_model"] == "gpt-5.6-mini"
        assert result["default_model"] == "gpt-5.6-mini"
        assert result["reasoning_effort"] == "none"

    def test_requires_model_field_hidden_when_show_when_fails(self, monkeypatch):
        """When the selected model doesn't match show_when, the
        requires_model field is skipped entirely (absent from the result)."""
        monkeypatch.setenv("TESTPROV_API_KEY", "sk-existing")
        mock_model = _make_mock_model("claude-opus-4-5")

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=self._provider_info(),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                return_value=[mock_model],
            ),
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                side_effect=lambda *a, **kw: kw.get(
                    "default", (kw.get("choices") or [""])[0]
                ),
            ),
        ):
            result = pcu.configure_provider("test-provider", MagicMock())

        assert result is not None
        assert result["default_model"] == "claude-opus-4-5"
        assert "reasoning_effort" not in result


# ============================================================
# (g): Full exact-key-set assertion for configure_provider()
# ============================================================


class TestConfigureProviderExactKeySet:
    """End-to-end: pressing Enter through an entire mocked provider schema
    must write exactly the keys that have a real value or a declared
    default -- unset (default=None) choice/boolean fields must be entirely
    absent from the result, matching the text-field "may be absent"
    contract."""

    @staticmethod
    def _provider_info():
        return {
            "display_name": "Test Provider",
            "config_fields": [
                {
                    "id": "api_key",
                    "display_name": "API Key",
                    "field_type": "secret",
                    "prompt": "API key",
                    "env_var": "TESTPROV_API_KEY",
                    "required": True,
                },
                {
                    "id": "base_url",
                    "display_name": "API Base URL",
                    "field_type": "text",
                    "prompt": "API base URL",
                    "required": False,
                    "default": "https://api.example.com/v1",
                },
                {
                    "id": "prompt_cache_retention",
                    "display_name": "Prompt cache retention",
                    "field_type": "choice",
                    "prompt": "Cache retention window",
                    "choices": ["in_memory", "24h"],
                    "required": False,
                    "default": None,
                },
                {
                    "id": "enable_reasoning_context",
                    "display_name": "Enable reasoning context",
                    "field_type": "boolean",
                    "prompt": "Enable reasoning context passthrough?",
                    "required": False,
                    "default": None,
                },
                {
                    "id": "reasoning_effort",
                    "display_name": "Reasoning effort",
                    "field_type": "choice",
                    "prompt": "Reasoning effort",
                    "choices": ["none", "low", "medium"],
                    "required": False,
                    "default": "none",
                    "requires_model": True,
                    "show_when": {"default_model": "contains:gpt"},
                },
            ],
        }

    def test_full_key_set_omits_unset_fields(self, monkeypatch):
        monkeypatch.setenv("TESTPROV_API_KEY", "sk-existing")
        mock_model = _make_mock_model("gpt-5.6-mini")

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=self._provider_info(),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                return_value=[mock_model],
            ),
            patch("amplifier_app_cli.provider_config_utils.console", MagicMock()),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                side_effect=lambda *a, **kw: kw.get(
                    "default", (kw.get("choices") or [""])[0]
                ),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Confirm.ask",
                side_effect=lambda *a, **kw: kw.get("default"),
            ),
        ):
            result = pcu.configure_provider("test-provider", MagicMock())

        assert result is not None
        assert set(result.keys()) == {
            "api_key",
            "base_url",
            "default_model",
            "reasoning_effort",
        }, f"Unexpected key set: {sorted(result.keys())}"
        assert result["api_key"] == "${TESTPROV_API_KEY}"
        assert result["base_url"] == "https://api.example.com/v1"
        assert result["default_model"] == "gpt-5.6-mini"
        assert result["reasoning_effort"] == "none"
        # The two "leave unset" fields must not appear at all.
        assert "prompt_cache_retention" not in result
        assert "enable_reasoning_context" not in result


# ============================================================
# Fix 3: dead cli_overrides machinery removed
# ============================================================


class TestConfigureProviderDeadParamsRemoved:
    """configure_provider() no longer accepts the dead model=/endpoint=/
    deployment=/use_azure_cli= CLI-override parameters (zero callers,
    survey-verified). Passing them is now a TypeError, not a silent no-op."""

    def test_dead_kwargs_rejected(self):
        import inspect

        sig = inspect.signature(pcu.configure_provider)
        for dead_param in ("model", "endpoint", "deployment", "use_azure_cli"):
            assert dead_param not in sig.parameters, (
                f"configure_provider() should no longer accept '{dead_param}'"
            )
