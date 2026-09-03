"""`amplifier routing list` / `show` mark a matrix that shadows another.

Background. hooks-routing's ``mount()`` searches
``[*custom_routing_dirs, bundle routing/]`` and loads the FIRST ``<name>.yaml``
it finds, so a same-named file in ``~/.amplifier/routing/`` silently makes the
shipped bundle matrix dead. The CLI used to list both as peers with no
indication that one suppressed the other.

What these tests pin:

* a shadowed setup marks the suppressed matrix AND names the file that wins;
* an unshadowed setup's output is byte-identical to the pre-change output;
* the command still works with no user routing dir at all;
* provenance comes from hooks-routing's ``resolve_matrix_source`` -- when that
  function is unreachable (bundle older than routing-matrix PR #52) the CLI
  draws no marker rather than guessing the precedence rule itself.

About ``_STAND_IN_MATRIX_LOADER`` below: hooks-routing is a *bundle* module,
not a distribution this repo depends on, so there is nothing to import in a
test environment. The stand-in reproduces the contract of
``amplifier_module_hooks_routing.matrix_loader`` as of routing-matrix
``d17d03c`` (PR #52) so the CLI's own consumption path -- locate the bundle,
load the module by file path, call the function, render what it returns -- is
exercised end to end. Production code never uses this file; it loads the real
module out of the cached bundle.
"""

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
# Fixtures
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


def _make_bundle(tmp_path: Path, *, with_loader: bool = True) -> Path:
    """Create a cached routing-matrix bundle tree. Returns its routing/ dir."""
    bundle_root = (
        tmp_path / ".amplifier" / "cache" / "amplifier-bundle-routing-matrix-test"
    )
    routing_dir = bundle_root / "routing"
    routing_dir.mkdir(parents=True)
    (routing_dir / "openai.yaml").write_text(
        yaml.dump(_matrix("openai", "Shipped OpenAI routing."))
    )
    (routing_dir / "balanced.yaml").write_text(
        yaml.dump(_matrix("balanced", "Shipped balanced routing."))
    )

    if with_loader:
        pkg = bundle_root / "modules" / "hooks-routing"
        pkg = pkg / "amplifier_module_hooks_routing"
        pkg.mkdir(parents=True)
        (pkg / "matrix_loader.py").write_text(_STAND_IN_MATRIX_LOADER)

    return routing_dir


def _make_user_dir(tmp_path: Path, names: list[str]) -> Path:
    user_dir = tmp_path / ".amplifier" / "routing"
    user_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (user_dir / f"{name}.yaml").write_text(
            yaml.dump(_matrix(name, f"Custom matrix: {name}"))
        )
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
    """Everything _discover_matrix_files() would return, same sort order."""
    amp = tmp_path / ".amplifier"
    files = list(amp.glob("cache/*/routing/*.yaml")) + list(amp.glob("routing/*.yaml"))
    return sorted(files)


def _invoke(tmp_path: Path, args: list[str], *, provenance: bool = True):
    """Run a routing subcommand against the tmp tree."""
    from amplifier_app_cli.commands import routing as routing_mod

    settings = _make_settings(tmp_path)
    patches = [
        patch.object(routing_mod, "_get_settings", return_value=settings),
        patch.object(
            routing_mod, "_discover_matrix_files", return_value=_discovered(tmp_path)
        ),
        patch.object(routing_mod.Path, "home", return_value=tmp_path),
    ]
    if not provenance:
        # Simulates "provenance unreachable" -- the pre-change code path.
        patches.append(
            patch.object(routing_mod, "resolve_matrix_origins", return_value={})
        )

    runner = CliRunner()
    with patches[0], patches[1], patches[2]:
        if not provenance:
            with patches[3]:
                return runner.invoke(routing_mod.routing_group, args)
        return runner.invoke(routing_mod.routing_group, args)


# ---------------------------------------------------------------------------
# routing list -- shadowed
# ---------------------------------------------------------------------------


