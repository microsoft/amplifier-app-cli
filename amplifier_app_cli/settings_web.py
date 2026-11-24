"""Local web UI for Amplifier settings (`amplifier settings`).

The browser edits one *scope document* at a time -- the parsed YAML of the
global, project or local settings file -- and saves the whole document back
through ``AppSettings.write_scope``, the same funnel every CLI command uses.
So atomic writes and plaintext-secret normalization apply exactly as they do
for `amplifier provider`. Every form on the page is a view over that document.

Safety model:
  * binds 127.0.0.1 only, random port by default; HTTP/1.0, one request per
    connection, so an unread request body can never become a second request
  * the page and its scripts are public; every /api request needs the session
    secret in the X-Amplifier-Settings header. The page gets that secret once,
    by posting the single-use launch token from the printed link's #fragment to
    /api/session, and keeps it in sessionStorage (scoped to this origin and
    port). No cookies.
  * Host must match (blocks DNS rebinding); Origin and Sec-Fetch-Site must be
    same-origin
  * strict CSP with no inline script or style; responses are never cached
  * saves carry the sha256 of the exact bytes the page was given; a file that
    changed underneath is a 409, never a silent overwrite
  * secret-named values in settings documents are replaced by MASKED and put
    back from disk on save; the raw file is sent only when the File page asks
    (/api/scope/raw); key values are masked, one is revealed only when asked
  * after an update or reset has run, the page must be relaunched (503)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import yaml
from amplifier_foundation.paths.resolution import get_amplifier_home

from . import settings_web_system as system
from .lib.env_vars import ENV_PATTERN
from .lib.settings import AppSettings, Scope
from .paths import is_running_from_home
from .provider_sources import DEFAULT_PROVIDER_SOURCES
from .settings_web_diagnose import diagnose
from .settings_web_env import LiveEnv
from .settings_web_routing import RoutingService
from .settings_web_system import SettingsError

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent / "data" / "settings_ui"
ASSET = re.compile(r"^/((?:[a-z0-9_-]+/)*[a-z0-9_-]+\.(js|css|html))$")
ASSET_TYPES = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "html": "text/html; charset=utf-8"}
EDITABLE_SCOPES: tuple[Scope, ...] = ("global", "project", "local")
MASKED = "•••• hidden"
HOME_SCOPE_REASON = "You're in your home folder, where project and local settings would be your global file. Run `amplifier settings` from a project folder to edit them."
EXPIRED = "This settings page has expired. Run `amplifier settings` again to open a new one."
RESTART = "Amplifier was updated or reset. Run amplifier settings again."
MAX_BODY = 1_000_000
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


# --------------------------------------------------------------------------
# Settings model (plain functions over AppSettings; testable without HTTP)
# --------------------------------------------------------------------------


def read_scope_file(path: Path) -> tuple[bytes | None, dict[str, Any], str | None]:
    """(raw bytes, doc, error) from ONE read, so doc and version always agree."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, {}, None
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        return raw, {}, f"{path} is not valid YAML: {e}"
    if not isinstance(data, dict):
        return raw, {}, f"{path} must contain a mapping at the top level"
    return raw, data, None


def version_of(raw: bytes | None) -> str:
    """Content hash of a scope file's bytes; "" when it does not exist."""
    return hashlib.sha256(raw).hexdigest() if raw is not None else ""


def scope_docs(settings: AppSettings) -> list[dict[str, Any]]:
    """The parsed global, project and local documents (broken files read as empty)."""
    return [read_scope_file(settings.get_scope_path(s))[1] for s in EDITABLE_SCOPES]


def placeholders(value: Any) -> set[str]:
    """Every ${VAR} / ${VAR:default} referenced anywhere in a settings value."""
    if isinstance(value, str):
        return set(system.env_refs(value))
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return set().union(*(placeholders(v) for v in items))


is_literal_secret = system.is_secret_value


def mask_secrets(doc: Any, path: str = "") -> tuple[Any, list[str]]:
    """A copy of doc with every literal secret replaced by MASKED, plus their dotted paths."""
    if not isinstance(doc, (dict, list)):
        return doc, []
    out: Any = {} if isinstance(doc, dict) else []
    masked: list[str] = []
    for k, v in doc.items() if isinstance(doc, dict) else enumerate(doc):
        where = f"{path}.{k}" if path else str(k)
        if isinstance(doc, dict) and is_literal_secret(k, v):
            new = MASKED
            masked.append(where)
        else:
            new, inner = mask_secrets(v, where)
            masked += inner
        if isinstance(out, dict):
            out[k] = new
        else:
            out.append(new)
    return out, masked


