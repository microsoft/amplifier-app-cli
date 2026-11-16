"""Status, maintenance jobs, keys and provider schemas behind `amplifier settings`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web_env
    from amplifier_app_cli.commands import routing

    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", lambda: None)
    monkeypatch.setenv("AMPLIFIER_SETTINGS_SHELL_ENV", "off")  # no test starts $SHELL

    def no_shell(*a: object, **kw: object) -> None:
        raise AssertionError("diagnose must not start a login shell")

    monkeypatch.setattr(settings_web_env, "_run_shell", no_shell)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AMPLIFIER_HOME", str(h))
    monkeypatch.delenv("MY_KEY", raising=False)
    return h



def test_diagnose_explains_each_step_and_never_leaks_keys(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli.settings_web_diagnose import diagnose

    (home / "keys.env").write_text('DEMO_KEY="saved-value-abcdef123456"\n')
    monkeypatch.setenv("DEMO_KEY", "shell-value-zyxwvu987654")
    (home / "settings.yaml").write_text(
        yaml.safe_dump({"config": {"providers": [{"id": "demo", "module": "provider-nothing-here", "config": {"api_key": "${DEMO_KEY}", "base_url": "${DEMO_URL}"}}]}})
    )
    proj = tmp_path / "p3" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))
    report = diagnose(settings, "demo", FakeEnv())  # no re-read yet: nothing asks the shell
    steps = {s["name"]: s for s in report["steps"]}
    assert steps["Provider code"]["status"] == "fail"
    assert any("provider install" in t for t in report["tips"])
    assert "DEMO_URL" in steps["Settings"]["summary"]  # unset variable is named
    assert steps["API key"]["status"] == "warn" and "two different values" in steps["API key"]["summary"]
    wheres = [s["where"] for s in steps["API key"]["sources"]]
    assert wheres == ["keys.env", "your environment (the shell, launchctl or the program that started this page)"]
    assert steps["Key check"]["status"] == "skip" and steps["Through Amplifier"]["status"] == "skip"
    assert "saved-value" not in str(report) and "shell-value" not in str(report)
    assert diagnose(settings, "missing")["steps"][0]["status"] == "fail"


class FakeEnv:
    """LiveEnv as the contract has it: shell_value() is None until the user asks for a re-read."""

    def __init__(self, shell: dict[str, str] | None = None) -> None:
        self.shell = shell or {}

    def shell_value(self, name: str) -> str | None:
        return self.shell.get(name)


def _demo_settings(home: Path, tmp_path: Path) -> AppSettings:
    (home / "settings.yaml").write_text(
        yaml.safe_dump({"config": {"providers": [{"id": "demo", "module": "provider-nothing-here", "config": {"api_key": "${DEMO_KEY}"}}]}})
    )
    proj = tmp_path / "p4" / ".amplifier"
    proj.mkdir(parents=True)
    return AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))


def test_diagnose_key_check_reads_status_from_exception_chain(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from amplifier_app_cli import provider_loader, settings_web_env
    from amplifier_app_cli.settings_web_diagnose import diagnose

    saved, shell = "saved-value-abcdef123456", "shell-value-zyxwvu987654"
    (home / "keys.env").write_text(f'DEMO_KEY="{saved}"\n')
    monkeypatch.setenv("DEMO_KEY", shell)
    monkeypatch.setattr(settings_web_env, "where_set", lambda var: ["~/.zshrc:12"])
    monkeypatch.setattr(provider_loader, "load_provider_class", lambda module: object)
    calls: list[str] = []

    class HTTPStatusError(Exception):
        status_code = 401
        response = SimpleNamespace(request=SimpleNamespace(url="https://api.example.test/v1/models"))

    class AuthenticationError(Exception):
        pass

    def fake_models(module: str, collected_config: dict, require_catalog: bool = False) -> list:
        key = collected_config["api_key"]
        calls.append(key)
        if key == saved:
            return [SimpleNamespace(id="model-a"), SimpleNamespace(id="model-b")]
        raise AuthenticationError(f"Incorrect API key provided: {shell}") from HTTPStatusError("401 Unauthorized")

    monkeypatch.setattr(provider_loader, "get_provider_models", fake_models)
    report = diagnose(_demo_settings(home, tmp_path), "demo", FakeEnv({"DEMO_KEY": shell}))  # after a re-read
    steps = {s["name"]: s for s in report["steps"]}
    assert calls == [shell, saved, "${DEMO_KEY}"]  # each candidate, then as configured
    check = steps["Key check"]
    assert check["status"] == "warn"
    assert [r["http"] for r in check["results"]] == [401, 200]
    assert check["results"][1]["models"] == 2
    assert "https://api.example.test/v1/models" in check["summary"]
    assert steps["Through Amplifier"]["status"] == "fail" and "HTTP 401" in steps["Through Amplifier"]["summary"]
    assert "~/.zshrc:12" in report["tips"][0] and "keys.env works" in report["tips"][0]
    assert saved not in str(report) and shell not in str(report)


def test_diagnose_all_ok_when_key_works(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from amplifier_app_cli import provider_loader
    from amplifier_app_cli.settings_web_diagnose import diagnose

    (home / "keys.env").write_text('DEMO_KEY="saved-value-abcdef123456"\n')
    monkeypatch.delenv("DEMO_KEY", raising=False)
    monkeypatch.setattr(provider_loader, "load_provider_class", lambda module: object)
    monkeypatch.setattr(provider_loader, "get_provider_models", lambda module, collected_config, require_catalog=False: [SimpleNamespace(id="m")])
    report = diagnose(_demo_settings(home, tmp_path), "demo")
    assert [s["status"] for s in report["steps"]] == ["ok", "ok", "ok", "ok", "ok"]
    assert [s["name"] for s in report["steps"]] == ["Provider code", "Settings", "API key", "Key check", "Through Amplifier"]
    assert "Everything checks out" in report["tips"][0]
    assert "saved-value" not in str(report)


def test_exported_but_empty_variable_is_what_amplifier_uses(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli.settings_web_diagnose import diagnose

    (home / "keys.env").write_text('DEMO_KEY="saved-value-abcdef123456"\n')
    monkeypatch.setenv("DEMO_KEY", "")  # set, so the runtime never loads keys.env's value
    report = diagnose(_demo_settings(home, tmp_path), "demo")
    key = next(s for s in report["steps"] if s["name"] == "API key")
    assert key["status"] == "fail" and "set but empty" in key["summary"]


def test_settings_step_reads_defaults_like_the_runtime(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli.settings_web_diagnose import diagnose

    monkeypatch.delenv("DEMO_URL", raising=False)
    (home / "settings.yaml").write_text(
        yaml.safe_dump({"config": {"providers": [{"id": "demo", "module": "provider-nothing-here", "config": {"base_url": "${DEMO_URL:http://localhost:9/v1}"}}]}})
    )
    proj = tmp_path / "p5" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))
    step = next(s for s in diagnose(settings, "demo")["steps"] if s["name"] == "Settings")
    assert step["summary"] == "Calls http://localhost:9/v1."
    assert step["config"]["base_url"]["resolved"] == "http://localhost:9/v1"
