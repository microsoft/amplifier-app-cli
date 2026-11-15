"""Step-by-step connection diagnosis for one provider (the page's "Diagnose").

Steps: Provider code, Settings (${VAR}s resolved), API key (keys.env vs the
environment), Key check (the provider lists models once per candidate key) and
Through Amplifier (as configured; log lines are captured for display only).
HTTP status and URL come from the exception chain, tips from those structured
results, and every candidate key value is masked before the report is returned.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import re
import time
from typing import Any

from .lib.env_vars import ENV_PATTERN
from .lib.settings import AppSettings
from .provider_manager import resolve_provider_entry
from .settings_web_system import SECRET_FIELD, SECRET_NAME, env_refs, is_secret_value, mask, read_keys_file, run_with_timeout

KEYLIKE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9_\-\.]{8,})")
LOGGERS = ("httpx", "httpx2", "httpcore.connection", "httpcore2.connection", "openai", "anthropic", "google_genai", "ollama")
KEY_TIMEOUT_S = 20.0
LIST_TIMEOUT_S = 30.0


def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, mask(s))
    return KEYLIKE.sub(lambda m: mask(m.group(0)), text)


def _resolve(value: Any) -> tuple[Any, list[str], list[str]]:
    """Expand ${VAR} and ${VAR:default} as the runtime does (lib/env_vars.py): value, names, unset names."""
    if not isinstance(value, str):
        return value, [], []
    found = list(ENV_PATTERN.finditer(value))
    resolved = ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    return resolved, [m.group(1) for m in found], [m.group(1) for m in found if m.group(1) not in os.environ and m.group(2) is None]


def _step(name: str, status: str, summary: str, **detail: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "summary": summary, **detail}


def _http_of(e: BaseException) -> tuple[int | None, str | None]:
    """HTTP status and request URL from an exception or anything it wraps."""
    status, url, seen, todo = None, None, set(), [e]
    while todo:
        x = todo.pop(0)
        if x is None or id(x) in seen:
            continue
        seen.add(id(x))
        code = getattr(x, "status_code", None)
        status = status or (code if isinstance(code, int) else None)
        req = getattr(getattr(x, "response", None), "request", None) or getattr(x, "request", None)
        url = url or (str(getattr(req, "url", "")) or None)
        todo += [x.__cause__, x.__context__]
    return status, url


def _list_models(module: str, cfg: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One model-list call through the provider: {http, seconds, models | message, url?, ids?}."""
    from .provider_loader import get_provider_models

    t0 = time.monotonic()
    try:
        models = run_with_timeout(lambda: get_provider_models(module, collected_config=cfg, require_catalog=True), timeout)
        ids = [str(getattr(m, "id", None) or m) for m in models]
        return {"http": 200, "seconds": round(time.monotonic() - t0, 2), "models": len(ids), "ids": ids}
    except TimeoutError:
        return {"http": None, "seconds": round(time.monotonic() - t0, 2), "message": f"No answer within {timeout:.0f} seconds."}
    except Exception as e:  # noqa: BLE001 -- this is the error we're here to show
        status, url = _http_of(e)
        cause = e.__cause__ or e.__context__
        why = f" (caused by {type(cause).__name__}: {cause})" if cause and str(cause) not in str(e) else ""
        return {"http": status, "seconds": round(time.monotonic() - t0, 2), "message": f"{type(e).__name__}: {e}{why}"[:400], "url": url, "error_type": type(e).__name__}


def _key_sources(var: str, file_val: str | None, env_val: str | None, live_env: Any) -> tuple[list[dict[str, Any]], list[str]]:
    sources = [{"where": "keys.env", "preview": mask(file_val), "length": len(file_val)}] if file_val is not None else []
    set_by: list[str] = []
    if env_val is not None and env_val != file_val:
        # The login shell only explains, and only after the user asked for a re-read:
        # shell_value() is None until then, so no shell is started from here.
        from_shell = live_env is not None and live_env.shell_value(var) == env_val
        if from_shell:
            from .settings_web_env import where_set

            set_by = where_set(var)
        where = f"your shell ({', '.join(set_by)})" if set_by else "your shell startup files" if from_shell else "your environment (the shell, launchctl or the program that started this page)"
        sources.append({"where": where, "preview": mask(env_val), "length": len(env_val)})
    return sources, set_by


