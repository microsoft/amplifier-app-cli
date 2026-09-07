"""Editable installs must land in the home that asked for them.

FAIL-BEFORE: every test here fails against the shipped CLI, where the install
target is the literal ``sys.executable`` at three call sites and there is no
per-home environment to land in.

The behavior under test is the deeper fix for the incident recorded in
``docs/lanes/q99f-amplifier-home-pth-bug/FINDINGS.md``: ``AMPLIFIER_HOME``
selects the cache root but not the Python environment, so two homes write the
same ``.pth`` filenames into one ``site-packages``. ``lib.venv_home_guard``
refuses to run once that has happened; ``lib.home_env`` removes the shared
resource so there is nothing to refuse.

The isolation assertions below are the unit-level form of the host measurement
quoted in ``docs/lanes/xq95-per-home-venv/FINDINGS.md``: an editable install
directed at an overlay left the base environment's ``.pth`` count at 75 before
and 75 after.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from amplifier_app_cli.lib.home_env import BASE_PTH_NAME
from amplifier_app_cli.lib.home_env import CONSTRAINTS_NAME
from amplifier_app_cli.lib.home_env import ENABLE_ENV
from amplifier_app_cli.lib.home_env import activate_home_env
from amplifier_app_cli.lib.home_env import base_constraints
from amplifier_app_cli.lib.home_env import base_site_packages
from amplifier_app_cli.lib.home_env import ensure_home_env
from amplifier_app_cli.lib.home_env import env_python
from amplifier_app_cli.lib.home_env import env_site_packages
from amplifier_app_cli.lib.home_env import home_env_root
from amplifier_app_cli.lib.home_env import is_enabled
from amplifier_app_cli.lib.home_env import uv_target_args
from amplifier_app_cli.lib.home_env import write_constraints

PY_TAG = f"py{sys.version_info.major}.{sys.version_info.minor}"


@pytest.fixture
def on(monkeypatch):
    """Per-home environments switched on for this test."""
    monkeypatch.setenv(ENABLE_ENV, "1")


def fake_venv(root: Path) -> Path:
    """Materialize the parts of a venv this module reads, without running uv."""
    if sys.platform == "win32":  # pragma: no cover - CI runs posix
        site_packages = root / "Lib" / "site-packages"
        python = root / "Scripts" / "python.exe"
    else:
        site_packages = root / "lib" / PY_TAG.replace("py", "python") / "site-packages"
        python = root / "bin" / "python"
    site_packages.mkdir(parents=True, exist_ok=True)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    return site_packages


class RecordingRunner:
    """A ``subprocess.run`` stand-in that creates the venv it is asked for."""

    def __init__(self, *, returncode: int = 0, create: bool = True):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.create = create

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if self.create and self.returncode == 0:
            fake_venv(Path(cmd[-1]))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr="")


# --- the switch --------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_is_enabled_accepts_the_usual_truthy_spellings(value):
    assert is_enabled({ENABLE_ENV: value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_is_enabled_rejects_everything_else(value):
    assert is_enabled({ENABLE_ENV: value}) is False


def test_disabled_is_the_default(monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    assert is_enabled() is False


# --- layout ------------------------------------------------------------------


def test_root_is_under_the_home_and_keyed_by_interpreter_version(tmp_path):
    root = home_env_root(tmp_path)
    assert root == tmp_path / "env" / PY_TAG
    # Version-keyed so a base-interpreter upgrade starts a fresh overlay rather
    # than importing 3.13 builds into 3.14.
    assert PY_TAG in root.parts


def test_two_homes_get_two_roots(tmp_path):
    a = home_env_root(tmp_path / "home-a")
    b = home_env_root(tmp_path / "home-b")
    assert a != b
    assert not str(a).startswith(str(b))


def test_env_python_exists_as_a_path_before_the_venv_does(tmp_path):
    python = env_python(home_env_root(tmp_path))
    assert python.name.startswith("python")
    assert not python.exists()


def test_site_packages_is_none_until_the_venv_exists(tmp_path):
    assert env_site_packages(home_env_root(tmp_path)) is None


def test_site_packages_found_once_the_venv_exists(tmp_path):
    root = home_env_root(tmp_path)
    expected = fake_venv(root)
    assert env_site_packages(root) == expected


# --- constraints -------------------------------------------------------------


def test_base_constraints_pin_every_installed_distribution():
    pins = base_constraints()
    assert pins, "the running environment always has distributions installed"
    assert all("==" in pin for pin in pins)
    names = [pin.split("==")[0] for pin in pins]
    assert names == sorted(names), "stable order keeps the file diff-friendly"
    assert len(names) == len(set(names)), "one pin per distribution, no duplicates"


def test_base_constraints_pin_a_distribution_that_is_certainly_present():
    """``pytest`` is running this test, so it is installed by construction."""
    assert any(pin.lower().startswith("pytest==") for pin in base_constraints())


def test_write_constraints_lands_in_the_overlay(tmp_path):
    root = tmp_path / "env"
    root.mkdir()
    path = write_constraints(root)
    assert path == root / CONSTRAINTS_NAME
    assert path.read_text(encoding="utf-8").splitlines() == base_constraints()


# --- creation ----------------------------------------------------------------


def test_ensure_is_a_no_op_while_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    runner = RecordingRunner()
    assert ensure_home_env(tmp_path, runner=runner) is None
    assert runner.calls == [], "nothing may be created while the feature is off"


def test_ensure_creates_the_overlay_with_uv(on, tmp_path):
    runner = RecordingRunner()
    python = ensure_home_env(tmp_path, runner=runner)
    assert python == env_python(home_env_root(tmp_path))
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[:2] == ["uv", "venv"]
    assert cmd[-1] == str(home_env_root(tmp_path))
    # Built from the interpreter Amplifier is running on, so the overlay can
    # import the base environment's packages through the base .pth.
    assert sys.executable in cmd


def test_ensure_writes_the_base_pointer(on, tmp_path):
    ensure_home_env(tmp_path, runner=RecordingRunner())
    site_packages = env_site_packages(home_env_root(tmp_path))
    assert site_packages is not None
    pth = site_packages / BASE_PTH_NAME
    assert pth.read_text(encoding="utf-8").strip() == str(base_site_packages())


def test_ensure_writes_the_constraints_file(on, tmp_path):
    ensure_home_env(tmp_path, runner=RecordingRunner())
    assert (home_env_root(tmp_path) / CONSTRAINTS_NAME).exists()


def test_ensure_is_idempotent_and_does_not_rerun_uv(on, tmp_path):
    runner = RecordingRunner()
    ensure_home_env(tmp_path, runner=runner)
    ensure_home_env(tmp_path, runner=runner)
    assert len(runner.calls) == 1


def test_ensure_refreshes_the_constraints_on_every_call(on, tmp_path):
    ensure_home_env(tmp_path, runner=RecordingRunner())
    constraints = home_env_root(tmp_path) / CONSTRAINTS_NAME
    constraints.write_text("stale==0.0.0\n", encoding="utf-8")
    ensure_home_env(tmp_path, runner=RecordingRunner())
    assert "stale==0.0.0" not in constraints.read_text(encoding="utf-8")


# --- fallback: never fatal ---------------------------------------------------


def test_missing_uv_falls_back_instead_of_raising(on, tmp_path):
    def no_uv(cmd, **kwargs):
        raise FileNotFoundError("uv")

    assert ensure_home_env(tmp_path, runner=no_uv) is None


def test_failed_creation_falls_back_instead_of_raising(on, tmp_path):
    runner = RecordingRunner(returncode=1, create=False)
    assert ensure_home_env(tmp_path, runner=runner) is None


def test_silent_creation_failure_falls_back(on, tmp_path):
    """uv exits 0 but writes nothing -- still a fallback, never a claim."""
    runner = RecordingRunner(returncode=0, create=False)
    assert ensure_home_env(tmp_path, runner=runner) is None


# --- what the call sites splice in -------------------------------------------


def test_uv_args_are_todays_behavior_while_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    assert uv_target_args(tmp_path) == ["--python", sys.executable]


def test_uv_args_target_the_overlay_when_enabled(on, tmp_path):
    args = uv_target_args(tmp_path, runner=RecordingRunner())
    root = home_env_root(tmp_path)
    assert args == [
        "--python",
        str(env_python(root)),
        "--constraint",
        str(root / CONSTRAINTS_NAME),
    ]


def test_uv_args_never_target_the_base_environment_when_enabled(on, tmp_path):
    args = uv_target_args(tmp_path, runner=RecordingRunner())
    assert sys.executable not in args, (
        "an install directed at sys.executable is exactly the collision this fixes"
    )


def test_uv_args_fall_back_to_the_base_environment_when_uv_is_missing(on, tmp_path):
    def no_uv(cmd, **kwargs):
        raise FileNotFoundError("uv")

    # The fallback is deliberate: venv_home_guard turns the resulting cross-home
    # collision into a refusal on the next run, so falling back is recoverable
    # while making a missing uv fatal would not be.
    assert uv_target_args(tmp_path, runner=no_uv) == ["--python", sys.executable]


def test_two_homes_get_two_install_targets(on, tmp_path):
    a = uv_target_args(tmp_path / "home-a", runner=RecordingRunner())
    b = uv_target_args(tmp_path / "home-b", runner=RecordingRunner())
    assert a[1] != b[1]
    assert "home-a" in a[1] and "home-b" in b[1]


# --- activation --------------------------------------------------------------


def test_activate_is_a_no_op_while_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    fake_venv(home_env_root(tmp_path))
    before = list(sys.path)
    assert activate_home_env(tmp_path) is None
    assert sys.path == before


def test_activate_is_a_no_op_when_the_overlay_does_not_exist(on, tmp_path):
    before = list(sys.path)
    assert activate_home_env(tmp_path) is None
    assert sys.path == before


def test_activate_appends_the_overlay(on, tmp_path, monkeypatch):
    site_packages = fake_venv(home_env_root(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    added = activate_home_env(tmp_path)
    assert added == site_packages
    assert str(site_packages) in sys.path


def test_activate_leaves_the_base_environment_ahead_of_the_overlay(
    on, tmp_path, monkeypatch
):
    """Ordering is the reason a shared dependency resolves to one copy.

    ``site.addsitedir`` appends, so a package present in both environments is
    imported from the base one. Constraints then make a *different* version in
    the overlay impossible, which is what keeps that ordering safe rather than
    merely lucky.
    """
    site_packages = fake_venv(home_env_root(tmp_path))
    base = str(base_site_packages())
    monkeypatch.setattr(sys, "path", [base, *sys.path])
    activate_home_env(tmp_path)
    assert sys.path.index(base) < sys.path.index(str(site_packages))


# --- the call sites actually route through the library -----------------------


def test_provider_install_sites_no_longer_name_sys_executable():
    """The three ``uv pip install -e`` call sites this repo owns.

    Named individually rather than grepped for, so a future call site that
    reintroduces ``sys.executable`` is caught by review of this list, not by
    silence.
    """
    from amplifier_app_cli import provider_manager
    from amplifier_app_cli import provider_sources

    for module in (provider_sources, provider_manager):
        source = Path(module.__file__).read_text(encoding="utf-8")
        install_blocks = source.count('"install",')
        assert install_blocks > 0, f"{module.__name__} installs nothing any more?"
        assert "sys.executable" not in source, (
            f"{module.__name__} still hardcodes the base environment as the "
            "install target; route it through home_env.uv_target_args()"
        )
        assert "uv_target_args" in source
