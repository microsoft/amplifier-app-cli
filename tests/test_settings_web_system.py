"""Status, maintenance jobs, keys and provider schemas behind `amplifier settings`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from amplifier_app_cli import settings_web_system as system
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.settings_web_routing import RoutingService


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli.commands import routing

    def no_fetch() -> None:
        raise AssertionError("the settings page must never fetch the routing bundle")

    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", no_fetch)
    monkeypatch.setenv("AMPLIFIER_SETTINGS_SHELL_ENV", "off")  # no test starts $SHELL
    monkeypatch.delenv("AMPLIFIER_SETTINGS_JOBS", raising=False)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AMPLIFIER_HOME", str(h))
    monkeypatch.delenv("MY_KEY", raising=False)
    return h


def test_mask_shows_at_most_a_quarter() -> None:
    assert system.mask("sk-ant-0123456789abcdef") == "sk-…ef"  # 23 characters -> 5 shown
    for n in (16, 17, 40, 200):
        v = "".join(chr(65 + i % 26) for i in range(n))
        shown = len(system.mask(v)) - 1  # minus the ellipsis
        assert shown <= n // 4 and shown <= 10
    assert "abc" not in system.mask("abc")
    assert set(system.mask("fifteen-chars-x")) == {"•"}


@pytest.mark.parametrize("name", ["api_key", "apikey", "apiKey", "access_token", "aws_secret_access_key", "private_key", "subscription_key", "secret_key", "credentials", "credential", "password", "passphrase", "token", "PGPASSWORD", "NGROK_AUTHTOKEN", "dbpassword", "accesstoken"])
def test_secret_names_match(name: str) -> None:
    assert system.SECRET_FIELD.search(name)
    assert system.SECRET_NAME.search(name.upper())


@pytest.mark.parametrize("name", ["turkey", "max_tokens", "max_output_tokens", "monkey", "keyboard", "base_url", "default_model", "tokens", "priority"])
def test_non_secret_names_do_not_match(name: str) -> None:
    assert not system.SECRET_FIELD.search(name)
    assert not system.SECRET_NAME.search(name.upper())


def test_env_refs_use_the_runtime_pattern() -> None:
    assert system.env_refs("${A}/x/${B:fallback}/${C:}") == ["A", "B", "C"]
    assert system.env_refs("plain") == []


def test_defaults_come_from_the_cli() -> None:
    from amplifier_app_cli.lib.bundle_loader.discovery import DEFAULT_BUNDLE
    from amplifier_app_cli.utils.settings_manager import DEFAULT_SETTINGS

    d = system.defaults()
    assert d["bundle"] == DEFAULT_BUNDLE
    assert d["update_check_hours"] == DEFAULT_SETTINGS["updates"]["check_frequency_hours"]
    assert d["matrix"] == "balanced" and d["ntfy_server"] == "https://ntfy.sh"


def test_reveal_only_for_keys_the_page_lists(home: Path) -> None:
    (home / "keys.env").write_text('MY_KEY="secret-value"\nOTHER="x"\n')
    assert system.reveal_key("MY_KEY", {"MY_KEY"}) == "secret-value"
    with pytest.raises(system.SettingsError):
        system.reveal_key("OTHER", {"MY_KEY"})  # not offered by the page
    with pytest.raises(system.SettingsError):
        system.reveal_key("../etc", {"../etc"})


class FakeProc:
    def __init__(self, code: int | None) -> None:
        self.code = code

    def poll(self) -> int | None:
        return self.code


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Record Popen calls instead of running amplifier; each process has exited 0."""
    calls: list[dict] = []

    def fake(argv: list[str], **kw: object) -> FakeProc:
        calls.append({"argv": argv, **kw})
        return FakeProc(0)

    monkeypatch.setattr(system.subprocess, "Popen", fake)
    return calls