_MISSING = object()


def _ident(entry: Any) -> Any:
    """How validate_doc tells list entries apart (providers): id, else module."""
    return (entry.get("id") or entry.get("module")) if isinstance(entry, dict) else None


def _counterpart(old: Any, key: Any, new: Any) -> Any:
    """The on-disk value matching new[key]; list entries are matched by identity, not index."""
    if isinstance(old, dict):
        return old.get(key, _MISSING)
    if isinstance(old, list):
        if _ident(new) is not None:
            return next((o for o in old if _ident(o) == _ident(new)), _MISSING)
        return old[key] if key < len(old) else _MISSING
    return _MISSING


def unmask_secrets(doc: Any, old: Any, path: str = "") -> None:
    """Put back, in place, each MASKED value from the on-disk document."""
    if not isinstance(doc, (dict, list)):
        return
    for k, v in list(doc.items() if isinstance(doc, dict) else enumerate(doc)):
        where = f"{path}.{k}" if path else str(k)
        before = _counterpart(old, k, v)
        if v == MASKED:
            if not isinstance(before, str):
                raise SettingsError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    f"{where} was hidden on this page and isn't in the file any more. Type the value again.",
                )
            doc[k] = before
        else:
            unmask_secrets(v, before, where)


def validate_doc(doc: Any) -> list[str]:
    """Reject only the shapes the CLI would choke on; unknown keys pass through."""
    if not isinstance(doc, dict):
        return ["Settings must be a mapping (key: value) at the top level."]
    errs: list[str] = []
    bundle = doc.get("bundle")
    if bundle is not None and not isinstance(bundle, dict):
        errs.append("bundle must be a mapping.")
    elif isinstance(bundle, dict):
        if "active" in bundle and not isinstance(bundle["active"], str):
            errs.append("bundle.active must be a bundle name.")
        app = bundle.get("app")
        if app is not None and not (isinstance(app, list) and all(isinstance(u, str) for u in app)):
            errs.append("bundle.app must be a list of bundle URIs.")
        added = bundle.get("added")
        if added is not None and not (isinstance(added, dict) and all(isinstance(v, str) for v in added.values())):
            errs.append("bundle.added must map names to URIs.")
    config = doc.get("config")
    if config is not None and not isinstance(config, dict):
        errs.append("config must be a mapping.")
    elif isinstance(config, dict) and "providers" in config:
        providers = config["providers"]
        if not isinstance(providers, list):
            errs.append("config.providers must be a list.")
        else:
            seen: set[str] = set()
            for i, p in enumerate(providers):
                where = f"config.providers[{i}]"
                if not isinstance(p, dict):
                    errs.append(f"{where} must be a mapping.")
                    continue
                if not isinstance(p.get("module"), str) or not p["module"]:
                    errs.append(f"{where} needs a module (e.g. provider-anthropic).")
                if "config" in p and not isinstance(p["config"], dict):
                    errs.append(f"{where}.config must be a mapping.")
                ident = _ident(p)
                if isinstance(ident, str):
                    if ident in seen:
                        errs.append(f"Two providers share the id '{ident}'.")
                    seen.add(ident)
    if doc.get("routing") is not None and not isinstance(doc["routing"], dict):
        errs.append("routing must be a mapping.")
    return errs


def scope_available(scope: Scope) -> bool:
    """The CLI's rule (ui/scope.py): from the home folder only the global scope exists.

    Paths are resolved too, so a symlinked home (or AMPLIFIER_HOME) can't make the
    project file and the global file the same file under two names.
    """
    if scope == "global":
        return True
    project_dir = (Path.cwd() / ".amplifier").resolve()
    return not is_running_from_home() and project_dir != get_amplifier_home().resolve()


