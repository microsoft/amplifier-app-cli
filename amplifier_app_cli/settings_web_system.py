"""Machine-level pieces of the settings page: keys, provider schemas, status, jobs.

* Key values are masked everywhere; one is revealed only on request.
* Provider settings come only from the provider's own ConfigField declarations.
* Update and reset run the real `amplifier update` / `amplifier reset` from a
  fixed allow-list, in the server's working directory. They replace the program
  serving the page, so they start detached and log to a private temp
  folder made on the first job (not the Amplifier home: reset --full deletes it). AMPLIFIER_SETTINGS_JOBS=off turns them off.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, TypeVar

from amplifier_foundation.paths.resolution import get_amplifier_home

from .commands import reset as reset_cli
from .lib.env_vars import ENV_PATTERN

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Ends in a secret word. Only bare "key" needs a word boundary (monkey, turkey);
# max_tokens / max_output_tokens end in "tokens", so they never match.
_SECRET_WORDS = r"((^|_)key|apikey|token|secret|password|passphrase|credentials?)$"
SECRET_FIELD = re.compile(_SECRET_WORDS, re.IGNORECASE)  # config field names
SECRET_NAME = re.compile(_SECRET_WORDS, re.IGNORECASE)  # environment variable names
URL_CREDENTIALS = re.compile(r"://[^/?#@\s:]*:[^/?#@\s]+@")  # https://user:password@host


def is_secret_value(field: Any, value: Any) -> bool:
    """A literal (not only ${...}) secret: under a secret-named field, or a URL with a password."""
    if not isinstance(value, str):
        return False
    literal = ENV_PATTERN.sub("", value)
    named = isinstance(field, str) and bool(SECRET_FIELD.search(field))
    return bool(literal.strip()) and named or bool(URL_CREDENTIALS.search(literal))


DEFAULT_MATRIX = "balanced"  # what `amplifier routing` falls back to (commands/routing.py)
DEFAULT_NTFY_SERVER = "https://ntfy.sh"  # hooks-notify-push's default
T = TypeVar("T")


def env_refs(value: str) -> list[str]:
    """Every variable a value references, as the runtime expands it: ${VAR} and ${VAR:default}."""
    return [m.group(1) for m in ENV_PATTERN.finditer(value)]


def defaults() -> dict[str, Any]:
    """What the CLI uses when settings don't say, from the CLI's own constants."""
    from .lib.bundle_loader.discovery import DEFAULT_BUNDLE
    from .utils.settings_manager import DEFAULT_SETTINGS

    return {
        "bundle": DEFAULT_BUNDLE,
        "update_check_hours": DEFAULT_SETTINGS["updates"]["check_frequency_hours"],
        "matrix": DEFAULT_MATRIX,
        "ntfy_server": DEFAULT_NTFY_SERVER,
    }


class SettingsError(Exception):
    """An error the page can show as-is, with the HTTP status to send."""

    def __init__(self, status: HTTPStatus, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


def run_with_timeout(fn: Callable[[], T], timeout: float) -> T:
    """fn() on a daemon thread; TimeoutError if it takes longer. A hung call is left behind
    and never joined, so Ctrl+C doesn't wait on a provider that isn't answering."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 -- re-raised in the caller
            box["error"] = e

    t = threading.Thread(target=run, name="settings-provider-call", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------------------------------------------------------------- keys
def mask(value: str) -> str:
    """At most a quarter of the value (ends only, never more than 10 characters); dots under 16."""
    v = value.strip()
    if len(v) < 16:
        return "•" * min(max(len(v), 4), 10)
    shown = min(len(v) // 4, 10)
    return f"{v[: shown - shown // 2]}…{v[len(v) - shown // 2 :]}"


def read_keys_file() -> dict[str, str]:
    """name -> value saved in keys.env (independent of the environment)."""
    from .key_manager import KeyManager

    return KeyManager().stored_items()


def reveal_key(name: str, allowed: set[str]) -> str:
    """Full value of one key the page lists. Never a bulk read."""
    if ENV_NAME.match(name) and name in allowed:
        value = read_keys_file().get(name) or os.environ.get(name)
        if value:
            return value
    raise SettingsError(HTTPStatus.NOT_FOUND, "There's no saved value for that key.")


# ---------------------------------------------------------------- provider schema
_FIELD_KEYS = ("id", "display_name", "field_type", "prompt", "choices", "default")


def provider_schema(module_id: str) -> dict[str, Any]:
    """{fields, origin[, error]}: the provider's own ConfigField declarations, nothing inferred.

    Loaded through the CLI's normal provider discovery; a load failure is reported, not hidden.
    """
    from .provider_loader import get_provider_info

    try:
        info = get_provider_info(module_id) or {}
    except Exception as e:  # noqa: BLE001 -- shown on the page instead of an empty form
        return {"fields": [], "origin": "none", "error": f"{type(e).__name__}: {e}"[:300]}
    fields = [{k: f[k] for k in _FIELD_KEYS if f.get(k) is not None} for f in info.get("config_fields") or [] if f.get("id")]
    return {"fields": fields, "origin": "provider" if fields else "none"}


# ---------------------------------------------------------------- reset
def _size(path: Path, budget_s: float = 4.0) -> int | None:
    """Bytes under path; None if it takes too long to measure (shown as "large")."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total, start = 0, time.time()
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
        if time.time() - start > budget_s:
            return None
    return total


def reset_info() -> dict[str, Any]:
    """Reset's own categories, with sizes, plus what reset never touches."""
    home = get_amplifier_home()
    cats = []
    for cat in reset_cli.CATEGORY_ORDER:
        paths = [home / p for p in reset_cli.RESET_CATEGORIES[cat]]
        sizes = [_size(p) for p in paths]
        cats.append(
            {
                "name": cat,
                "description": reset_cli.CATEGORY_DESCRIPTIONS[cat],
                "exists": any(p.exists() for p in paths),
                "bytes": None if None in sizes else sum(s or 0 for s in sizes),
                "default_remove": cat in reset_cli.DEFAULT_REMOVE,
            }
        )
    return {"categories": cats, "unmanaged": reset_cli.unmanaged_files(), "home": str(home)}


# ---------------------------------------------------------------- jobs
MUTATING = ("update", "reset")


class Jobs:
    """Runs allow-listed amplifier commands and keeps their output."""

    def __init__(self, cwd: str | None = None) -> None:
        self.cwd = cwd or os.getcwd()  # the server's directory, so update sees the project's bundles
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Outside the Amplifier home: `reset --full` deletes that home, including the
        # log of the reset that is running. Created on the first job, not per launch.
        self.log_dir: Path | None = None

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("AMPLIFIER_SETTINGS_JOBS", "").strip().lower() not in ("0", "off", "false", "no")

    @property
    def restart_required(self) -> bool:
        """True once an update or reset has exited: the program serving this page may be gone."""
        return any(j["mutating"] and j["proc"].poll() is not None for j in self._jobs.values())

    def info(self) -> dict[str, Any]:
        on = self.enabled()
        return {"enabled": on, "reason": None if on else "Update and reset are turned off here (AMPLIFIER_SETTINGS_JOBS).", "restart_required": self.restart_required}

    @staticmethod
    def argv(kind: str, options: dict[str, Any]) -> list[str]:
        if kind == "update-check":
            args = ["update", "--check-only"]
        elif kind == "update":
            args = ["update", "-y"]
        elif kind in ("reset-plan", "reset"):
            remove = options.get("remove") if isinstance(options.get("remove"), list) else []
            cats = [c for c in reset_cli.CATEGORY_ORDER if c in remove]
            if options.get("full") is True:
                args = ["reset", "--full"]
            elif cats:
                args = ["reset", "--remove", ",".join(cats)]  # --remove keeps everything else
            else:
                raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Choose at least one thing to reset.")
            args += ["-y"] + (["--dry-run"] if kind == "reset-plan" else [])
        else:
            raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, f"Unknown action: {kind}")
        return [sys.executable, "-m", "amplifier_app_cli", *args]

    def start(self, kind: str, options: dict[str, Any]) -> dict[str, Any]:
        mutating = kind in MUTATING
        if mutating and not self.enabled():
            raise SettingsError(HTTPStatus.FORBIDDEN, "Update and reset are turned off for this settings page. Run `amplifier update` or `amplifier reset` in a terminal.")
        argv = self.argv(kind, options)
        with self._lock:
            if mutating and any(j["mutating"] and j["proc"].poll() is None for j in self._jobs.values()):
                raise SettingsError(HTTPStatus.CONFLICT, "Another update or reset is still running.")
            if kind == "reset":
                plan = self._jobs.get(str(options.get("plan_id") or ""))
                # the preview must be finished, clean, and for exactly this reset (argv minus --dry-run)
                if not plan or plan["kind"] != "reset-plan" or plan["proc"].poll() != 0 or plan["argv"][:-1] != argv:
                    raise SettingsError(HTTPStatus.CONFLICT, "Preview the reset first.")
            if self.log_dir is None:
                self.log_dir = Path(tempfile.mkdtemp(prefix="amplifier-settings-jobs-"))  # 0700
            job_id = uuid.uuid4().hex[:12]
            log = self.log_dir / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{job_id}.log"
            env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "100", "PYTHONUNBUFFERED": "1"}
            with open(log, "wb") as fh:
                proc = subprocess.Popen(  # noqa: S603 -- argv comes from the allow-list above
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=self.cwd,
                    start_new_session=mutating,  # survives this server being replaced
                )
            self._jobs[job_id] = {"proc": proc, "log": log, "started": time.time(), "mutating": mutating, "kind": kind, "argv": argv}
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        j = self._jobs.get(job_id)
        if j is None:
            raise SettingsError(HTTPStatus.NOT_FOUND, "That job isn't known to this settings page.")
        code = j["proc"].poll()
        try:
            text = j["log"].read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return {
            "id": job_id,
            "running": code is None,
            "exit_code": code,
            "output": text[-60_000:],
            "log": str(j["log"]),
            "command": "amplifier " + " ".join(j["argv"][3:]),
            "seconds": round(time.time() - j["started"]),
        }


