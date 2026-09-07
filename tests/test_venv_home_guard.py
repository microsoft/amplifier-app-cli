"""AMPLIFIER_HOME must not silently repoint another home's editable installs.

FAIL-BEFORE: every test here fails against the shipped (unfixed) CLI, because
nothing in it ever looked at the environment's ``.pth`` files before installing.

The fixture below is not invented. It is the state captured in a Digital Twin
Universe on 2026-09-07 (``docs/lanes/q99f-amplifier-home-pth-bug/``): one shared
uv tool venv, ``AMPLIFIER_HOME=/root/home-a`` run first, then
``AMPLIFIER_HOME=/root/home-b`` -- after which nine ``_editable_impl_*.pth``
files that had pointed into ``/root/home-a/cache`` pointed into
``/root/home-b/cache`` instead, with no warning of any kind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_app_cli.lib.venv_home_guard import OVERRIDE_ENV
from amplifier_app_cli.lib.venv_home_guard import SharedVenvHomeError
from amplifier_app_cli.lib.venv_home_guard import detect_home_conflict
from amplifier_app_cli.lib.venv_home_guard import enforce_home_ownership
from amplifier_app_cli.lib.venv_home_guard import format_conflict
from amplifier_app_cli.lib.venv_home_guard import home_for_target
from amplifier_app_cli.lib.venv_home_guard import is_exempt
from amplifier_app_cli.lib.venv_home_guard import read_editable_entries

# The nine provider modules whose .pth files flipped in the DTU reproduction,
# with the cache-entry hashes exactly as captured.
FLIPPED_IN_DTU = {
    "_editable_impl_amplifier_module_provider_anthropic.pth": "cache/amplifier-module-provider-anthropic-5181591dcf06d076",
    "_editable_impl_amplifier_module_provider_azure_openai.pth": "cache/amplifier-module-provider-azure-openai-922834ab260b519e",
    "_editable_impl_amplifier_module_provider_chat_completions.pth": "cache/amplifier-module-provider-chat-completions-5872434b7d7c24df",
    "_editable_impl_amplifier_module_provider_gemini.pth": "cache/amplifier-module-provider-gemini-06d1437d03d6b064",
    "_editable_impl_amplifier_module_provider_github_copilot.pth": "cache/amplifier-module-provider-github-copilot-ed66ecbd21ea6054",
    "_editable_impl_amplifier_module_provider_ollama.pth": "cache/amplifier-module-provider-ollama-014c2047033c455b",
    "_editable_impl_amplifier_module_provider_openai.pth": "cache/amplifier-module-provider-openai-e2c4b8c10222ed8c",
    "_editable_impl_amplifier_module_provider_openai_chatgpt.pth": "cache/amplifier-module-provider-openai-chatgpt-36f6cbc7a2650e2e",
    "_editable_impl_amplifier_module_provider_vllm.pth": "cache/amplifier-module-provider-vllm-ac98bf87e319447e",
}

# A bundle module lives one level deeper, under `modules/<name>` -- the shape
# that produced the "the two disagree about whose code runs" line in run B.
NESTED_IN_DTU = {
    "_editable_impl_amplifier_module_tool_recipes.pth": "cache/amplifier-bundle-recipes-2b1e350432fea9ba/modules/tool-recipes",
}


def _write_site_packages(
    site_packages: Path, home: Path, entries: dict[str, str]
) -> None:
    site_packages.mkdir(parents=True, exist_ok=True)
    for name, relative in entries.items():
        (site_packages / name).write_text(f"{home / relative}\n", encoding="utf-8")


@pytest.fixture
def two_homes(tmp_path: Path) -> tuple[Path, Path, Path]:
    """home_a (owns the venv), home_b (the scratch home), shared site-packages."""
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    site_packages = tmp_path / "uv-tools" / "amplifier" / "site-packages"
    home_a.mkdir()
    home_b.mkdir()
    _write_site_packages(
        site_packages, home_a, {**FLIPPED_IN_DTU, **NESTED_IN_DTU}
    )
    return home_a, home_b, site_packages


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------


def test_second_home_is_refused_before_anything_is_installed(two_homes):
    """The scratch-home run must stop, not silently repoint home-a's installs."""
    home_a, home_b, site_packages = two_homes

    with pytest.raises(SharedVenvHomeError) as excinfo:
        enforce_home_ownership(
            ["run", "hi"],
            current_home=home_b,
            site_packages=site_packages,
            env={},
        )

    conflict = excinfo.value.conflict
    assert len(conflict.foreign) == len(FLIPPED_IN_DTU) + len(NESTED_IN_DTU)
    assert conflict.foreign_homes == (home_a.resolve(),)


def test_the_home_that_owns_the_venv_runs_normally(two_homes):
    """No conflict, no refusal, for the home the editable installs belong to."""
    home_a, _home_b, site_packages = two_homes

    assert (
        enforce_home_ownership(
            ["run", "hi"], current_home=home_a, site_packages=site_packages, env={}
        )
        is None
    )
    assert detect_home_conflict(current_home=home_a, site_packages=site_packages) is None


def test_fresh_environment_is_not_a_conflict(tmp_path: Path):
    """A venv with no Amplifier-owned editable installs must never trip."""
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    (site_packages / "unrelated.pth").write_text("/opt/other/lib\n", encoding="utf-8")

    assert (
        detect_home_conflict(
            current_home=tmp_path / "home", site_packages=site_packages
        )
        is None
    )


def test_dev_checkout_editables_are_not_claimed_by_any_home(tmp_path: Path):
    """`uv pip install -e ~/dev/my-module` must not be read as a home's cache."""
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    (site_packages / "_editable_impl_mine.pth").write_text(
        f"{tmp_path / 'dev' / 'amplifier-module-mine'}\n", encoding="utf-8"
    )

    assert read_editable_entries(site_packages) == []
    assert (
        detect_home_conflict(
            current_home=tmp_path / "home", site_packages=site_packages
        )
        is None
    )


