"""LiveEnv: the launch environment is the truth, keys.env follows live, the shell only explains."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.settings_web_routing import RoutingService


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli.commands import routing

    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", lambda: None)
    # never spawn the developer's login shell or a real update/reset from a test
    monkeypatch.setenv("AMPLIFIER_SETTINGS_SHELL_ENV", "off")
    monkeypatch.setenv("AMPLIFIER_SETTINGS_JOBS", "off")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AMPLIFIER_HOME", str(h))
    for name in ("MY_KEY", "LIVE_KEY", "EMPTY_KEY"):
        monkeypatch.delenv(name, raising=False)
    return h


def test_launch_env_wins_then_keys_env_live(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The launch environment is the truth (membership, not truthiness); keys.env follows without restarts."""
    from amplifier_app_cli import settings_web_env as live

    monkeypatch.setenv("LIVE_KEY", "from-launch-0987654321")
    monkeypatch.setenv("EMPTY_KEY", "")  # exported but empty: the runtime keeps "", so do we
    (home / "keys.env").write_text('LIVE_KEY="from-keys-env-1234567890"\nEMPTY_KEY="saved"\n')
    env = live.LiveEnv()
    assert env.refresh({"LIVE_KEY", "EMPTY_KEY"})
    assert os.environ["LIVE_KEY"] == "from-launch-0987654321"
    assert os.environ["EMPTY_KEY"] == ""
    (home / "keys.env").write_text('MY_KEY="added-later-1234567890"\n')  # e.g. `amplifier provider use`
    assert env.refresh({"LIVE_KEY"})
    assert os.environ["MY_KEY"] == "added-later-1234567890"
    assert os.environ["LIVE_KEY"] == "from-launch-0987654321"
    assert not env.refresh({"LIVE_KEY"})  # nothing changed this time
    (home / "keys.env").write_text("")
    assert env.refresh(set())
    assert "MY_KEY" not in os.environ  # left keys.env, never in the launch env: gone
    assert env.shell_value("LIVE_KEY") is None  # no re-read happened


def test_reread_explains_differences_without_touching_the_environment(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web_env as live

    monkeypatch.delenv("AMPLIFIER_SETTINGS_SHELL_ENV")
    monkeypatch.setenv("LIVE_KEY", "direnv-only-1234567890")  # e.g. from direnv or `op run`
    (home / "keys.env").write_text('MY_KEY="same-everywhere-12345"\n')
    shell = {"HOME": str(home), "MY_KEY": "same-everywhere-12345", "EMPTY_KEY": "x"}
    monkeypatch.setattr(live, "login_shell_env", lambda: dict(shell))
    monkeypatch.setattr(live, "where_set", lambda name: [f"~/.zshrc line {len(name)}"])
    env = live.LiveEnv()
    env.refresh({"LIVE_KEY", "MY_KEY", "EMPTY_KEY"})
    out = env.reread({"LIVE_KEY", "MY_KEY", "EMPTY_KEY"})
    assert out["error"] is None
    assert out["differences"] == [
        {"name": "EMPTY_KEY", "launch": "unset", "shell": "set", "where": ["~/.zshrc line 9"]},
        {"name": "LIVE_KEY", "launch": "set", "shell": "unset", "where": []},
    ]
    assert "direnv-only" not in str(out) and "same-everywhere" not in str(out)  # never values
    assert os.environ["LIVE_KEY"] == "direnv-only-1234567890" and "EMPTY_KEY" not in os.environ
    assert env.shell_value("EMPTY_KEY") == "x"
    assert env.info()["reread_at"] and env.info()["differences"] == out["differences"]


def test_shell_reading_can_be_turned_off(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web_env as live

    monkeypatch.setattr(live, "login_shell_env", lambda: pytest.fail("shell read while turned off"))
    env = live.LiveEnv()
    assert "turned off" in (env.reread({"HOME"})["error"] or "")
    assert live.where_set("HOME") == []


def _server(home: Path, tmp_path: Path):
    from types import SimpleNamespace

    from amplifier_app_cli import settings_web_env
    from amplifier_app_cli.settings_web_system import Jobs

    proj = tmp_path / "proj" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))
    return SimpleNamespace(settings=settings, routing=RoutingService(settings), env=settings_web_env.LiveEnv(), jobs=Jobs())


def test_changes_poll_never_runs_the_shell(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web, settings_web_env

    monkeypatch.delenv("AMPLIFIER_SETTINGS_SHELL_ENV")
    monkeypatch.setattr(settings_web_env, "login_shell_env", lambda: pytest.fail("/api/changes ran the login shell"))
    server = _server(home, tmp_path)
    first = settings_web.changes(server, {})["fingerprint"]
    (home / "keys.env").write_text('MY_KEY="polled-1234567890"\n')
    assert settings_web.changes(server, {})["fingerprint"] != first  # keys.env still noticed
    assert os.environ["MY_KEY"] == "polled-1234567890"


def test_change_fingerprint_moves_when_a_file_changes(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web

    server = _server(home, tmp_path)
    first = settings_web.changes(server, {})["fingerprint"]
    assert settings_web.changes(server, {})["fingerprint"] == first
    (home / "settings.yaml").write_text("routing:\n  matrix: economy\n")  # e.g. `amplifier routing use economy`
    assert settings_web.changes(server, {})["fingerprint"] != first
