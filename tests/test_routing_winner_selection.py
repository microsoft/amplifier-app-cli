"""`amplifier routing list`/`show`/`use` pick the row the LOADER would load.

Background. The listing used to build its rows like this::

    matrices[data["name"]] = (data, path)      # over sorted(discovered files)

Two things in one line, and neither matches hooks-routing:

* the key is the ``name:`` field *inside* the YAML, but the loader resolves
  ``f"{matrix_name}.yaml"`` -- by **file stem**;
* the winner is whichever file comes LAST in ``sorted()``, but the loader takes
  the FIRST hit in ``[*custom_routing_dirs, bundle routing/]``.

On a stock host those two rules agree by accident: ``~/.amplifier/cache/...``
sorts before ``~/.amplifier/routing/...`` because ``"c" < "r"``, so the user
file lands last and wins either way. This file constructs the cases where they
DISAGREE, which is the whole point -- when they disagree, the old listing told
the user that a file was in use which the loader would never read.

Each disagreement test re-runs the old algorithm inline
(:func:`_last_write_wins`) and asserts it picks the *other* file first, so the
test cannot quietly become vacuous if the trees stop colliding.

About ``_STAND_IN_MATRIX_LOADER``: hooks-routing is a *bundle* module, not a
distribution this repo depends on, so there is nothing to import in a test
environment. The stand-in reproduces the contract of
``amplifier_module_hooks_routing.matrix_loader.resolve_matrix_source`` as of
routing-matrix ``d17d03c`` (PR #52). It mirrors the copy in
``test_routing_shadowing.py``; both are stand-ins for the same upstream
function, which is the single source of truth for the precedence rule.
Production code never uses either -- it loads the real module out of the
cached bundle.
"""

import json
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths

# ---------------------------------------------------------------------------
# Stand-in for the bundle's matrix_loader.py (contract: routing-matrix d17d03c)
# ---------------------------------------------------------------------------

_STAND_IN_MATRIX_LOADER = '''\
"""Stand-in for amplifier_module_hooks_routing.matrix_loader (test fixture)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

USER_SOURCE = "user"
BUNDLE_SOURCE = "bundle"


@dataclass(frozen=True)
class MatrixSource:
    name: str
    path: Path | None = None
    source: str | None = None
    shadowed: tuple[tuple[Path, str], ...] = ()
    searched: tuple[Path, ...] = field(default=())

    @property
    def is_shadowed(self) -> bool:
        return bool(self.shadowed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matrix_name": self.name,
            "matrix_path": str(self.path) if self.path is not None else None,
            "matrix_source": self.source,
            "matrix_shadowed": self.is_shadowed,
            "shadowed_paths": [str(p) for p, _ in self.shadowed],
        }


def resolve_matrix_source(
    name: str,
    custom_routing_dirs: Sequence[Path],
    bundle_routing_dir: Path,
) -> MatrixSource:
    filename = f"{name}.yaml"

    def _key(path: Path) -> Path:
        try:
            return path.resolve()
        except OSError:
            return path

    bundle_key = _key(bundle_routing_dir)
    candidates: list[tuple[Path, str]] = [
        (
            Path(d) / filename,
            BUNDLE_SOURCE if _key(Path(d)) == bundle_key else USER_SOURCE,
        )
        for d in custom_routing_dirs
    ]
    candidates.append((bundle_routing_dir / filename, BUNDLE_SOURCE))

    searched: list[Path] = []
    present: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for candidate, origin in candidates:
        candidate_key = _key(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        searched.append(candidate)
        if candidate.exists():
            present.append((candidate, origin))

    if not present:
        return MatrixSource(name=name, searched=tuple(searched))

    winner, winner_source = present[0]
    return MatrixSource(
        name=name,
        path=winner,
        source=winner_source,
        shadowed=tuple(present[1:]),
        searched=tuple(searched),
    )
'''