def test_jobs_off_refuses_update_and_reset(home: Path, popen: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMPLIFIER_SETTINGS_JOBS", "off")
    jobs = system.Jobs(cwd=str(home))
    assert not jobs.enabled() and jobs.info()["reason"]
    for kind in ("update", "reset"):
        with pytest.raises(system.SettingsError) as exc:
            jobs.start(kind, {"full": True})
        assert exc.value.status == 403
    jobs.start("update-check", {})
    jobs.start("reset-plan", {"remove": ["cache"]})
    assert len(popen) == 2


def test_jobs_run_in_the_server_cwd_and_log_to_one_private_dir(home: Path, tmp_path: Path, popen: list[dict]) -> None:
    jobs = system.Jobs(cwd=str(tmp_path))
    job = jobs.start("update-check", {})
    assert popen[0]["cwd"] == str(tmp_path)
    log_dir = Path(job["log"]).parent
    assert not log_dir.is_relative_to(home)  # reset --full deletes the home; the log must survive it
    assert log_dir.stat().st_mode & 0o777 == 0o700
    jobs.start("update-check", {})
    assert Path(jobs.get(next(reversed(jobs._jobs)))["log"]).parent == log_dir  # one folder per launch
    assert system.Jobs(cwd=str(tmp_path)).log_dir is None  # nothing created until a job runs


def test_reset_needs_a_matching_finished_preview(home: Path, popen: list[dict]) -> None:
    jobs = system.Jobs(cwd=str(home))
    with pytest.raises(system.SettingsError) as exc:
        jobs.start("reset", {"full": True})
    assert exc.value.status == 409
    plan = jobs.start("reset-plan", {"remove": ["registry", "cache"]})
    with pytest.raises(system.SettingsError):  # a different reset than the one previewed
        jobs.start("reset", {"full": True, "plan_id": plan["id"]})
    with pytest.raises(system.SettingsError):  # an update-check is not a preview
        jobs.start("reset", {"remove": ["cache", "registry"], "plan_id": jobs.start("update-check", {})["id"]})
    assert not jobs.restart_required
    jobs.start("reset", {"remove": ["cache", "registry"], "plan_id": plan["id"]})  # same set, other order
    assert popen[-1]["argv"][-3:] == ["--remove", "cache,registry", "-y"]
    assert jobs.restart_required and jobs.info()["restart_required"]


def test_failed_or_running_preview_does_not_open_the_gate(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for code in (1, None):
        monkeypatch.setattr(system.subprocess, "Popen", lambda argv, code=code, **kw: FakeProc(code))
        jobs = system.Jobs(cwd=str(home))
        plan = jobs.start("reset-plan", {"full": True})
        with pytest.raises(system.SettingsError) as exc:
            jobs.start("reset", {"full": True, "plan_id": plan["id"]})
        assert exc.value.status == 409


def test_jobs_only_build_allow_listed_commands() -> None:
    base = [sys.executable, "-m", "amplifier_app_cli"]
    assert system.Jobs.argv("update-check", {}) == [*base, "update", "--check-only"]
    assert system.Jobs.argv("reset-plan", {"remove": ["cache", "registry", "rm -rf /"]}) == [
        *base, "reset", "--remove", "cache,registry", "-y", "--dry-run",
    ]  # fmt: skip
    assert system.Jobs.argv("reset", {"full": True}) == [*base, "reset", "--full", "-y"]
    with pytest.raises(system.SettingsError):
        system.Jobs.argv("reset", {"remove": []})
    with pytest.raises(system.SettingsError):
        system.Jobs.argv("shell", {})


def test_reset_categories_report_sizes(home: Path) -> None:
    (home / "cache").mkdir()
    (home / "cache" / "blob").write_bytes(b"x" * 2048)
    cats = {c["name"]: c for c in system.reset_info()["categories"]}
    assert cats["cache"]["bytes"] == 2048 and cats["cache"]["default_remove"]
    assert not cats["projects"]["default_remove"]


def test_provider_schema_comes_only_from_the_providers_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import provider_loader

    declared = {
        "provider-demo": {
            "config_fields": [
                {"id": "api_key", "display_name": "API Key", "field_type": "secret", "prompt": "Key", "env_var": "X"},
                {"id": "effort", "display_name": "Effort", "field_type": "choice", "choices": ["low", "high"], "default": None},
            ]
        },
    }
    calls: list[tuple[str, object]] = []

    def fake_info(module_id: str, *, source_path: Path | None = None) -> dict | None:
        calls.append((module_id, source_path))
        if module_id == "provider-broken":
            raise RuntimeError("source not activated")
        return declared.get(module_id)

    monkeypatch.setattr(provider_loader, "get_provider_info", fake_info)

    schema = system.provider_schema("provider-demo")
    assert schema == {
        "fields": [
            {"id": "api_key", "display_name": "API Key", "field_type": "secret", "prompt": "Key"},
            {"id": "effort", "display_name": "Effort", "field_type": "choice", "choices": ["low", "high"]},
        ],
        "origin": "provider",
    }
    assert system.provider_schema("provider-unknown") == {"fields": [], "origin": "none"}
    assert system.provider_schema("provider-broken") == {"fields": [], "origin": "none", "error": "RuntimeError: source not activated"}
    assert all(path is None for _, path in calls)  # always the loader's own source selection


def _settings(home: Path, tmp_path: Path, doc: dict) -> AppSettings:
    (home / "settings.yaml").write_text(yaml.safe_dump(doc))
    proj = tmp_path / "p" / ".amplifier"
    proj.mkdir(parents=True, exist_ok=True)
    return AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))