# ---------------------------------------------------------------- home status
def status(settings: Any, routing: Any, keys: list[dict[str, Any]], merged: dict[str, Any]) -> dict[str, Any]:
    """What the home page shows, from the same sources the CLI uses."""
    from .utils.version import get_core_version, get_version_info

    try:
        version = {"display": get_version_info().display, "core": get_core_version()}
    except Exception:  # noqa: BLE001
        version = {"display": "unknown", "core": ""}

    have = {k["name"] for k in keys if k["stored"] or k["in_env"]}
    shadowed = {k["name"] for k in keys if k.get("shadowed")}
    overrides = settings.get_provider_overrides()
    # The default is the lowest config.priority (ties all count), as `amplifier provider list` marks it.
    priority = [c.get("priority", 100) if isinstance(c := p.get("config"), dict) else 100 for p in overrides]
    providers = []
    for p, pri in zip(overrides, priority):
        cfg = p.get("config") or {}
        # ${NAME} references by field. An unset *secret* is a real problem; an unset
        # optional value such as `base_url: ${ANTHROPIC_BASE_URL}` just means "use the
        # provider's default", so only secrets count as missing. An environment value
        # overriding a saved one matters for any field.
        refs = {k: m.group(1) for k, v in cfg.items() if isinstance(v, str) and (m := ENV_PATTERN.fullmatch(v.strip()))}
        providers.append({
            "name": routing.provider_name(p),
            "model": cfg.get("default_model"),
            "default": pri == min(priority),
            "missing_keys": [n for k, n in refs.items() if SECRET_FIELD.search(k) and n not in have],
            "shadowed_keys": [n for n in refs.values() if n in shadowed],
        })  # fmt: skip

    rcfg = merged.get("routing") or {}
    route: dict[str, Any] = {"matrix": rcfg.get("matrix", DEFAULT_MATRIX)}
    try:
        res = routing.resolve(route["matrix"], rcfg.get("overrides") or {}, live=False)
        route |= {
            "roles": len(res["roles"]),
            "unserved": [r["name"] for r in res["roles"] if r["problem"]],
            "pinned": [r["name"] for r in res["roles"] if r["overridden"]],
        }
    except SettingsError:
        route["missing"] = True

    upd = merged.get("updates") or {}
    hours_ago = None
    try:
        hours_ago = round((datetime.now() - datetime.fromisoformat(str(upd["last_check"]))).total_seconds() / 3600, 1)
    except (KeyError, ValueError, TypeError):
        pass

    bundle = merged.get("bundle") or {}
    return {
        "version": version,
        "providers": providers,
        "routing": route,
        "bundle": {"active": bundle.get("active") or defaults()["bundle"], "always": len(bundle.get("app") or [])},
        "updates": {"hours_ago": hours_ago, "every_hours": upd.get("check_frequency_hours")},
    }