# ---------------------------------------------------------------------------
# The algorithm this change removes, kept so disagreement can be PROVEN
# ---------------------------------------------------------------------------


def _last_write_wins(matrix_files: list[Path]) -> dict[str, Path]:
    """The pre-change row selection, verbatim in behaviour.

    ``matrices[data["name"]] = (data, path)`` over the discovered files, so the
    last file in sort order overwrites the entry, keyed by the YAML's internal
    ``name:``.
    """
    winners: dict[str, Path] = {}
    for path in matrix_files:
        data = yaml.safe_load(path.read_text()) or {}
        if data and "name" in data:
            winners[data["name"]] = path
    return winners


# ---------------------------------------------------------------------------
# Tree fixtures
# ---------------------------------------------------------------------------


def _matrix(name: str, description: str) -> dict:
    return {
        "name": name,
        "description": description,
        "updated": "2026-09-02",
        "roles": {
            "general": {
                "description": "Catch-all",
                "candidates": [{"provider": "anthropic", "model": "claude-sonnet-*"}],
            },
            "fast": {
                "description": "Quick tasks",
                "candidates": [{"provider": "anthropic", "model": "claude-haiku-*"}],
            },
        },
    }


def _make_bundle(
    tmp_path: Path,
    *,
    cache_dirname: str = "cache",
    with_loader: bool = True,
    matrices: dict[str, str] | None = None,
) -> Path:
    """Create a cached routing-matrix bundle tree. Returns its ``routing/`` dir.

    ``cache_dirname`` is a knob on purpose. The bundle normally lives under
    ``~/.amplifier/cache/``, which sorts before ``~/.amplifier/routing/``;
    passing ``"zz-cache"`` reverses that, which is exactly the "any change to
    the cache path silently flips which file the CLI shows" case.
    """
    bundle_root = (
        tmp_path / ".amplifier" / cache_dirname / "amplifier-bundle-routing-matrix-test"
    )
    routing_dir = bundle_root / "routing"
    routing_dir.mkdir(parents=True)

    contents = matrices or {
        "openai": "Shipped OpenAI routing.",
        "balanced": "Shipped balanced routing.",
    }
    for stem, description in contents.items():
        (routing_dir / f"{stem}.yaml").write_text(yaml.dump(_matrix(stem, description)))

    if with_loader:
        pkg = bundle_root / "modules" / "hooks-routing"
        pkg = pkg / "amplifier_module_hooks_routing"
        pkg.mkdir(parents=True)
        (pkg / "matrix_loader.py").write_text(_STAND_IN_MATRIX_LOADER)

    return routing_dir


def _make_user_dir(tmp_path: Path, files: dict[str, dict]) -> Path:
    """Write ``{stem: matrix_dict}`` into ``<tmp>/.amplifier/routing/``."""
    user_dir = tmp_path / ".amplifier" / "routing"
    user_dir.mkdir(parents=True, exist_ok=True)
    for stem, data in files.items():
        (user_dir / f"{stem}.yaml").write_text(yaml.dump(data))
    return user_dir


def _make_settings(tmp_path: Path) -> AppSettings:
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    settings = AppSettings(paths=paths)
    scope_settings = settings._read_scope("global")
    scope_settings["config"] = {
        "providers": [{"module": "provider-anthropic", "config": {"priority": 1}}]
    }
    settings._write_scope("global", scope_settings)
    return settings


def _discovered(tmp_path: Path) -> list[Path]:
    """Everything ``_discover_matrix_files()`` would return, same sort order.

    The real function globs the bundle cache and then the custom dir and
    returns ``sorted(files)`` -- so only the sort order matters here, not the
    order the two globs ran in.
    """
    amp = tmp_path / ".amplifier"
    return sorted(amp.glob("**/routing/*.yaml"))


def _invoke(tmp_path: Path, args: list[str]):
    """Run a routing subcommand against the tmp tree."""
    from amplifier_app_cli.commands import routing as routing_mod

    settings = _make_settings(tmp_path)
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
    """Strip the ``→ `` active marker and any ``⚠ ...`` suffixes from a row name."""
    name = rendered_name.strip().removeprefix("→ ").strip()
    return name.split("  ")[0].strip()


