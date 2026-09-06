"""`routing list`/`show` must agree with the runtime about which matrices exist.

MEASURED DEFECT (lane otr, 2026-09-06). With ``routing.matrix =
anthropic-knob-consistent`` in effect and the session actively routing 13
agents through it, the CLI said::

    Matrix 'anthropic-knob-consistent' not found
    0 active, 9 disabled

That is the tool an operator reaches for *precisely when routing is
misbehaving*, telling them the opposite of the truth.

WHY IT HAPPENS. Two identities, one of which the runtime does not have:

* the runtime resolves a matrix by **file stem** -- ``resolve_matrix_source()``
  builds ``f"{name}.yaml"`` and loads the first hit. ``load_matrix()`` requires
  only that the file parse to a mapping, and ``validate_matrix()`` requires
  ``general``/``fast`` roles. **Neither reads the YAML's ``name:`` field at
  all**; as of routing-matrix ``972b0ce``, ``matrix_loader.py`` and
  ``resolver_class.py`` contain no read of it.
* the CLI dropped any matrix whose YAML lacked a ``name:`` key::

      if data and "name" in data:
          matrices[stem] = (data, path)

  A file with no ``name:`` is fully loadable and routable by the runtime, and
  invisible to the CLI. Dropped from ``list`` (so a live matrix contributes to
  neither the active nor the disabled count) and "not found" in ``show``.

PR #294 fixed the *keying* -- rows moved from the YAML's ``name:`` to the file
stem. It left the *existence gate* on ``name:``, which is the half that
produces the measured output above. This file pins the gate.

The invariant, stated once: **a stem the runtime can resolve is a stem the CLI
lists.** :class:`TestCliAndRuntimeCannotDiverge` asserts exactly that,
comparing the CLI's row set against the runtime's own resolver rather than
against a hand-written expectation.

About ``_STAND_IN_MATRIX_LOADER``: hooks-routing is a *bundle* module, not a
distribution this repo depends on, so there is nothing to import in a test
environment. It is imported here from ``test_routing_winner_selection`` rather
than copied, so the two files cannot drift apart -- see that module's docstring
for its provenance (routing-matrix ``d17d03c``, PR #52).
"""

import json
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from test_routing_winner_selection import (  # type: ignore[import-not-found]
    _STAND_IN_MATRIX_LOADER,
    _discovered,
    _make_bundle,
    _make_settings,
    _matrix,
)

# The nine matrices the routing-matrix bundle ships (972b0ce), so the
# reproduction below produces otr's "9 disabled" literally rather than
# approximately.
SHIPPED = (
    "anthropic",
    "balanced",
    "copilot",
    "economy",
    "gemini",
    "ollama",
    "openai-knob-consistent",
    "openai",
    "quality",
)

LIVE_STEM = "anthropic-knob-consistent"


# ---------------------------------------------------------------------------
# Tree fixtures
# ---------------------------------------------------------------------------


def _no_name(description: str) -> dict:
    """A valid, runtime-loadable matrix that simply carries no ``name:``.

    ``validate_matrix()`` requires ``general`` and ``fast`` roles; ``_matrix``
    supplies both. Nothing in the runtime asks for ``name:``.
    """
    data = _matrix("ignored", description)
    del data["name"]
    return data


def _host_tree(tmp_path: Path, live: dict | None = None) -> Path:
    """The measured host: nine shipped matrices + one live user matrix."""
    _make_bundle(tmp_path, matrices={stem: f"Shipped {stem}." for stem in SHIPPED})
    user_dir = tmp_path / ".amplifier" / "routing"
    user_dir.mkdir(parents=True, exist_ok=True)
    if live is not None:
        (user_dir / f"{LIVE_STEM}.yaml").write_text(yaml.dump(live))
    return user_dir