class TestRoutingListShadowed:
    def test_shadowed_matrix_is_marked_and_winner_named(self, tmp_path):
        """A user matrix that suppresses a bundle matrix is flagged, both paths shown."""
        _make_bundle(tmp_path)
        _make_user_dir(tmp_path, ["openai"])

        result = _invoke(tmp_path, ["list"])

        assert result.exit_code == 0, result.output
        out = result.output
        # The row itself carries the marker.
        assert "shadows bundle" in out
        # And the relationship is spelled out: winner, then what it suppresses.
        assert "in use" in out
        assert "suppressed" in out
        assert "~/.amplifier/routing/openai.yaml" in out
        assert (
            "~/.amplifier/cache/amplifier-bundle-routing-matrix-test/routing/openai.yaml"
            in out.replace("\n", "")
        )

        # Non-vacuity: the SAME shadowed tree, rendered through the pre-change
        # path (no provenance), shows the two files as peers with no marker.
        # That difference is the whole defect this change fixes.
        before = _invoke(tmp_path, ["list"], provenance=False)
        assert "shadow" not in before.output
        assert "suppressed" not in before.output

    def test_unshadowed_matrix_in_same_listing_is_not_marked(self, tmp_path):
        """Only the colliding name is flagged; peers are left alone."""
        _make_bundle(tmp_path)
        _make_user_dir(tmp_path, ["openai"])

        result = _invoke(tmp_path, ["list"])

        assert result.exit_code == 0, result.output
        # 'balanced' exists only in the bundle -- it must not be flagged.
        assert "1 matrix is shadowed" in result.output
        marked_lines = [
            line for line in result.output.splitlines() if "shadows bundle" in line
        ]
        assert len(marked_lines) == 1
        assert "balanced" not in marked_lines[0]

    def test_two_shadowed_matrices_are_counted(self, tmp_path):
        _make_bundle(tmp_path)
        _make_user_dir(tmp_path, ["openai", "balanced"])

        result = _invoke(tmp_path, ["list"])

        assert result.exit_code == 0, result.output
        assert "2 matrices are shadowed" in result.output

    def test_json_carries_hooks_routing_provenance_fields(self, tmp_path):
        """JSON output exposes MatrixSource.to_dict() verbatim for shadowed entries."""
        import json

        _make_bundle(tmp_path)
        _make_user_dir(tmp_path, ["openai"])

        result = _invoke(tmp_path, ["list", "--format", "json"])

        assert result.exit_code == 0, result.output
        items = json.loads(result.output)
        by_shadowed = [i for i in items if "routing_source" in i]
        assert len(by_shadowed) == 1
        source = by_shadowed[0]["routing_source"]
        assert source["matrix_name"] == "openai"
        assert source["matrix_source"] == "user"
        assert source["matrix_shadowed"] is True
        assert source["matrix_path"].endswith("/.amplifier/routing/openai.yaml")
        assert len(source["shadowed_paths"]) == 1
        assert "amplifier-bundle-routing-matrix-test" in source["shadowed_paths"][0]


# ---------------------------------------------------------------------------
# routing list -- unshadowed output must not change
# ---------------------------------------------------------------------------


class TestRoutingListUnshadowed:
    def test_unshadowed_output_is_byte_identical_to_pre_change(self, tmp_path):
        """No name collision => output identical to the no-provenance code path."""
        _make_bundle(tmp_path)
        _make_user_dir(tmp_path, ["my-custom"])

        with_provenance = _invoke(tmp_path, ["list"])
        without_provenance = _invoke(tmp_path, ["list"], provenance=False)

        assert with_provenance.exit_code == 0, with_provenance.output
        assert with_provenance.output == without_provenance.output
        assert "shadow" not in with_provenance.output

    def test_unshadowed_json_is_byte_identical_to_pre_change(self, tmp_path):
        _make_bundle(tmp_path)
        _make_user_dir(tmp_path, ["my-custom"])

        with_provenance = _invoke(tmp_path, ["list", "--format", "json"])
        without_provenance = _invoke(
            tmp_path, ["list", "--format", "json"], provenance=False
        )

        assert with_provenance.exit_code == 0, with_provenance.output
        assert with_provenance.output == without_provenance.output
        assert "routing_source" not in with_provenance.output

    def test_no_user_routing_dir_at_all(self, tmp_path):
        """The command still works when ~/.amplifier/routing does not exist."""
        _make_bundle(tmp_path)
        assert not (tmp_path / ".amplifier" / "routing").exists()

        result = _invoke(tmp_path, ["list"])
        baseline = _invoke(tmp_path, ["list"], provenance=False)

        assert result.exit_code == 0, result.output
        assert "openai" in result.output
        assert "balanced" in result.output
        assert "shadow" not in result.output
        assert result.output == baseline.output


# ---------------------------------------------------------------------------
# Graceful degradation -- bundle predates routing-matrix PR #52
# ---------------------------------------------------------------------------