def _rows(tmp_path: Path) -> dict[str, dict]:
    """`routing list --format json`, keyed by the row's bare name."""
    result = _invoke(tmp_path, ["list", "--format", "json"])
    assert result.exit_code == 0, result.output
    return {_row_key(item["name"]): item for item in json.loads(result.output)}


# ---------------------------------------------------------------------------
# Disagreement 1 -- sort order no longer puts the user file last
# ---------------------------------------------------------------------------


class TestSortOrderDisagreement:
    """The bundle file sorts LAST, so last-write-wins picks it; the loader does not."""

    def test_the_two_rules_actually_disagree_on_this_tree(self, tmp_path):
        """Non-vacuity gate: if this fails, the tests below prove nothing."""
        bundle = _make_bundle(tmp_path, cache_dirname="zz-cache")
        user = _make_user_dir(
            tmp_path, {"openai": _matrix("openai", "Custom matrix: openai")}
        )
        files = _discovered(tmp_path)

        # The old rule: last file in sort order wins.
        assert _last_write_wins(files)["openai"] == bundle / "openai.yaml"

        # The loader's rule: first hit in [*custom_dirs, bundle routing/].
        from amplifier_app_cli.lib.routing_provenance import resolve_matrix_origins

        assert resolve_matrix_origins(files)["openai"].path == user / "openai.yaml"

    def test_resolve_winning_paths_follows_the_loader(self, tmp_path):
        from amplifier_app_cli.lib.routing_provenance import resolve_winning_paths

        _make_bundle(tmp_path, cache_dirname="zz-cache")
        user = _make_user_dir(
            tmp_path, {"openai": _matrix("openai", "Custom matrix: openai")}
        )

        winners = resolve_winning_paths(_discovered(tmp_path))

        assert winners["openai"] == user / "openai.yaml"
        assert winners["balanced"].name == "balanced.yaml"

    def test_list_shows_the_loaders_winner_not_the_last_written(self, tmp_path):
        _make_bundle(tmp_path, cache_dirname="zz-cache")
        _make_user_dir(tmp_path, {"openai": _matrix("openai", "Custom matrix: openai")})

        rows = _rows(tmp_path)

        assert rows["openai"]["matrix_file"] == "~/.amplifier/routing/openai.yaml"
        summary = rows["openai"]["config_summary"]
        assert summary["description"] == "Custom matrix: openai"
        assert summary["description"] != "Shipped OpenAI routing."

    def test_show_renders_the_loaders_winner(self, tmp_path):
        _make_bundle(tmp_path, cache_dirname="zz-cache")
        _make_user_dir(tmp_path, {"openai": _matrix("openai", "Custom matrix: openai")})

        result = _invoke(tmp_path, ["show", "openai", "--detailed"])

        assert result.exit_code == 0, result.output
        assert "Custom matrix: openai" in result.output
        assert "Shipped OpenAI routing." not in result.output

    def test_shadowing_marker_still_names_the_same_winner(self, tmp_path):
        """The row and the shadowing footer must not contradict each other."""
        _make_bundle(tmp_path, cache_dirname="zz-cache")
        _make_user_dir(tmp_path, {"openai": _matrix("openai", "Custom matrix: openai")})

        rows = _rows(tmp_path)
        source = rows["openai"]["routing_source"]

        # matrix_path is a native absolute path (backslashes on Windows) --
        # compare as a Path, not as a POSIX string. matrix_file is the display
        # form and IS POSIX-spelled on every platform (see _display_path).
        assert (
            Path(source["matrix_path"]) == tmp_path / ".amplifier/routing/openai.yaml"
        )
        assert rows["openai"]["matrix_file"] == "~/.amplifier/routing/openai.yaml"
        assert len(source["shadowed_paths"]) == 1
        assert "amplifier-bundle-routing-matrix-test" in source["shadowed_paths"][0]