def _status(settings: AppSettings, keys: list[dict] | None = None) -> dict:
    return system.status(settings, RoutingService(settings), keys or [], settings.get_merged_settings())


def test_status_flags_missing_keys_and_uses_cli_defaults(home: Path, tmp_path: Path) -> None:
    cfg = {"api_key": "${MY_KEY}", "base_url": "${UNSET_BASE_URL}"}
    st = _status(_settings(home, tmp_path, {"config": {"providers": [{"id": "a", "module": "provider-anthropic", "config": cfg}]}}))
    assert st["providers"][0]["missing_keys"] == ["MY_KEY"]
    assert st["routing"]["matrix"] == system.defaults()["matrix"]
    assert st["bundle"]["active"] == system.defaults()["bundle"]  # (no matrices cached: never fetched)


def test_default_provider_is_the_lowest_priority(home: Path, tmp_path: Path) -> None:
    providers = [
        {"id": "first", "module": "provider-openai", "config": {"priority": 5}},
        {"id": "best", "module": "provider-anthropic", "config": {"priority": 1}},
        {"id": "unset", "module": "provider-ollama"},  # 100, as the CLI assumes
    ]
    st = _status(_settings(home, tmp_path, {"config": {"providers": providers}}))
    assert {p["name"]: p["default"] for p in st["providers"]} == {"first": False, "best": True, "unset": False}


def test_environment_value_shadowing_a_saved_key_is_flagged(home: Path, tmp_path: Path) -> None:
    """Amplifier never overrides an exported variable, so a stale one silently wins."""
    settings = _settings(home, tmp_path, {"config": {"providers": [{"id": "a", "module": "provider-anthropic", "config": {"api_key": "${MY_KEY:}"}}]}})
    st = _status(settings, [{"name": "MY_KEY", "stored": True, "in_env": True, "shadowed": True}])
    assert st["providers"][0]["shadowed_keys"] == ["MY_KEY"] and st["providers"][0]["missing_keys"] == []


def test_notify_test_needs_a_channel(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMPLIFIER_NTFY_TOPIC", raising=False)
    with pytest.raises(system.SettingsError):
        system.notify_test({})


def test_notify_topic_comes_from_the_environment_only(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    (home / "keys.env").write_text('AMPLIFIER_NTFY_TOPIC="from-keys-file"\n')
    monkeypatch.delenv("AMPLIFIER_NTFY_TOPIC", raising=False)
    with pytest.raises(system.SettingsError):
        system.notify_test({})  # keys.env alone isn't what the hook reads
    sent: list[str] = []

    class Resp:
        status = 200

        def __enter__(self) -> "Resp":
            return self

        def __exit__(self, *a: object) -> None:
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: sent.append(req.full_url) or Resp())
    monkeypatch.setenv("AMPLIFIER_NTFY_TOPIC", "from-env")
    monkeypatch.setenv("AMPLIFIER_NTFY_SERVER", "https://ntfy.example.test")
    system.notify_test({})
    # the hook's order: its config `server`, then AMPLIFIER_NTFY_SERVER, then ntfy.sh
    system.notify_test({"config": {"notifications": {"ntfy": {"server": "https://mine.example.test/"}}}})
    monkeypatch.delenv("AMPLIFIER_NTFY_SERVER")
    system.notify_test({})
    assert sent == ["https://ntfy.example.test/from-env", "https://mine.example.test/from-env", "https://ntfy.sh/from-env"]


@pytest.mark.parametrize(
    ("field", "value", "secret"),
    [
        ("api_key", "sk-live-123", True),
        ("api_key", "${MY_KEY}", False),
        ("webhook", "https://user:hunter2@hooks.example/x", True),
        ("webhook", "https://user:${PASS}@hooks.example/x", False),
        ("base_url", "https://api.example/v1", False),
        ("base_url", "https://me@api.example/v1", False),
    ],
)
def test_secret_values_include_url_passwords(field: str, value: str, secret: bool) -> None:
    assert system.is_secret_value(field, value) is secret
