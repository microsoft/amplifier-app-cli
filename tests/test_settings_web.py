"""Tests for `amplifier settings` (local web settings editor)."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest
import yaml

from types import SimpleNamespace

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.settings_web import (
    MASKED,
    SettingsError,
    SettingsServer,
    build_state,
    save_key,
    save_scope,
    scope_raw,
    validate_doc,
)
from amplifier_app_cli.settings_web_routing import RoutingService


@pytest.fixture(autouse=True)
def _no_shell_no_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spawn the developer's login shell or a real update/reset from a test."""
    monkeypatch.setenv("AMPLIFIER_SETTINGS_SHELL_ENV", "off")
    monkeypatch.setenv("AMPLIFIER_SETTINGS_JOBS", "off")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AMPLIFIER_HOME", str(h))
    return h


@pytest.fixture
def settings(home: Path, tmp_path: Path) -> AppSettings:
    proj = tmp_path / "proj" / ".amplifier"
    proj.mkdir(parents=True)
    return AppSettings(
        SettingsPaths(
            global_settings=home / "settings.yaml",
            project_settings=proj / "settings.yaml",
            local_settings=proj / "settings.local.yaml",
        )
    )


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI fetches the routing bundle when none is cached; tests stay offline."""
    from amplifier_app_cli.commands import routing

    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", lambda: None)


def _srv(settings: AppSettings) -> SimpleNamespace:
    from amplifier_app_cli.settings_web_env import LiveEnv
    from amplifier_app_cli.settings_web_system import Jobs

    return SimpleNamespace(settings=settings, routing=RoutingService(settings), env=LiveEnv(), jobs=Jobs())


def _state(settings: AppSettings) -> dict:
    return build_state(_srv(settings))


def _global(state: dict) -> dict:
    return next(s for s in state["scopes"] if s["name"] == "global")


def test_validate_doc_rejects_shapes_the_cli_chokes_on() -> None:
    assert validate_doc({"anything": {"unknown": 1}}) == []
    assert validate_doc([1]) != []
    errs = validate_doc(
        {"config": {"providers": [{"id": "a", "module": "provider-x"}, {"id": "a"}]}}
    )
    assert any("needs a module" in e for e in errs)
    assert any("share the id 'a'" in e for e in errs)
    assert validate_doc({"bundle": {"app": "not-a-list"}}) != []


def test_round_trip_preserves_unknown_keys(settings: AppSettings) -> None:
    original = {"custom_thing": {"nested": [1, 2]}, "routing": {"matrix": "balanced"}}
    settings.paths.global_settings.write_text(yaml.safe_dump(original))
    g = _global(_state(settings))
    save_scope(_srv(settings), {"scope": "global", "doc": g["doc"], "version": g["version"]})
    assert yaml.safe_load(settings.paths.global_settings.read_text()) == original


def test_stale_version_is_a_conflict_not_an_overwrite(settings: AppSettings) -> None:
    settings.paths.global_settings.write_text("a: 1\n")
    g = _global(_state(settings))
    settings.paths.global_settings.write_text("a: 2\n")  # someone else wrote it
    with pytest.raises(SettingsError) as exc:
        save_scope(_srv(settings), {"scope": "global", "doc": {"a": 3}, "version": g["version"]}
        )
    assert exc.value.status == 409
    assert settings.paths.global_settings.read_text() == "a: 2\n"


def test_yaml_save_and_invalid_yaml(settings: AppSettings) -> None:
    save_scope(_srv(settings), {"scope": "project", "yaml": "routing:\n  matrix: economy\n", "version": ""})
    assert yaml.safe_load(settings.paths.project_settings.read_text()) == {"routing": {"matrix": "economy"}}
    with pytest.raises(SettingsError) as exc:
        save_scope(_srv(settings), {"scope": "global", "yaml": "a: [", "version": ""})
    assert exc.value.status == 422


def test_session_scope_not_editable(settings: AppSettings) -> None:
    with pytest.raises(SettingsError):
        save_scope(_srv(settings), {"scope": "session", "doc": {}})


def test_pasted_secret_never_lands_in_yaml(settings: AppSettings, home: Path) -> None:
    doc = {
        "config": {
            "providers": [
                {
                    "id": "my-llm",
                    "module": "provider-does-not-exist",
                    "config": {"api_key": "sk-literal-123"},
                }
            ]
        }
    }
    with pytest.raises(SettingsError) as exc:  # keys go through /api/key, never the settings file
        save_scope(_srv(settings), {"scope": "global", "doc": doc, "version": ""})
    assert exc.value.status == 422 and "config.providers.0.config.api_key" in str(exc.value)
    assert "sk-literal-123" not in str(exc.value)
    assert not settings.paths.global_settings.exists()
    assert "sk-literal-123" not in ((home / "keys.env").read_text() if (home / "keys.env").exists() else "")


def test_key_values_are_write_only(settings: AppSettings, home: Path) -> None:
    st = save_key(_srv(settings), {"name": "MY_TOKEN", "value": "secret-value"})
    assert "secret-value" in (home / "keys.env").read_text()
    assert "secret-value" not in json.dumps(st, default=str)
    assert {"name": "MY_TOKEN", "stored": True}.items() <= next(
        k for k in st["keys"] if k["name"] == "MY_TOKEN"
    ).items()
    for bad in (
        {"name": "bad name", "value": "x"},
        {"name": "OK", "value": "a\nB=1"},
        {"name": "OK", "value": ""},
    ):
        with pytest.raises(SettingsError):
            save_key(_srv(settings), bad)


def test_state_masks_literal_secrets_and_save_puts_them_back(settings: AppSettings) -> None:
    settings.paths.global_settings.write_text(
        "config:\n  providers:\n"
        "  - module: provider-a\n    config:\n      api_key: sk-literal-AAAAAAAAAAAA\n      max_tokens: 100\n"
        "  - module: provider-b\n    config:\n      api_key: sk-literal-BBBBBBBBBBBB\n      base_url: ${B_URL:http://x}\n"
        "  - module: provider-c\n    config:\n      api_key: ${C_API_KEY}\n"
    )
    st = _state(settings)
    blob = json.dumps(st, default=str)
    assert "sk-literal" not in blob
    assert not {"effective", "cwd"} & st.keys() and "yaml" not in _global(st)
    assert {"defaults", "env"} <= st.keys() and st["env"]["source"] == "launch"
    g = _global(st)
    assert g["masked"] == ["config.providers.0.config.api_key", "config.providers.1.config.api_key"]
    providers = g["doc"]["config"]["providers"]
    assert providers[0]["config"] == {"api_key": MASKED, "max_tokens": 100}
    assert providers[2]["config"]["api_key"] == "${C_API_KEY}"  # references are not secrets
    assert {"B_URL", "C_API_KEY"} <= {k["name"] for k in st["keys"]}  # ${VAR:default} is a reference too

    # remove provider-a: provider-b moves to index 0 and must keep ITS key, not a's
    save_scope(_srv(settings), {"scope": "global", "doc": {"config": {"providers": providers[1:]}}, "version": g["version"]})
    saved = yaml.safe_load(settings.paths.global_settings.read_text())["config"]["providers"]
    # these modules can't load here, so the CLI leaves their literals in place: the restore must
    # still have put b's own key back, not a's
    assert saved[0]["config"]["api_key"] == "sk-literal-BBBBBBBBBBBB"
    assert "sk-literal-AAAAAAAAAAAA" not in settings.paths.global_settings.read_text()


def test_masked_value_with_nothing_on_disk_is_422(settings: AppSettings) -> None:
    doc = {"config": {"providers": [{"module": "provider-new", "config": {"api_key": MASKED}}]}}
    with pytest.raises(SettingsError) as exc:
        save_scope(_srv(settings), {"scope": "global", "doc": doc, "version": ""})
    assert exc.value.status == 422
    assert not settings.paths.global_settings.exists()


def test_version_is_the_hash_of_the_bytes_parsed_and_raw_is_on_request(settings: AppSettings) -> None:
    import hashlib

    raw = b"notifications:\n  token: literal-token-value\n"
    settings.paths.global_settings.write_bytes(raw)
    g = _global(_state(settings))
    assert g["version"] == hashlib.sha256(raw).hexdigest()
    assert g["doc"] == {"notifications": {"token": MASKED}}  # any depth, any secret-named field
    assert scope_raw(_srv(settings), {"scope": "global"}) == {"yaml": raw.decode(), "version": g["version"]}


def test_from_home_project_and_local_are_unavailable(settings: AppSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web

    monkeypatch.setattr(settings_web, "is_running_from_home", lambda: True)
    st = _state(settings)
    by = {s["name"]: s for s in st["scopes"]}
    assert by["global"]["available"] and not by["project"]["available"] and not by["local"]["available"]
    assert by["project"]["unavailable_reason"]
    with pytest.raises(SettingsError) as exc:
        save_scope(_srv(settings), {"scope": "project", "doc": {"a": 1}, "version": ""})
    assert exc.value.status == 422
    assert not settings.paths.project_settings.exists()


def test_value_error_from_write_scope_is_422(settings: AppSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(scope, doc):
        raise ValueError("No free name for that key.")

    monkeypatch.setattr(settings, "write_scope", boom)
    with pytest.raises(SettingsError) as exc:
        save_scope(_srv(settings), {"scope": "global", "doc": {"a": 1}, "version": ""})
    assert exc.value.status == 422 and "No free name" in str(exc.value)


# ---------------- HTTP ----------------


@pytest.fixture
def server(settings: AppSettings):
    srv = SettingsServer(port=0, settings=settings)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _req(srv: SettingsServer, method: str, path: str, headers: dict | None = None, body: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r, data


def _session(srv: SettingsServer) -> dict:
    r, data = _req(srv, "POST", "/api/session", body={"token": srv.launch_token})
    assert r.status == 200
    return {"X-Amplifier-Settings": json.loads(data)["secret"]}


def test_assets_are_public_and_api_needs_the_session_secret(server: SettingsServer) -> None:
    assert "/#t=" in server.launch_url
    for path in ("/", "/app.js", "/settings.css", "/lib/api.js"):
        r, _ = _req(server, "GET", path)
        assert r.status == 200, path
        assert r.getheader("Set-Cookie") is None
    r, data = _req(server, "GET", "/api/state")
    assert r.status == 401 and json.loads(data)["relaunch"] is True

    ok = _session(server)
    r, data = _req(server, "GET", "/api/state", ok)
    assert r.status == 200 and "scopes" in json.loads(data)
    assert "script-src 'self'" in (r.getheader("Content-Security-Policy") or "")
    assert _req(server, "GET", "/api/state", {"X-Amplifier-Settings": "wrong"})[0].status == 401
    for bad in ({"Host": "evil.example:80"}, {"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert _req(server, "GET", "/api/state", {**ok, **bad})[0].status == 403
        assert _req(server, "GET", "/app.js", bad)[0].status == 403

    body = {"scope": "global", "doc": {"a": 1}, "version": ""}
    assert _req(server, "POST", "/api/scope", {}, body)[0].status == 401
    assert _req(server, "POST", "/api/scope", ok, body)[0].status == 200


def test_launch_token_works_once(server: SettingsServer) -> None:
    token = server.launch_token
    r, data = _req(server, "POST", "/api/session", body={"token": "é" + token})  # non-ASCII: refused, no crash
    assert r.status == 401
    r, data = _req(server, "POST", "/api/session", body={"token": token})
    assert r.status == 200 and json.loads(data)["secret"] == server.secret
    for again in (token, "", None):
        r, data = _req(server, "POST", "/api/session", body={"token": again})
        assert r.status == 401 and json.loads(data)["relaunch"] is True
    assert server.launch_token is None


def test_refused_request_body_never_becomes_a_request(server: SettingsServer, home: Path) -> None:
    """Blocker 1: a refused POST's unread body, pipelined on one socket, must not run."""
    import socket

    me = server.host_header
    inner_body = json.dumps({"name": "SMUGGLED_PROOF", "value": "written-by-a-refused-origin"}).encode()
    # even carrying the real secret, a request hidden in a refused body must never execute
    inner = (
        f"POST /api/key HTTP/1.1\r\nHost: {me}\r\nX-Amplifier-Settings: {server.secret}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(inner_body)}\r\n\r\n"
    ).encode() + inner_body

    def refused(body: bytes) -> bytes:
        return (
            f"POST /nothing HTTP/1.1\r\nHost: {me}\r\nOrigin: http://127.0.0.1:9999\r\n"
            f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode() + body

    s = socket.create_connection(server.server_address, timeout=10)
    s.sendall(refused(inner) + refused(inner))  # two refused POSTs, pipelined
    received = b""
    while chunk := s.recv(65536):  # the server closes after the first answer
        received += chunk
    s.close()
    assert received.count(b"HTTP/1.") == 1 and received.startswith(b"HTTP/1.0 403")
    assert not (home / "keys.env").exists() or "SMUGGLED_PROOF" not in (home / "keys.env").read_text()


def test_restart_required_answers_503_except_jobs(server: SettingsServer, monkeypatch: pytest.MonkeyPatch) -> None:
    ok = _session(server)
    from amplifier_app_cli.settings_web_system import Jobs

    monkeypatch.setattr(Jobs, "restart_required", property(lambda self: True))
    r, data = _req(server, "GET", "/api/state", ok)
    assert r.status == 503 and json.loads(data)["restart"] is True
    assert _req(server, "POST", "/api/key", ok, {"name": "A_KEY", "value": "x"})[0].status == 503
    assert _req(server, "GET", "/api/jobs?id=nope", ok)[0].status == 404  # still answered


def test_env_reread_is_explicit_and_can_be_off(server: SettingsServer, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web_env

    monkeypatch.setattr(settings_web_env, "login_shell_env", lambda: pytest.fail("shell read without a re-read"))
    ok = _session(server)
    for path in ("/api/state", "/api/state?fresh=1", "/api/changes"):
        assert _req(server, "GET", path, ok)[0].status == 200
    assert _req(server, "POST", "/api/providers/diagnose", ok, {"name": "nope"})[0].status != 500
    r, data = _req(server, "POST", "/api/env/reread", ok, {})
    assert r.status == 200 and "turned off" in json.loads(data)["error"]


def test_static_assets_ship_with_package() -> None:
    ui = Path(__file__).parents[1] / "amplifier_app_cli" / "data" / "settings_ui"
    for name in ("settings.html", "settings.css", "app.js"):
        assert (ui / name).is_file()
    # every module app.js (transitively) imports exists, so the page can't 404 on a module
    import re

    seen, todo = set(), ["app.js"]
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        path = ui / rel
        assert path.is_file(), rel
        for spec in re.findall(r"""from\s+["'](\.{1,2}/[^"']+)["']""", path.read_text()):
            todo.append(str((path.parent / spec).resolve().relative_to(ui.resolve())))
    assert "<script>" not in (ui / "settings.html").read_text()  # CSP: no inline script


def test_shutdown_never_waits_for_a_request_stuck_on_a_provider(server: SettingsServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl+C must stop the page even while a request waits on a provider that never answers."""
    import time

    ok = _session(server)
    stuck, release = threading.Event(), threading.Event()
    monkeypatch.setattr(server.routing, "models", lambda provider: stuck.set() or release.wait(30) or {})
    threading.Thread(target=_req, args=(server, "GET", "/api/routing/models?provider=x", ok), daemon=True).start()
    assert stuck.wait(10)
    started = time.monotonic()
    server.shutdown()
    server.server_close()
    assert time.monotonic() - started < 2
    release.set()