def _invoke(tmp_path: Path, args: list[str], *, active: str | None = None):
    """Run a routing subcommand, optionally with ``routing.matrix`` set.

    ``active`` is written to the top-level ``routing:`` key -- the one
    ``get_routing_config()`` reads and the one hooks-routing appends
    ``.yaml`` to.
    """
    from amplifier_app_cli.commands import routing as routing_mod

    settings = _make_settings(tmp_path)
    if active is not None:
        scope = settings._read_scope("global")
        scope["routing"] = {"matrix": active}
        settings._write_scope("global", scope)

    with (
        patch.object(routing_mod, "_get_settings", return_value=settings),
        patch.object(
            routing_mod, "_discover_matrix_files", return_value=_discovered(tmp_path)
        ),
        patch.object(routing_mod.Path, "home", return_value=tmp_path),
    ):
        return CliRunner().invoke(routing_mod.routing_group, args)


def _flat(output: str) -> str:
    """Collapse Rich's console wrapping so substring asserts are width-proof."""
    return " ".join(output.split())


def _row_key(rendered_name: str) -> str:
    """Strip the ``→ `` active marker and any ``⚠ ...`` suffixes from a row."""
    name = rendered_name.strip().removeprefix("→ ").strip()
    return name.split("  ")[0].strip()


def _rows(tmp_path: Path, *, active: str | None = None) -> dict[str, dict]:
    """`routing list --format json`, keyed by the row's bare name."""
    result = _invoke(tmp_path, ["list", "--format", "json"], active=active)
    assert result.exit_code == 0, result.output
    return {_row_key(item["name"]): item for item in json.loads(result.output)}


# ---------------------------------------------------------------------------
# The runtime's own answer, asked directly
# ---------------------------------------------------------------------------


def _runtime_resolvable(tmp_path: Path, stems: list[str]) -> set[str]:
    """Which stems hooks-routing would actually resolve, per its own function.

    ``resolve_matrix_source`` is obtained through *production's own*
    ``load_resolve_matrix_source`` -- the same call ``routing list`` makes --
    so this asks the runtime rather than re-deriving its rule in the test.
    """
    from amplifier_app_cli.lib.routing_provenance import (
        classify_routing_dirs,
        load_resolve_matrix_source,
    )

    matrix_files = _discovered(tmp_path)
    custom_dirs, bundle_dirs = classify_routing_dirs(matrix_files)
    resolve = load_resolve_matrix_source(bundle_dirs)
    assert resolve is not None, "fixture bundle must carry matrix_loader.py"

    resolvable = set()
    for stem in stems:
        source = resolve(stem, custom_dirs, bundle_dirs[0])
        if source.path is not None:
            resolvable.add(stem)
    return resolvable


# ---------------------------------------------------------------------------
# Non-vacuity: the runtime really does not need `name:`
# ---------------------------------------------------------------------------


class TestRuntimeDoesNotReadDeclaredName:
    """If the runtime DID require ``name:``, dropping the row would be right.

    These gate the rest of the file: they prove the CLI's old requirement was
    the CLI's own invention, not a rule inherited from the loader.
    """

    def test_stand_in_loader_resolves_a_file_that_has_no_name_field(self, tmp_path):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        assert LIVE_STEM in _runtime_resolvable(tmp_path, [LIVE_STEM])

    def test_stand_in_loader_source_never_reads_the_declared_name(self, tmp_path):
        """The contract in prose, pinned against the fixture's own source."""
        assert '"name"' not in _STAND_IN_MATRIX_LOADER.replace("name: str", "")
        # It keys on the requested stem, and only on that.
        assert 'filename = f"{name}.yaml"' in _STAND_IN_MATRIX_LOADER


# ---------------------------------------------------------------------------
# The measured defect
# ---------------------------------------------------------------------------