class TestProvenanceUnreachable:
    def test_no_marker_when_bundle_has_no_matrix_loader(self, tmp_path):
        """Old cached bundle: no resolve_matrix_source => no marker, not a guess."""
        _make_bundle(tmp_path, with_loader=False)
        _make_user_dir(tmp_path, ["openai"])

        result = _invoke(tmp_path, ["list"])

        assert result.exit_code == 0, result.output
        assert "openai" in result.output
        assert "shadow" not in result.output

    def test_no_marker_when_loader_lacks_the_function(self, tmp_path):
        """A matrix_loader.py without resolve_matrix_source is not an error."""
        from amplifier_app_cli.lib.routing_provenance import resolve_matrix_origins

        routing_dir = _make_bundle(tmp_path, with_loader=False)
        pkg = (
            routing_dir.parent
            / "modules"
            / "hooks-routing"
            / "amplifier_module_hooks_routing"
        )
        pkg.mkdir(parents=True)
        (pkg / "matrix_loader.py").write_text("def load_matrix(path):\n    return {}\n")
        _make_user_dir(tmp_path, ["openai"])

        assert resolve_matrix_origins(_discovered(tmp_path)) == {}


# ---------------------------------------------------------------------------
# routing show
# ---------------------------------------------------------------------------


class TestRoutingShow:
    def test_show_names_the_winner_and_the_suppressed_file(self, tmp_path):
        _make_bundle(tmp_path)
        _make_user_dir(tmp_path, ["openai"])

        result = _invoke(tmp_path, ["show", "openai"])

        assert result.exit_code == 0, result.output
        out = result.output
        assert "shadows a bundle matrix" in out
        assert "~/.amplifier/routing/openai.yaml" in out
        assert "suppressed" in out

    def test_show_unshadowed_output_is_unchanged(self, tmp_path):
        _make_bundle(tmp_path)
        _make_user_dir(tmp_path, ["my-custom"])

        with_provenance = _invoke(tmp_path, ["show", "balanced"])
        without_provenance = _invoke(tmp_path, ["show", "balanced"], provenance=False)

        assert with_provenance.exit_code == 0, with_provenance.output
        assert with_provenance.output == without_provenance.output
        assert "shadow" not in with_provenance.output


# ---------------------------------------------------------------------------
# routing_provenance unit tests
# ---------------------------------------------------------------------------


class TestRoutingProvenance:
    def test_classify_splits_bundle_and_custom_dirs(self, tmp_path):
        from amplifier_app_cli.lib.routing_provenance import classify_routing_dirs

        bundle_routing = _make_bundle(tmp_path)
        user_dir = _make_user_dir(tmp_path, ["openai"])

        custom_dirs, bundle_dirs = classify_routing_dirs(_discovered(tmp_path))

        assert custom_dirs == [user_dir]
        assert bundle_dirs == [bundle_routing]

    def test_loads_the_bundles_own_resolve_matrix_source(self, tmp_path):
        """The function really comes from the bundle on disk, not from this repo."""
        from amplifier_app_cli.lib.routing_provenance import load_resolve_matrix_source

        bundle_routing = _make_bundle(tmp_path)
        fn = load_resolve_matrix_source([bundle_routing])

        assert fn is not None
        loader_path = (
            bundle_routing.parent
            / "modules"
            / "hooks-routing"
            / "amplifier_module_hooks_routing"
            / "matrix_loader.py"
        )
        assert Path(fn.__code__.co_filename) == loader_path

    def test_origins_report_winner_and_shadowed(self, tmp_path):
        from amplifier_app_cli.lib.routing_provenance import resolve_matrix_origins

        bundle_routing = _make_bundle(tmp_path)
        user_dir = _make_user_dir(tmp_path, ["openai"])

        origins = resolve_matrix_origins(_discovered(tmp_path))

        assert set(origins) == {"openai", "balanced"}
        assert origins["openai"].is_shadowed is True
        assert origins["openai"].path == user_dir / "openai.yaml"
        assert origins["openai"].source == "user"
        assert origins["openai"].shadowed == (
            (bundle_routing / "openai.yaml", "bundle"),
        )
        assert origins["balanced"].is_shadowed is False
        assert origins["balanced"].source == "bundle"

    def test_no_bundle_dir_yields_no_origins(self, tmp_path):
        """Without a cached bundle there is no loader to consume -- report nothing."""
        from amplifier_app_cli.lib.routing_provenance import resolve_matrix_origins

        _make_user_dir(tmp_path, ["openai"])
        assert resolve_matrix_origins(_discovered(tmp_path)) == {}