def diagnose(settings: AppSettings, name: str, live_env: Any = None) -> dict[str, Any]:
    started = time.monotonic()
    entry = resolve_provider_entry(settings.get_provider_overrides(), name)
    if entry is None:
        return {"provider": name, "steps": [_step("Provider", "fail", f"No provider called {name} in your settings.")], "tips": [], "seconds": 0}
    module = str(entry.get("module", ""))
    module = module if module.startswith("provider-") else f"provider-{module}"
    cfg = dict(entry.get("config") or {})
    steps: dict[str, dict[str, Any]] = {}
    tips: list[str] = []

    # 1. Provider code
    from .provider_loader import load_provider_class

    try:
        version = importlib.metadata.version(f"amplifier-module-{module}")
    except importlib.metadata.PackageNotFoundError:
        version = None
    loaded = load_provider_class(module) is not None
    msg = f"{module}{f' {version}' if version else ''} is installed and loads." if loaded else f"{module} isn't installed in this Amplifier, so it can't be used here."
    steps["Provider code"] = _step("Provider code", "ok" if loaded else "fail", msg, module=module, version=version, source=entry.get("source"))
    if not loaded:
        tips.append(f"Run `amplifier provider install {module.removeprefix('provider-')}`, or start a session once; Amplifier reinstalls missing providers on start.")

    # 2. Settings
    shown, unset = {}, []
    for k, v in sorted(cfg.items()):
        if not SECRET_FIELD.search(k):
            resolved, names, missing = _resolve(v)
            hide = is_secret_value(k, v) or any(SECRET_NAME.search(n) for n in names) or is_secret_value(k, resolved)
            if hide:  # a key behind a harmless-looking field, or a password in a URL
                v, resolved = (mask(v) if is_secret_value(k, v) else v), mask(str(resolved))
            shown[k] = {"value": v, "resolved": resolved if names else None}
            unset += missing
    base = str(_resolve(cfg.get("base_url"))[0] or "").strip()
    summary = f"Calls {base}." if base else "No base URL; the provider's built-in default is used."
    if unset:
        summary += f" Not set in the environment: {', '.join(sorted(set(unset)))} (the provider falls back to its default)."
    steps["Settings"] = _step("Settings", "ok", summary, config=shown)

    # 3. API key
    key_field = next((str(k) for k in cfg if SECRET_FIELD.search(str(k))), None)
    raw = cfg.get(key_field) if key_field else None
    var = (env_refs(raw) or [None])[0] if isinstance(raw, str) else None
    candidates: list[tuple[str, str]] = []
    set_by: list[str] = []
    if key_field is None:
        steps["API key"] = _step("API key", "skip", "This provider has no API key setting.")
    elif not var:
        candidates = [("in your settings file", str(raw))] if raw else []
        steps["API key"] = _step("API key", "warn" if raw else "fail", "The key is written directly in settings. Saving on this page moves it to keys.env." if raw else "No key set.", field=key_field)
    else:
        env_val, file_val = os.environ.get(var), read_keys_file().get(var)
        sources, set_by = _key_sources(var, file_val, env_val, live_env)
        used = env_val if env_val is not None else file_val  # a set variable wins even if empty, as at runtime
        if not used:
            why = "it's set but empty in your environment, and Amplifier never overrides a set variable" if env_val == "" else "it's not in keys.env or your environment"
            steps["API key"] = _step("API key", "fail", f"`{var}` is empty: {why}.", variable=var, sources=sources)
            tips.append(f"Add `{var}` on the API keys page." if env_val is None else f"Unset the empty `{var}` in your environment, then restart `amplifier settings`.")
        elif env_val is not None and file_val is not None and env_val != file_val:
            steps["API key"] = _step("API key", "warn", f"`{var}` has two different values. Amplifier uses the one from your environment, because it never overrides an existing variable.", variable=var, sources=sources, used="your environment", set_by=set_by)
            candidates = [("from your environment (the one in use)", env_val), ("from keys.env", file_val)]
        else:
            where = "keys.env" if env_val is None or env_val == file_val else "your environment"
            steps["API key"] = _step("API key", "ok", f"`{var}` comes from {where} ({mask(used)}, {len(used)} characters).", variable=var, sources=sources, used=where)
            candidates = [(f"from {where}", used)]
    secrets = [v for _, v in candidates]

    # 4. Key check: the provider itself, once per candidate key
    if not loaded or not candidates:
        steps["Key check"] = _step("Key check", "skip", "Skipped: the provider code isn't available." if not loaded else "No key to check.")
    else:
        results = []
        for label, value in candidates:
            r = _list_models(module, {**cfg, str(key_field): value}, KEY_TIMEOUT_S)
            r.pop("ids", None)
            results.append({"key": label, **r})
        first, works = results[0], [r for r in results if r["http"] == 200]
        if first["http"] == 200:
            steps["Key check"] = _step("Key check", "ok", f"The key in use works: {first['models']} models in {first['seconds']}s.", results=results)
        else:
            http = f" with HTTP {first['http']}" if first["http"] else ""
            at = f" ({first['url']})" if first.get("url") else ""
            steps["Key check"] = _step("Key check", "warn" if works else "fail", f"The key in use failed{http}{at}: {first.get('message')}", results=results)
        if first["http"] in (401, 403) and works:
            v = var or "the key"
            if set_by:
                tips.insert(0, f"The saved key in keys.env works; the one set in {', '.join(set_by)} doesn't. Update or remove `export {v}=` there, then restart `amplifier settings` from a new terminal.")
            else:
                tips.insert(0, f"The saved key in keys.env works; the one in your environment doesn't. Run `launchctl unsetenv {v}` (macOS), then restart `amplifier settings` from a new terminal.")
        elif first["http"] in (401, 403):
            tips.append("The provider rejected the key. Create a new key in the provider's dashboard and save it on the API keys page.")
        elif first["http"] == 404:
            tips.append("The provider answered 404. Check the base URL on the Providers page (OpenAI-compatible servers usually end in `/v1`), and that the server is running.")
        elif first["http"] is None and not works:
            tips.append("Couldn't get an answer. Check your network, VPN or proxy, the base URL, and that the server is running.")

    # 5. Through Amplifier, as configured, with logs captured for display
    if not loaded:
        steps["Through Amplifier"] = _step("Through Amplifier", "skip", "Skipped: the provider code isn't available.")
    else:
        records: list[str] = []
        t_start = time.monotonic()

        class Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(f"+{time.monotonic() - t_start:5.1f}s {record.name}: {record.getMessage()}")

        handler = Collect(logging.DEBUG)
        saved = [(lg, lg.level, lg.propagate) for lg in map(logging.getLogger, (f"amplifier_module_{module.replace('-', '_')}", *LOGGERS))]
        for lg, _, _ in saved:
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)
            lg.propagate = False
        try:
            r = _list_models(module, cfg, LIST_TIMEOUT_S)
        finally:
            for lg, level, propagate in saved:
                lg.removeHandler(handler)
                lg.setLevel(level)
                lg.propagate = propagate
        log = [_scrub(x, secrets)[:400] for x in records[-60:]]
        if r["http"] == 200:
            steps["Through Amplifier"] = _step("Through Amplifier", "ok", f"Listed {r['models']} models in {r['seconds']}s, the same call `amplifier provider test` makes.", models=r["ids"][:50], log=log)
        else:
            at = f" from {r['url']}" if r.get("url") else ""
            http = f"HTTP {r['http']}{at} after" if r["http"] else "after"
            steps["Through Amplifier"] = _step("Through Amplifier", "fail", f"{http} {r['seconds']}s. {r['message']}", error_type=r.get("error_type"), log=log)

    if not tips and all(s["status"] in ("ok", "skip") for s in steps.values()):
        tips.append("Everything checks out. If sessions still fail, the problem is with a specific model or request; run a session with `--verbose`.")
    report = {"provider": name, "module": module, "steps": list(steps.values()), "tips": tips, "seconds": round(time.monotonic() - started, 1)}
    return json.loads(_scrub(json.dumps(report, default=str), secrets))