class TestLiveMatrixWithNoDeclaredName:
    """otr's measured contradiction, reproduced end to end.

    Fail-before, at parent commit ``3bb0104``::

        ── routing matrices (0 active, 9 disabled) ──
        Matrix 'anthropic-knob-consistent' not found. Available: anthropic, ...
    """

    def test_list_reports_the_live_matrix_as_present(self, tmp_path):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        rows = _rows(tmp_path, active=LIVE_STEM)

        assert LIVE_STEM in rows, (
            "the live matrix is missing from `routing list` -- this is otr's "
            "'0 active, 9 disabled' against a matrix that was routing 13 agents"
        )
        assert set(rows) == {*SHIPPED, LIVE_STEM}

    def test_list_reports_the_live_matrix_as_active(self, tmp_path):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        rows = _rows(tmp_path, active=LIVE_STEM)

        assert rows[LIVE_STEM]["enabled"] is True
        assert rows[LIVE_STEM]["behaviors"] == ["active"]

    def test_console_listing_never_says_zero_active(self, tmp_path):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        result = _invoke(tmp_path, ["list"], active=LIVE_STEM)

        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert "0 active" not in flat
        assert "1 active" in flat
        assert LIVE_STEM in flat

    def test_show_renders_the_live_matrix_instead_of_not_found(self, tmp_path):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        result = _invoke(tmp_path, ["show", LIVE_STEM], active=LIVE_STEM)

        assert result.exit_code == 0, result.output
        assert "not found" not in _flat(result.output)

    def test_bare_show_follows_the_active_setting_to_the_live_matrix(self, tmp_path):
        """`routing show` with no argument reads ``routing.matrix``."""
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        result = _invoke(tmp_path, ["show"], active=LIVE_STEM)

        assert result.exit_code == 0, result.output
        assert "not found" not in _flat(result.output)

    def test_show_detailed_titles_the_matrix_by_the_stem_the_runtime_uses(
        self, tmp_path
    ):
        """Never ``Matrix: unknown`` -- that is the same lie in a quieter voice."""
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        result = _invoke(tmp_path, ["show", LIVE_STEM, "--detailed"], active=LIVE_STEM)

        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert f"Matrix: {LIVE_STEM}" in flat
        assert "unknown" not in flat

    def test_show_compact_titles_the_matrix_by_the_stem(self, tmp_path):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        result = _invoke(tmp_path, ["show", LIVE_STEM, "--compact"], active=LIVE_STEM)

        assert result.exit_code == 0, result.output
        assert f"Routing: {LIVE_STEM}" in _flat(result.output)

    def test_use_accepts_the_stem_the_runtime_resolves(self, tmp_path):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        result = _invoke(tmp_path, ["use", LIVE_STEM])

        assert result.exit_code == 0, result.output
        assert f"set to '{LIVE_STEM}'" in _flat(result.output)
        assert "not found" not in _flat(result.output)

    def test_absent_name_is_not_reported_as_a_disagreement(self, tmp_path):
        """No ``name:`` is not a MISMATCHED ``name:``.

        The runtime never reads the field, so its absence is unremarkable and
        must not produce a warning that sends an operator looking for a
        problem that does not exist.
        """
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        result = _invoke(tmp_path, ["list"], active=LIVE_STEM)
        rows = _rows(tmp_path, active=LIVE_STEM)

        assert "file says name:" not in _flat(result.output)
        assert "declared_name" not in rows[LIVE_STEM]["config_summary"]


# ---------------------------------------------------------------------------
# The invariant, asked of the runtime rather than of a fixture
# ---------------------------------------------------------------------------


