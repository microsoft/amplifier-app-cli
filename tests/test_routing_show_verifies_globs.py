"""`routing show` must not call a candidate "active" unless its glob resolves.

"Configured" is a property of the PROVIDER; a candidate's model is a GLOB that
must ALSO match something that provider actually lists. `show` used to mark a
candidate active on provider presence alone, so it claimed models the session
could not serve. Measured 2026-09-07 in a DTU with only `gpt-5.6-terra`
configured on the openai provider:

    fast -> * openai / gpt-?.?-luna  <- active      # nothing would resolve this

The `_LiveModels` helper closes that: one memoized `list_models()` per matrix
provider name, alias-aware, and honest about what it could not fetch -- an
unlistable provider is UNVERIFIED (rendered exactly as before, named once in a
footer), never "no match".
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from amplifier_app_cli.commands import routing as r
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


def _settings(tmp_path: Path, providers: list[dict]) -> AppSettings:
    paths = SettingsPaths(
        global_settings=tmp_path / "g" / "settings.yaml",
        project_settings=tmp_path / "p" / "settings.yaml",
        local_settings=tmp_path / "l" / "settings.local.yaml",
    )
    paths.global_settings.parent.mkdir(parents=True, exist_ok=True)
    paths.global_settings.write_text(
        yaml.safe_dump({"config": {"providers": providers}}), encoding="utf-8"
    )
    return AppSettings(paths=paths)


OPENAI_ONLY = [{"module": "provider-openai", "source": "git+x"}]
CHATGPT_ONLY = [{"module": "provider-openai-chatgpt", "source": "git+x"}]
ALIASES = {"openai": ("openai-chatgpt",)}


def _listing(table: dict[str, list[str]]):
    """Patch the live model listing with a fixed provider-selector -> models table.

    Patched at ``_list_models_bounded`` -- the seam ``_LiveModels`` calls and
    the one the conftest autouse guard stubs -- so these tests override that
    guard rather than being defeated by it.
    """
    return patch.object(
        r, "_list_models_bounded", side_effect=lambda sel, _s: table.get(sel, [])
    )


# ---------------------------------------------------------------------------
# _LiveModels.resolve
# ---------------------------------------------------------------------------


class TestResolve:
    def test_glob_with_a_live_match_is_active_and_names_the_id(self, tmp_path):
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": ["gpt-5.6-terra", "gpt-5.5"]}):
            assert r._LiveModels(s).resolve("openai", "gpt-?.?-terra") == (
                "active",
                "gpt-5.6-terra",
            )

    def test_glob_matching_nothing_listed_is_no_match(self, tmp_path):
        """The DTU case: provider present, luna not among its models."""
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": ["gpt-5.6-terra"]}):
            assert r._LiveModels(s).resolve("openai", "gpt-?.?-luna") == (
                "no-match",
                None,
            )

    def test_exact_name_is_active_without_a_lookup(self, tmp_path):
        """The resolver sends exact names straight to the API; so does this."""
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({}) as listing:
            assert r._LiveModels(s).resolve("openai", "gpt-5.6-terra") == (
                "active",
                "gpt-5.6-terra",
            )
            listing.assert_not_called()

    def test_unlistable_provider_is_unverified_never_no_match(self, tmp_path):
        """Offline / no key / module absent -> [] -> UNVERIFIED, and named."""
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": []}):
            live = r._LiveModels(s)
            assert live.resolve("openai", "gpt-?.?-luna") == ("unverified", None)
            assert live.unverified == {"openai"}

    def test_unconfigured_provider_is_unverified_and_not_listed(self, tmp_path):
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({}) as listing:
            live = r._LiveModels(s)
            assert live.resolve("gemini", "gemini-[3-9]*-flash")[0] == "unverified"
            listing.assert_not_called()

    def test_picks_the_same_id_the_hook_would(self, tmp_path):
        """Newest version wins; a `-fast` sibling must NOT beat the clean id for a
        suffix-free glob -- the exact trap the shipped globs were shaped around."""
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": ["gpt-5.5", "gpt-5.6-terra", "gpt-5.6-terra-fast"]}):
            assert (
                r._LiveModels(s).resolve("openai", "gpt-?.?-terra")[1]
                == "gpt-5.6-terra"
            )


# ---------------------------------------------------------------------------
# memoization and aliasing
# ---------------------------------------------------------------------------


class TestListingBehaviour:
    def test_one_list_models_call_per_provider_across_roles(self, tmp_path):
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": ["gpt-5.6-terra", "gpt-5.6-luna"]}) as listing:
            live = r._LiveModels(s)
            for glob in (
                "gpt-?.?-terra",
                "gpt-?.?-luna",
                "gpt-?.?-terra",
                "gpt-?.?-sol",
            ):
                live.resolve("openai", glob)
            assert listing.call_count == 1

    def test_alias_lists_models_from_the_configured_backend(self, tmp_path):
        """`provider: openai` with ONLY openai-chatgpt configured lists chatgpt's
        models -- the same preference the resolver applies."""
        s = _settings(tmp_path, CHATGPT_ONLY)
        with (
            patch.object(r, "_provider_family_aliases", return_value=ALIASES),
            _listing(
                {"openai-chatgpt": ["gpt-5.6-terra", "gpt-5.6-terra-fast"]}
            ) as listing,
        ):
            assert r._LiveModels(s).resolve("openai", "gpt-?.?-terra") == (
                "active",
                "gpt-5.6-terra",
            )
            assert listing.call_args.args[0] == "openai-chatgpt"


# ---------------------------------------------------------------------------
# _resolve_role with live verification
# ---------------------------------------------------------------------------


ROLE = {
    "candidates": [
        {"provider": "openai", "model": "gpt-?.?-luna"},
        {"provider": "anthropic", "model": "claude-haiku-*"},
    ]
}


class TestResolveRole:
    def test_falls_through_a_no_match_candidate_like_the_resolver(self, tmp_path):
        s = _settings(
            tmp_path,
            OPENAI_ONLY + [{"module": "provider-anthropic", "source": "git+x"}],
        )
        with _listing({"openai": ["gpt-5.6-terra"], "anthropic": ["claude-haiku-4-5"]}):
            assert r._resolve_role(ROLE, {"openai", "anthropic"}, r._LiveModels(s)) == (
                "claude-haiku-*",
                "anthropic",
            )

    def test_without_live_is_provider_presence_only_as_before(self):
        """`routing list`'s compatibility counts stay offline-fast and unchanged."""
        assert r._resolve_role(ROLE, {"openai", "anthropic"}) == (
            "gpt-?.?-luna",
            "openai",
        )

    def test_all_configured_candidates_no_match_yields_none(self, tmp_path):
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": ["gpt-5.6-terra"]}):
            assert r._resolve_role(ROLE, {"openai"}, r._LiveModels(s)) == (None, None)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