# ---------------------------------------------------------------------------
# Disagreement 2 -- the YAML's `name:` differs from its filename
# ---------------------------------------------------------------------------


class TestNameStemDisagreement:
    """A user file named ``my-fast.yaml`` that declares ``name: balanced``.

    Under last-write-wins it sorted last and OVERWROTE the row for the real
    ``balanced`` matrix, so ``routing list``/``show balanced`` displayed a file
    the loader would never resolve as ``balanced``.
    """

    def _tree(self, tmp_path: Path) -> tuple[Path, Path]:
        bundle = _make_bundle(
            tmp_path, matrices={"balanced": "Shipped balanced routing."}
        )
        user = _make_user_dir(
            tmp_path, {"my-fast": _matrix("balanced", "Custom matrix: my-fast")}
        )
        return bundle, user

    def test_the_two_rules_actually_disagree_on_this_tree(self, tmp_path):
        """Non-vacuity gate: the old rule really did hand back the wrong file."""
        bundle, user = self._tree(tmp_path)
        files = _discovered(tmp_path)

        old = _last_write_wins(files)
        # One row, keyed "balanced", pointing at my-fast.yaml.
        assert old["balanced"] == user / "my-fast.yaml"
        assert "my-fast" not in old

        from amplifier_app_cli.lib.routing_provenance import resolve_winning_paths

        new = resolve_winning_paths(files)
        assert new["balanced"] == bundle / "balanced.yaml"
        assert new["my-fast"] == user / "my-fast.yaml"

    def test_list_keys_rows_by_filename_and_flags_the_disagreement(self, tmp_path):
        self._tree(tmp_path)

        rows = _rows(tmp_path)

        assert set(rows) == {"balanced", "my-fast"}
        assert (
            rows["balanced"]["config_summary"]["description"]
            == "Shipped balanced routing."
        )
        assert rows["balanced"]["matrix_file"].endswith(
            "amplifier-bundle-routing-matrix-test/routing/balanced.yaml"
        )
        assert rows["my-fast"]["config_summary"]["declared_name"] == "balanced"
        assert "declared_name" not in rows["balanced"]["config_summary"]

    def test_console_listing_surfaces_the_disagreement(self, tmp_path):
        self._tree(tmp_path)

        result = _invoke(tmp_path, ["list"])

        assert result.exit_code == 0, result.output
        assert "file says name: balanced" in _flat(result.output)
        assert "routing resolves by filename" in _flat(result.output)

    def test_show_balanced_renders_the_bundle_file(self, tmp_path):
        self._tree(tmp_path)

        result = _invoke(tmp_path, ["show", "balanced", "--detailed"])

        assert result.exit_code == 0, result.output
        assert "Shipped balanced routing." in result.output
        assert "Custom matrix: my-fast" not in result.output

    def test_use_writes_the_filename_the_loader_resolves(self, tmp_path):
        self._tree(tmp_path)

        result = _invoke(tmp_path, ["use", "my-fast"])

        assert result.exit_code == 0, result.output
        assert "set to 'my-fast'" in result.output

    def test_use_of_a_declared_name_is_refused_with_the_filename_to_use(self, tmp_path):
        """The old listing advertised ``turbo``; the loader can never load it."""
        _make_bundle(tmp_path, matrices={"balanced": "Shipped balanced routing."})
        _make_user_dir(
            tmp_path, {"my-fast": _matrix("turbo", "Custom matrix: my-fast")}
        )

        result = _invoke(tmp_path, ["use", "turbo"])

        assert result.exit_code == 0, result.output
        assert "not found" in _flat(result.output)
        assert "routing resolves by filename" in _flat(result.output)
        assert "use: my-fast" in _flat(result.output)


# ---------------------------------------------------------------------------
# Provenance unreachable -- still not last-write-wins
# ---------------------------------------------------------------------------