class TestCliAndRuntimeCannotDiverge:
    """Every stem the runtime resolves is a stem the CLI lists, and vice versa.

    This is the pin the deliverable asks for. It compares the CLI's row set
    against ``resolve_matrix_source()`` -- the runtime's own function, loaded
    out of the cached bundle -- so it cannot be satisfied by teaching the test
    the same wrong answer as the code.

    The identity lives in another repo (``matrix_loader.py`` in
    amplifier-bundle-routing-matrix), so this pins the CLI side against the
    documented keying rather than editing across the boundary.
    """

    def test_row_set_equals_the_runtime_resolvable_set_with_a_nameless_matrix(
        self, tmp_path
    ):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))
        stems = [*SHIPPED, LIVE_STEM]

        assert set(_rows(tmp_path)) == _runtime_resolvable(tmp_path, stems)

    def test_row_set_equals_the_runtime_resolvable_set_with_a_mismatched_name(
        self, tmp_path
    ):
        _host_tree(tmp_path, live=_matrix("knob-consistent-v3", "Live matrix."))
        stems = [*SHIPPED, LIVE_STEM]

        assert set(_rows(tmp_path)) == _runtime_resolvable(tmp_path, stems)

    def test_row_set_equals_the_runtime_resolvable_set_on_a_stock_tree(self, tmp_path):
        _host_tree(tmp_path, live=None)

        assert set(_rows(tmp_path)) == _runtime_resolvable(tmp_path, list(SHIPPED))

    def test_a_stem_the_runtime_cannot_resolve_is_not_listed(self, tmp_path):
        """Non-vacuity: the comparison above is not trivially true of any set."""
        _host_tree(tmp_path, live=None)

        assert "nonexistent-matrix" not in _rows(tmp_path)
        assert _runtime_resolvable(tmp_path, ["nonexistent-matrix"]) == set()


# ---------------------------------------------------------------------------
# A mismatched `name:` still warns (PR #294's behaviour, unchanged)
# ---------------------------------------------------------------------------


class TestMismatchedNameStillWarns:
    def test_list_still_flags_a_name_that_disagrees_with_the_filename(self, tmp_path):
        _host_tree(tmp_path, live=_matrix("knob-consistent-v3", "Live matrix."))

        result = _invoke(tmp_path, ["list"], active=LIVE_STEM)

        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert "file says name: knob-consistent-v3" in flat
        assert "routing resolves by filename" in flat

    def test_show_titles_a_mismatched_matrix_by_its_stem_not_its_declared_name(
        self, tmp_path
    ):
        """The header named the identity the runtime does NOT use."""
        _host_tree(tmp_path, live=_matrix("knob-consistent-v3", "Live matrix."))

        result = _invoke(tmp_path, ["show", LIVE_STEM, "--detailed"], active=LIVE_STEM)

        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert f"Matrix: {LIVE_STEM}" in flat
        assert "Matrix: knob-consistent-v3" not in flat
        # The declared name is still disclosed -- surfaced, not hidden.
        assert "file says name: knob-consistent-v3" in flat


# ---------------------------------------------------------------------------
# Runtime selection is UNCHANGED -- this is a reporting fix
# ---------------------------------------------------------------------------


class TestRuntimeSelectionUnchanged:
    def test_list_and_show_never_write_settings(self, tmp_path):
        """A reporting command that mutated ``routing.matrix`` would be a
        behaviour change; assert the settings bytes are untouched."""
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        settings_file = tmp_path / "global" / "settings.yaml"
        _invoke(tmp_path, ["list"], active=LIVE_STEM)
        before = settings_file.read_bytes()

        _invoke(tmp_path, ["list"], active=LIVE_STEM)
        _invoke(tmp_path, ["show", LIVE_STEM], active=LIVE_STEM)
        _invoke(tmp_path, ["show"], active=LIVE_STEM)

        assert settings_file.read_bytes() == before

    def test_the_winning_path_per_stem_is_untouched_by_this_change(self, tmp_path):
        """``resolve_winning_paths`` decides which FILE the runtime reads.

        The fix changes which rows survive, never which file a row points at.
        """
        from amplifier_app_cli.lib.routing_provenance import resolve_winning_paths

        user = _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        winners = resolve_winning_paths(_discovered(tmp_path))

        assert winners[LIVE_STEM] == user / f"{LIVE_STEM}.yaml"
        for stem in SHIPPED:
            assert winners[stem].name == f"{stem}.yaml"
            assert ".amplifier/routing" not in winners[stem].as_posix()

    def test_the_row_points_at_the_file_the_runtime_would_load(self, tmp_path):
        _host_tree(tmp_path, live=_no_name("Knob-consistent anthropic routing."))

        rows = _rows(tmp_path, active=LIVE_STEM)

        assert (
            rows[LIVE_STEM]["matrix_file"] == f"~/.amplifier/routing/{LIVE_STEM}.yaml"
        )