def key_list(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keys the page shows: everything in keys.env plus every ${VAR} settings use.

    os.environ has been synced by LiveEnv: the launch value if there was one,
    else keys.env's. Membership counts, so an exported-but-empty variable is set.
    """
    stored = system.read_keys_file()
    referenced = set().union(*(placeholders(d) for d in docs))
    out = []
    for name in sorted(referenced | set(stored)):
        value = stored[name] if name in stored else os.environ.get(name)
        out.append(
            {
                "name": name,
                "stored": name in stored,
                "in_env": name not in stored and name in os.environ,
                # Amplifier never overrides a variable already in the environment, so a
                # different launch value silently wins over the saved key
                "shadowed": name in stored and name in os.environ and os.environ[name] != stored[name],
                "referenced": name in referenced,
                "secret": bool(system.SECRET_NAME.search(name)),
                "preview": system.mask(value) if value else None,
            }
        )
    return out


def sync_env(server: Any) -> None:
    """Bring os.environ up to date with keys.env (never runs a shell)."""
    names = set().union(*(placeholders(d) for d in scope_docs(server.settings))) | {system.NTFY_TOPIC}
    if server.env.refresh(names):
        server.routing.refresh()  # values changed: model lists must be fetched again


def build_state(server: Any) -> dict[str, Any]:
    """Everything the page shows."""
    sync_env(server)
    settings = server.settings
    scopes = []
    for scope in EDITABLE_SCOPES:
        path = settings.get_scope_path(scope)
        raw, doc, error = read_scope_file(path)
        doc, masked = mask_secrets(doc)
        available = scope_available(scope)
        scopes.append(
            {
                "name": scope,
                "path": str(path),
                "exists": raw is not None,
                "available": available,
                "unavailable_reason": None if available else HOME_SCOPE_REASON,
                "version": version_of(raw),
                "doc": doc,
                "masked": masked,
                "error": error,
            }
        )
    keys = key_list(scope_docs(settings))
    defaults = system.defaults()
    try:
        status = system.status(settings, server.routing, keys, settings.get_merged_settings())
    except Exception as e:  # noqa: BLE001 -- home must render even when status can't
        status = {"error": f"Couldn't work out Amplifier's status: {e}"}
    status["jobs"] = server.jobs.info()
    return {
        "home": str(get_amplifier_home()),
        "scopes": scopes,
        "keys": keys,
        "status": status,
        "defaults": defaults,
        "env": server.env.info(),
        "known_providers": [{"module": m, "source": s} for m, s in sorted(DEFAULT_PROVIDER_SOURCES.items())],
    }


def _literal_secrets(doc: Any, path: str = "") -> list[tuple[str, str]]:
    """(dotted path, value) of every literal secret in doc, at any depth."""
    if not isinstance(doc, (dict, list)):
        return []
    out = []
    for k, v in doc.items() if isinstance(doc, dict) else enumerate(doc):
        where = f"{path}.{k}" if path else str(k)
        out += [(where, v)] if isinstance(doc, dict) and is_literal_secret(k, v) else _literal_secrets(v, where)
    return out


def save_scope(server: Any, body: dict[str, Any]) -> dict[str, Any]:
    settings = server.settings
    scope = body.get("scope")
    if scope not in EDITABLE_SCOPES:
        raise SettingsError(HTTPStatus.BAD_REQUEST, f"Unknown scope: {scope!r}")
    scope = cast(Scope, scope)
    if not scope_available(scope):
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, HOME_SCOPE_REASON)
    if "yaml" in body:
        try:
            doc = yaml.safe_load(body["yaml"] or "") or {}
        except yaml.YAMLError as e:
            raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, f"Not valid YAML: {e}") from e
    else:
        doc = body.get("doc")
    errors = validate_doc(doc)
    if errors:
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Settings not saved.", errors=errors)
    doc = cast(dict[str, Any], doc)
    path = settings.get_scope_path(scope)
    with settings.scope_lock(scope):
        raw, on_disk, _ = read_scope_file(path)
        if version_of(raw) != (body.get("version") or ""):
            raise SettingsError(
                HTTPStatus.CONFLICT,
                f"{path.name} changed since you opened it (another command or editor wrote it). "
                "Reload to see the current settings.",
            )
        # Keys reach keys.env through /api/key, never through a settings file. A literal
        # already on disk may stay (it comes back MASKED and is restored); a new one is refused.
        known = {v for _, v in _literal_secrets(on_disk)}
        new = [where for where, v in _literal_secrets(doc) if v != MASKED and v not in known]
        if new:
            raise SettingsError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "Keys can't be typed into the settings file. Save the key on the Keys page "
                f"and write ${{NAME}} here instead ({', '.join(new)}).",
            )
        unmask_secrets(doc, on_disk)
        try:
            moved = settings.write_scope(scope, doc)  # moves literal provider keys to keys.env
        except ValueError as e:  # e.g. no free name for a key
            raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, str(e)) from e
    server.routing.refresh()  # providers may have changed: re-check model lists
    return {**build_state(server), "moved_keys": moved}


def scope_raw(server: Any, q: dict[str, str]) -> dict[str, Any]:
    """The file as written, for the File page only (literal secrets included)."""
    scope = q.get("scope")
    if scope not in EDITABLE_SCOPES:
        raise SettingsError(HTTPStatus.BAD_REQUEST, f"Unknown scope: {scope!r}")
    raw = read_scope_file(server.settings.get_scope_path(cast(Scope, scope)))[0]
    return {"yaml": (raw or b"").decode("utf-8", errors="replace"), "version": version_of(raw)}


def save_key(server: Any, body: dict[str, Any]) -> dict[str, Any]:
    from .key_manager import KeyManager

    name = str(body.get("name") or "").strip()
    value = str(body.get("value") or "")
    if not system.ENV_NAME.match(name):
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Key names use letters, digits and underscores, e.g. ANTHROPIC_API_KEY.")
    if not value.strip() or any(c in value for c in "\r\n\"'"):
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Paste the key on its own: one line, no quotes.")
    KeyManager().save_key(name, value.strip())
    server.routing.refresh()  # a new key can make a provider's models listable
    return build_state(server)


def changes(server: Any, q: dict[str, str]) -> dict[str, Any]:
    """A fingerprint of everything the page shows; it polls this to stay current. Never runs a shell."""
    sync_env(server)
    home = get_amplifier_home()
    parts = [version_of(read_scope_file(server.settings.get_scope_path(s))[0]) for s in EDITABLE_SCOPES]
    parts.append(version_of(read_scope_file(home / "keys.env")[0]))
    parts += [f"{f.name}:{f.stat().st_mtime_ns}" for f in sorted((home / "routing").glob("*.yaml"))]
    parts.append(server.env.fingerprint)
    return {"fingerprint": hashlib.sha256("|".join(parts).encode()).hexdigest()}


def env_reread(server: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Compare a new login shell with the launch environment. Explains; never changes os.environ."""
    names = placeholders(scope_docs(server.settings)) | set(system.read_keys_file()) | {system.NTFY_TOPIC}
    return server.env.reread(names)


def routing_models(server: Any, q: dict[str, str]) -> dict[str, Any]:
    sync_env(server)
    return server.routing.models(q.get("provider", ""))


def diagnose_provider(server: Any, body: dict[str, Any]) -> dict[str, Any]:
    sync_env(server)
    return diagnose(server.settings, str(body.get("name") or ""), server.env)


def reveal_key(server: Any, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "")
    return {"value": system.reveal_key(name, {k["name"] for k in key_list(scope_docs(server.settings))})}


# --------------------------------------------------------------------------
# HTTP layer: one route table, one place that turns errors into responses
# --------------------------------------------------------------------------

Handler = Callable[["SettingsServer", Any], dict[str, Any]]
ROUTES: dict[tuple[str, str], Handler] = {
    ("GET", "/api/state"): lambda s, q: build_state(s),
    ("GET", "/api/changes"): changes,
    ("GET", "/api/scope/raw"): scope_raw,
    ("GET", "/api/reset/categories"): lambda s, q: system.reset_info(),
    ("GET", "/api/provider-schema"): lambda s, q: system.provider_schema(q.get("module", "")),
    ("GET", "/api/jobs"): lambda s, q: s.jobs.get(q.get("id", "")),
    ("GET", "/api/routing/matrices"): lambda s, q: {"matrices": s.routing.matrices()},
    ("GET", "/api/routing/models"): routing_models,
    ("POST", "/api/scope"): save_scope,
    ("POST", "/api/key"): save_key,
    ("POST", "/api/key/reveal"): reveal_key,
    ("POST", "/api/env/reread"): env_reread,
    ("POST", "/api/jobs"): lambda s, b: s.jobs.start(str(b.get("kind")), b.get("options") if isinstance(b.get("options"), dict) else {}),
    ("POST", "/api/notify/test"): lambda s, b: system.notify_test(s.settings.get_merged_settings()),
    ("POST", "/api/providers/diagnose"): diagnose_provider,
    ("POST", "/api/routing/resolve"): lambda s, b: s.routing.resolve(str(b.get("matrix") or ""), b.get("overrides") or {}, bool(b.get("live"))),
    ("POST", "/api/routing/matrix"): lambda s, b: s.routing.save_matrix(b),
    ("POST", "/api/routing/matrix/delete"): lambda s, b: s.routing.delete_matrix(str(b.get("name") or "")),
}


class _RequestHandler(BaseHTTPRequestHandler):
    # protocol_version stays the stdlib's HTTP/1.0: one request per connection.
    server: SettingsServer  # type: ignore[assignment]
    server_version, sys_version = "amplifier-settings", ""  # no Python version in headers

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Errors the stdlib raises itself (bad request line, unknown method): JSON with our headers."""
        self._json(code, {"error": message or HTTPStatus(code).phrase})

    def log_message(self, format: str, *args: Any) -> None:  # quiet unless --verbose
        if self.server.verbose:
            super().log_message(format, *args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        if status >= 400:
            self.close_connection = True  # whatever is left unread on the socket is discarded
        self.send_response(status)
        for k, v in {
            "Content-Type": ctype,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": CSP,
        }.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, default=str).encode(), "application/json")

    def _allowed(self) -> bool:
        h = self.headers
        return (
            h.get("Host") == self.server.host_header
            and h.get("Origin") in (None, self.server.origin)
            and h.get("Sec-Fetch-Site") in (None, "same-origin", "none")
        )

    def _authed(self) -> bool:
        sent = self.headers.get("X-Amplifier-Settings", "").encode("utf-8", "surrogatepass")
        return hmac.compare_digest(sent, self.server.secret.encode())

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise SettingsError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large.")
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as e:  # bad length, bad JSON, bad UTF-8
            raise SettingsError(HTTPStatus.BAD_REQUEST, "Expected a JSON object.") from e
        if not isinstance(body, dict):
            raise SettingsError(HTTPStatus.BAD_REQUEST, "Expected a JSON object.")
        return body

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        if not self._allowed():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Request refused."})
        if not url.path.startswith("/api/"):  # the page itself: public, holds nothing
            asset = ASSET.match("/settings.html" if url.path == "/" else url.path) if method == "GET" else None
            if asset and (UI_DIR / asset.group(1)).is_file():
                return self._send(HTTPStatus.OK, (UI_DIR / asset.group(1)).read_bytes(), ASSET_TYPES[asset.group(2)])
            if method == "GET" and url.path == "/favicon.ico":
                return self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        try:
            if (method, url.path) == ("POST", "/api/session"):
                return self._json(HTTPStatus.OK, {"secret": self.server.exchange(self._body().get("token"))})
            if not self._authed():
                return self._json(HTTPStatus.UNAUTHORIZED, {"error": EXPIRED, "relaunch": True})
            if self.server.jobs.restart_required and (method, url.path) != ("GET", "/api/jobs"):
                return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": RESTART, "restart": True})
            route = ROUTES.get((method, url.path))
            if route is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            arg: Any = {k: v[0] for k, v in parse_qs(url.query).items()} if method == "GET" else self._body()
            self._json(HTTPStatus.OK, route(self.server, arg))
        except SettingsError as e:
            self._json(e.status, {"error": str(e), **e.extra})
        except Exception as e:  # noqa: BLE001 -- always answer with something readable
            logger.exception("settings page request failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Something went wrong: {type(e).__name__}: {e}"})


class SettingsServer(ThreadingHTTPServer):
    # Request threads are daemons and never joined, so Ctrl+C can't wait on one
    # that is stuck talking to a slow provider.
    daemon_threads = True
    block_on_close = False

    def __init__(self, port: int = 0, settings: AppSettings | None = None, verbose: bool = False) -> None:
        super().__init__(("127.0.0.1", port), _RequestHandler)
        self.settings = settings or AppSettings()
        self.routing = RoutingService(self.settings)
        self.jobs = system.Jobs(cwd=os.getcwd())
        self.env = LiveEnv()
        self.verbose = verbose
        self.launch_token: str | None = secrets.token_urlsafe(24)
        self.secret = secrets.token_urlsafe(32)
        self._exchange_lock = threading.Lock()
        port = self.server_address[1]
        self.host_header = f"127.0.0.1:{port}"
        self.origin = f"http://{self.host_header}"
        # the token rides in the #fragment, so it never appears in a request line
        self.launch_url = f"{self.origin}/#t={self.launch_token}"

    def exchange(self, token: Any) -> str:
        """Trade the launch token for the session secret, once."""
        with self._exchange_lock:
            expected = self.launch_token
            if expected is None or not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8", "surrogatepass"), expected.encode()):
                raise SettingsError(HTTPStatus.UNAUTHORIZED, EXPIRED, relaunch=True)
            self.launch_token = None
            return self.secret