MATRIX = {
    "name": "t",
    "roles": {
        "fast": {
            "description": "quick",
            "candidates": [
                {"provider": "openai", "model": "gpt-?.?-luna"},
                {"provider": "openai", "model": "gpt-?.?-terra"},
            ],
        }
    },
}


class TestRendering:
    def _capture(self):
        return patch.object(r.console, "print")

    def test_details_marks_no_match_and_promotes_the_next_candidate(self, tmp_path):
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": ["gpt-5.6-terra"]}), self._capture() as out:
            r._show_matrix_details(MATRIX, s, "t")
        text = "\n".join(str(c.args[0]) for c in out.call_args_list if c.args)
        assert "gpt-?.?-luna" in text and "no model matches this glob" in text
        assert "gpt-?.?-terra" in text and "\u2190 active (gpt-5.6-terra)" in text
        assert "Model lists not fetched" not in text

    def test_details_unverified_renders_as_before_plus_footer(self, tmp_path):
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": []}), self._capture() as out:
            r._show_matrix_details(MATRIX, s, "t")
        text = "\n".join(str(c.args[0]) for c in out.call_args_list if c.args)
        assert "\u2605 openai / gpt-?.?-luna" in text and "\u2190 active" in text
        assert "no model matches" not in text
        assert "Model lists not fetched for: openai" in text

    def test_resolution_table_shows_the_resolved_id(self, tmp_path):
        s = _settings(tmp_path, OPENAI_ONLY)
        with _listing({"openai": ["gpt-5.6-terra"]}), self._capture() as out:
            r._show_matrix_resolution(MATRIX, s, "t")
        tables = [
            c.args[0]
            for c in out.call_args_list
            if c.args and hasattr(c.args[0], "columns")
        ]
        assert tables, "no Rich table printed"
        cells = [str(cell) for col in tables[0].columns for cell in col._cells]
        assert any("gpt-?.?-terra \u2192 gpt-5.6-terra" in c for c in cells), cells


# ---------------------------------------------------------------------------
# the helper that feeds all of this
# ---------------------------------------------------------------------------


def test_list_models_for_provider_returns_model_ids_not_reprs(tmp_path):
    """ModelInfo carries `.id`; the old `getattr(m, "name", m)` fell through to
    the dataclass repr, so every live-verified candidate read "no model
    matches". Dead code until `_LiveModels` called it -- now load-bearing."""
    from amplifier_core import ModelInfo

    s = _settings(tmp_path, OPENAI_ONLY)
    infos = [
        ModelInfo(
            id="gpt-5.6-terra",
            display_name="GPT 5.6 Terra",
            context_window=272000,
            max_output_tokens=128000,
        ),
        ModelInfo(
            id="gpt-5.6-luna",
            display_name="GPT 5.6 Luna",
            context_window=272000,
            max_output_tokens=128000,
        ),
    ]
    with (
        patch(
            "amplifier_app_cli.provider_loader.get_provider_models", return_value=infos
        ),
        patch.object(r, "_resolve_provider_module", return_value="provider-openai"),
        patch.object(r, "_get_provider_config", return_value={}),
    ):
        assert r._list_models_for_provider("openai", s) == [
            "gpt-5.6-terra",
            "gpt-5.6-luna",
        ]