class TestProvenanceUnreachableFallback:
    """A cached bundle older than routing-matrix PR #52 has no function to ask.

    The shadowing MARKER is still withheld (a wrong marker is worse than none),
    but a row has to point at some file, and the fallback picks by the same
    ``[*custom_dirs, *bundle_dirs]`` precedence the loader uses -- never by
    sort order.
    """

    def test_fallback_picks_the_user_file_when_it_sorts_first(self, tmp_path):
        from amplifier_app_cli.lib.routing_provenance import (
            resolve_matrix_origins,
            resolve_winning_paths,
        )

        bundle = _make_bundle(tmp_path, cache_dirname="zz-cache", with_loader=False)
        user = _make_user_dir(
            tmp_path, {"openai": _matrix("openai", "Custom matrix: openai")}
        )
        files = _discovered(tmp_path)

        assert resolve_matrix_origins(files) == {}  # nothing to ask
        assert _last_write_wins(files)["openai"] == bundle / "openai.yaml"
        assert resolve_winning_paths(files)["openai"] == user / "openai.yaml"

    def test_listing_shows_the_user_file_but_draws_no_shadow_marker(self, tmp_path):
        _make_bundle(tmp_path, cache_dirname="zz-cache", with_loader=False)
        _make_user_dir(tmp_path, {"openai": _matrix("openai", "Custom matrix: openai")})

        result = _invoke(tmp_path, ["list"])
        rows = _rows(tmp_path)

        assert result.exit_code == 0, result.output
        assert "shadow" not in result.output
        assert (
            rows["openai"]["config_summary"]["description"] == "Custom matrix: openai"
        )


# ---------------------------------------------------------------------------
# Cases that must NOT change
# ---------------------------------------------------------------------------


class TestUnchangedBehaviour:
    def test_agreeing_tree_lists_every_matrix_once(self, tmp_path):
        """Stock layout: cache sorts first, names match stems -- nothing moves."""
        _make_bundle(tmp_path)
        _make_user_dir(
            tmp_path, {"my-custom": _matrix("my-custom", "Custom matrix: my-custom")}
        )

        result = _invoke(tmp_path, ["list"])
        rows = _rows(tmp_path)

        assert result.exit_code == 0, result.output
        assert set(rows) == {"openai", "balanced", "my-custom"}
        assert "shadow" not in result.output
        assert "file says name:" not in result.output

    def test_no_user_routing_dir_at_all(self, tmp_path):
        _make_bundle(tmp_path)
        assert not (tmp_path / ".amplifier" / "routing").exists()

        result = _invoke(tmp_path, ["list"])
        rows = _rows(tmp_path)

        assert result.exit_code == 0, result.output
        assert set(rows) == {"openai", "balanced"}
        assert "shadow" not in result.output
        assert "file says name:" not in result.output

    def test_stock_shadowed_layout_still_picks_the_user_file(self, tmp_path):
        """The case where the two rules AGREE must keep agreeing."""
        from amplifier_app_cli.lib.routing_provenance import resolve_winning_paths

        _make_bundle(tmp_path)
        user = _make_user_dir(
            tmp_path, {"openai": _matrix("openai", "Custom matrix: openai")}
        )
        files = _discovered(tmp_path)

        assert _last_write_wins(files)["openai"] == user / "openai.yaml"
        assert resolve_winning_paths(files)["openai"] == user / "openai.yaml"

    def test_unparseable_winner_drops_the_row_rather_than_showing_the_loser(
        self, tmp_path
    ):
        """A broken user file must not let the shadowed bundle file stand in.

        The loader would fail on that matrix; showing the bundle file instead
        would be the same class of lie in the other direction.
        """
        _make_bundle(tmp_path)
        user = _make_user_dir(tmp_path, {})
        (user / "openai.yaml").write_text("[not, a, mapping]\n")

        rows = _rows(tmp_path)

        assert "openai" not in rows
        assert "balanced" in rows