# ---------------------------------------------------------------------------
# No regression for the common case
# ---------------------------------------------------------------------------


class TestAgreeingTreeUnchanged:
    """A tree where every stem agrees with its ``name:`` must not move.

    The golden strings below are the output at parent commit ``3bb0104``,
    captured before the change. Any drift in the common case fails here.
    """

    def _agreeing(self, tmp_path: Path) -> None:
        _make_bundle(tmp_path, matrices={stem: f"Shipped {stem}." for stem in SHIPPED})

    def test_console_listing_is_byte_identical(self, tmp_path):
        self._agreeing(tmp_path)

        result = _invoke(tmp_path, ["list"], active="balanced")

        assert result.exit_code == 0, result.output
        rendered = [
            line
            for line in result.output.splitlines()
            if "plaintext-secret" not in line
            and "Could not resolve provider" not in line
            and line.strip()
        ]
        assert rendered == [
            "── routing matrices (1 active, 8 disabled) ──",
            "  [off]    anthropic  (available)  ← disabled",
            "  [on]  → balanced  (active)",
            "  [off]    copilot  (available)  ← disabled",
            "  [off]    economy  (available)  ← disabled",
            "  [off]    gemini  (available)  ← disabled",
            "  [off]    ollama  (available)  ← disabled",
            "  [off]    openai  (available)  ← disabled",
            "  [off]    openai-knob-consistent  (available)  ← disabled",
            "  [off]    quality  (available)  ← disabled",
        ]

    def test_no_warning_footers_on_an_agreeing_tree(self, tmp_path):
        self._agreeing(tmp_path)

        result = _invoke(tmp_path, ["list"], active="balanced")

        assert "file says name:" not in result.output
        assert "shadow" not in result.output

    def test_show_header_is_unchanged_when_stem_and_name_agree(self, tmp_path):
        """Titling by stem is a no-op exactly when the two agree."""
        self._agreeing(tmp_path)

        result = _invoke(
            tmp_path, ["show", "balanced", "--detailed"], active="balanced"
        )

        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert "Matrix: balanced" in flat
        assert "Shipped balanced." in flat

    def test_json_rows_carry_no_declared_name_when_they_agree(self, tmp_path):
        self._agreeing(tmp_path)

        rows = _rows(tmp_path, active="balanced")

        assert set(rows) == set(SHIPPED)
        for stem in SHIPPED:
            assert "declared_name" not in rows[stem]["config_summary"]


# ---------------------------------------------------------------------------
# A file the runtime cannot load must still be dropped
# ---------------------------------------------------------------------------


class TestUnloadableFilesStillDropped:
    """The gate loosens to the runtime's rule -- it does not disappear.

    ``load_matrix()`` raises unless the file parses to a mapping, so a
    non-mapping or empty file is unloadable for the runtime too and must not
    gain a row.
    """

    def test_a_non_mapping_file_is_not_listed(self, tmp_path):
        user = _host_tree(tmp_path, live=None)
        (user / f"{LIVE_STEM}.yaml").write_text("[not, a, mapping]\n")

        assert LIVE_STEM not in _rows(tmp_path)

    def test_an_empty_file_is_not_listed(self, tmp_path):
        user = _host_tree(tmp_path, live=None)
        (user / f"{LIVE_STEM}.yaml").write_text("")

        assert LIVE_STEM not in _rows(tmp_path)

    def test_an_unparseable_file_is_not_listed(self, tmp_path):
        user = _host_tree(tmp_path, live=None)
        (user / f"{LIVE_STEM}.yaml").write_text("{[: unbalanced\n")

        assert LIVE_STEM not in _rows(tmp_path)

    def test_the_shipped_matrices_survive_a_broken_neighbour(self, tmp_path):
        user = _host_tree(tmp_path, live=None)
        (user / f"{LIVE_STEM}.yaml").write_text("[not, a, mapping]\n")

        assert set(_rows(tmp_path)) == set(SHIPPED)