# ---------------------------------------------------------------- phone test
NTFY_TOPIC = "AMPLIFIER_NTFY_TOPIC"  # same variable `amplifier notify ntfy` uses


def notify_test(merged: dict[str, Any]) -> dict[str, Any]:
    """Send one test notification the way hooks-notify-push will (ntfy POST)."""
    import urllib.error
    import urllib.request

    topic = os.environ.get(NTFY_TOPIC, "")  # the hook reads the topic from the environment only
    if not topic:
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Create a channel first.")
    # The hook's order: its config `server` (ntfy over push, as the settings merge them), then
    # AMPLIFIER_NTFY_SERVER, then its default.
    cfg = (merged.get("config") or {}).get("notifications") or {}
    configured = (cfg.get("ntfy") or {}).get("server") or (cfg.get("push") or {}).get("server")
    server = str(configured or os.environ.get("AMPLIFIER_NTFY_SERVER", DEFAULT_NTFY_SERVER)).rstrip("/")
    if not server.startswith(("http://", "https://")):
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, f"The ntfy server must be an http(s) URL, not {server}.")
    req = urllib.request.Request(
        f"{server}/{topic}",
        data="If you can read this, Amplifier can reach your phone.".encode(),
        headers={"Title": "Amplifier test notification", "Tags": "white_check_mark"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 -- http(s) checked above
            return {"ok": True, "status": r.status, "server": server}
    except urllib.error.HTTPError as e:
        raise SettingsError(HTTPStatus.BAD_GATEWAY, f"{server} answered {e.code}. Check the channel name and server.") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise SettingsError(HTTPStatus.BAD_GATEWAY, f"Couldn't reach {server}: {getattr(e, 'reason', e)}") from e