# ---------------------------------------------------------------------------
# home_for_target: which paths a home actually owns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target,expected",
    [
        ("/root/home-a/cache/amplifier-module-provider-openai-e2c4b8c10222ed8c", "/root/home-a"),
        (
            "/root/home-a/cache/amplifier-bundle-recipes-2b1e350432fea9ba/modules/tool-recipes",
            "/root/home-a",
        ),
        ("/home/u/.amplifier/cache/amplifier-foundation-c909465861f9d6ce", "/home/u/.amplifier"),
        # No 16-hex suffix -> not a foundation cache entry.
        ("/home/u/.amplifier/cache/something-else", None),
        # No cache segment at all.
        ("/home/u/dev/amplifier-module-provider-openai", None),
        ("/opt/site-packages/whatever", None),
    ],
)
def test_home_for_target(target: str, expected: str | None):
    result = home_for_target(Path(target))
    assert result == (Path(expected) if expected else None)


def test_pth_import_lines_are_skipped(tmp_path: Path):
    """setuptools finder glue is executable, not a path -- do not half-parse it."""
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    (site_packages / "__editable__.thing-0.1.0.pth").write_text(
        "import __editable___thing_finder; __editable___thing_finder.install()\n",
        encoding="utf-8",
    )

    assert read_editable_entries(site_packages) == []


# ---------------------------------------------------------------------------
# Escape hatches: the guard must be passable, and must not block the repair
# ---------------------------------------------------------------------------


def test_override_downgrades_refusal_to_a_returned_conflict(two_homes):
    """With the override set, the caller continues -- and still gets told."""
    _home_a, home_b, site_packages = two_homes

    conflict = enforce_home_ownership(
        ["run", "hi"],
        current_home=home_b,
        site_packages=site_packages,
        env={OVERRIDE_ENV: "1"},
    )

    assert conflict is not None
    assert len(conflict.foreign) == len(FLIPPED_IN_DTU) + len(NESTED_IN_DTU)


@pytest.mark.parametrize("argv", [["version"], ["reset", "--remove", "cache", "-y"], ["--help"], ["--version"], ["run", "-h"]])
def test_diagnostic_and_repair_paths_stay_reachable(two_homes, argv):
    """Blocking `reset` would make the recommended repair unreachable."""
    _home_a, home_b, site_packages = two_homes

    assert is_exempt(argv) is True
    assert (
        enforce_home_ownership(
            argv, current_home=home_b, site_packages=site_packages, env={}
        )
        is None
    )


@pytest.mark.parametrize("argv", [["run", "hi"], [], ["--verbose", "run", "hi"], ["bundle", "add", "x"]])
def test_session_paths_are_guarded(argv):
    assert is_exempt(argv) is False


# ---------------------------------------------------------------------------
# The message has to be actionable, not just loud
# ---------------------------------------------------------------------------


def test_message_names_both_homes_the_env_and_the_remedies(two_homes):
    home_a, home_b, site_packages = two_homes
    conflict = detect_home_conflict(
        current_home=home_b, site_packages=site_packages
    )
    assert conflict is not None

    message = format_conflict(conflict)

    assert str(home_a.resolve()) in message
    assert str(home_b.resolve()) in message
    assert str(site_packages) in message
    # Names the fix, rather than leaving the user to diagnose a .pth collision.
    assert OVERRIDE_ENV in message
    assert "UV_TOOL_DIR" in message
    assert "amplifier reset --remove cache" in message
    # Quotes the incident that motivated the guard.
    assert "61 of 63" in message


def test_message_flags_a_foreign_home_that_no_longer_exists(two_homes):
    """The repair case: the scratch home under /tmp has already been cleaned."""
    home_a, home_b, site_packages = two_homes
    home_a.rmdir()

    conflict = detect_home_conflict(
        current_home=home_b, site_packages=site_packages
    )
    assert conflict is not None
    assert "no longer exists" in format_conflict(conflict)


# ---------------------------------------------------------------------------
# CLI wiring: the refusal has to reach the user before click dispatches
# ---------------------------------------------------------------------------


def test_cli_entrypoint_refuses_and_exits_nonzero(two_homes, monkeypatch, capsys):
    """`amplifier run` must exit 1, not install into someone else's venv."""
    import sys

    from amplifier_app_cli.lib import venv_home_guard
    from amplifier_app_cli.main import _guard_shared_venv_home

    home_a, home_b, site_packages = two_homes
    monkeypatch.setattr(venv_home_guard, "site_packages_dir", lambda: site_packages)
    monkeypatch.setenv("AMPLIFIER_HOME", str(home_b))
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["amplifier", "run", "hi"])

    with pytest.raises(SystemExit) as excinfo:
        _guard_shared_venv_home()

    assert excinfo.value.code == 1
    output = capsys.readouterr().out
    assert "Refusing to run" in output
    assert str(home_a.resolve()) in output


def test_cli_entrypoint_warns_and_continues_under_override(
    two_homes, monkeypatch, capsys
):
    import sys

    from amplifier_app_cli.lib import venv_home_guard
    from amplifier_app_cli.main import _guard_shared_venv_home

    _home_a, home_b, site_packages = two_homes
    monkeypatch.setattr(venv_home_guard, "site_packages_dir", lambda: site_packages)
    monkeypatch.setenv("AMPLIFIER_HOME", str(home_b))
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    monkeypatch.setattr(sys, "argv", ["amplifier", "run", "hi"])

    _guard_shared_venv_home()

    output = capsys.readouterr().out
    assert "AMPLIFIER_HOME changed" in output
