"""Routing for the settings page -- a thin JSON layer over `amplifier routing`.

Discovery, which file wins for each matrix name, which providers count as
configured, whether a pattern matches a live model, and which candidate wins
are all delegated to `commands/routing.py`, so the page and `amplifier routing
list/show` cannot disagree. This module adds: the user's unsaved
`routing.overrides` composed in for preview, model listing that keeps the
failure reason, and saving a whole matrix the way `amplifier routing create` does.
"""

from __future__ import annotations

import copy
import datetime
import logging
import os
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .commands import routing as cli
from .lib.routing_provenance import resolve_matrix_origins
from .lib.settings import AppSettings, get_custom_routing_dir
from .settings_web_system import SettingsError, run_with_timeout
from .utils.atomic_write import atomic_write_yaml

# hooks-routing's validate_matrix_name (matrix_loader.py) accepts [a-z][a-z0-9_-]*; the hook isn't
# importable outside a session, so the pattern is repeated here, capped at 64 characters.
REQUIRED_ROLES = ("general", "fast")
EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def compose(base_roles: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Apply `routing.overrides` exactly as hooks-routing does at session start."""
    try:
        from amplifier_module_hooks_routing.matrix_loader import compose_matrix

        return compose_matrix(base_roles, overrides)
    except ImportError:  # hook not importable in this process: same rule, inline
        result = copy.deepcopy(base_roles)
        for role, data in overrides.items():
            data = copy.deepcopy(data)
            cands = data.get("candidates", [])
            if "base" in cands:
                base = result.get(role, {}).get("candidates", [])
                data["candidates"] = [x for c in cands for x in (base if c == "base" else [c])]
            result[role] = data
        return result


def _known_config(matrices: dict[str, tuple[dict[str, Any], Path]]) -> dict[str, dict[str, list[str]]]:
    """provider -> knob -> values any matrix uses (the pin editor's choices).

    Read from the matrices so a new knob or level shows up the day a matrix
    uses it. Nested knobs are dotted: extra_request_params.thinking_config.thinking_level.
    """
    seen: dict[str, dict[str, set[str]]] = {}

    def walk(provider: str, prefix: str, cfg: dict[str, Any]) -> None:
        for k, v in cfg.items():
            if isinstance(v, dict):
                walk(provider, f"{prefix}{k}.", v)
            else:
                seen.setdefault(provider, {}).setdefault(prefix + k, set()).add(str(v).lower() if isinstance(v, bool) else str(v))

    for data, _ in matrices.values():
        for role in data.get("roles", {}).values():
            for c in (role or {}).get("candidates", []):
                if isinstance(c, dict) and isinstance(c.get("config"), dict):
                    walk(str(c.get("provider", "")), "", c["config"])
    rank = lambda v: (EFFORT_ORDER.index(v) if v in EFFORT_ORDER else len(EFFORT_ORDER), v)  # noqa: E731
    return {p: {k: sorted(vs, key=rank) for k, vs in knobs.items()} for p, knobs in seen.items()}


class RoutingService:
    """Routing views for one server; caches live model lists until settings or keys change."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._live: cli.LiveModels | None = None

    def refresh(self) -> None:
        with self._lock:
            self._live = None

    def _live_models(self) -> cli.LiveModels:
        with self._lock:
            if self._live is None:
                self._live = cli.LiveModels(self.settings)
            return self._live

    @staticmethod
    def provider_name(p: dict[str, Any]) -> str:
        return str(p.get("id") or str(p.get("module", "")).removeprefix("provider-"))

    @staticmethod
    def _load() -> tuple[list[Path], dict[str, Any], dict[str, tuple[dict[str, Any], Path]]]:
        files = cli._discover_matrix_files(fetch=False)  # never download from a request
        origins = resolve_matrix_origins(files)
        return files, origins, cli._load_all_matrices_with_paths(files, origins)

    # -- `amplifier routing list` ---------------------------------------------
    def matrices(self) -> list[dict[str, Any]]:
        _, origins, loaded = self._load()
        types = cli._get_configured_provider_types(self.settings)
        custom = get_custom_routing_dir()
        out = []
        for stem, (data, path) in sorted(loaded.items()):
            covered, total = cli._check_compatibility(data, types)
            origin = origins.get(stem)
            out.append(
                {
                    "name": stem,
                    "description": str(data.get("description") or "").strip(),
                    "updated": str(data.get("updated") or ""),
                    "origin": "yours" if path.is_relative_to(custom) else "built-in",
                    "replaces": bool(origin and origin.is_shadowed),
                    "path": str(path),
                    "covered": covered,
                    "total": total,
                }
            )
        return out

    # -- `amplifier routing show --detailed`, with unsaved pins ---------------
    def resolve(self, matrix: str, overrides: dict[str, Any], live: bool) -> dict[str, Any]:
        if not isinstance(overrides, dict):
            raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "routing.overrides must map role names to candidates.")
        files, _, loaded = self._load()
        if matrix not in loaded:
            raise SettingsError(HTTPStatus.NOT_FOUND, f"There's no routing matrix called {matrix}. Run `amplifier routing list` to fetch the built-in ones.")
        roles = compose(loaded[matrix][0].get("roles", {}), overrides)
        types = cli._get_configured_provider_types(self.settings)
        lm = self._live_models() if live else None

        out_roles = []
        for role_name, role in roles.items():
            cands: list[dict[str, Any]] = []
            winner, any_configured = None, False
            for c in role.get("candidates", []):
                if not isinstance(c, dict):
                    continue
                provider, model = str(c.get("provider", "")), str(c.get("model", "?"))
                configured = provider in types
                any_configured |= configured
                status, resolved = "unconfigured", None
                if configured:
                    status, resolved = lm.resolve(provider, model) if lm else ("active", None)
                if status in ("active", "unverified"):
                    state = "winner" if winner is None else "fallback"
                    winner = len(cands) if winner is None else winner
                else:
                    state = status  # unconfigured | no-match
                cands.append(
                    {
                        "provider": provider,
                        "model": model,
                        "resolved": resolved if resolved and resolved != model else None,
                        "config": c.get("config") or {},
                        "state": state,
                        "verified": bool(lm) and status == "active" and provider not in lm.unverified,
                    }
                )
            out_roles.append(
                {
                    "name": role_name,
                    "description": str(role.get("description") or ""),
                    "overridden": role_name in overrides,
                    "override": overrides.get(role_name),
                    "candidates": cands,
                    "winner": winner,
                    "problem": None if winner is not None else ("no-match" if any_configured else "no-provider"),
                }
            )
        return {
            "matrix": matrix,
            "roles": out_roles,
            "live": live,
            "unverified": sorted(lm.unverified) if lm else [],
            "providers": [
                {"name": self.provider_name(p), "type": str(p.get("module", "")).removeprefix("provider-")}
                for p in self.settings.get_provider_overrides()
            ],
            "known_config": _known_config(loaded),
            "known_roles": cli.discover_roles_from_matrices(files),
        }

    # -- live model list, keeping the reason it failed ---------------------
    def models(self, provider: str) -> dict[str, Any]:
        from .provider_loader import ModelCatalogUnavailable, get_provider_models

        warnings: list[str] = []

        class Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                msg = record.getMessage().removeprefix("[PROVIDER]").strip()
                if msg not in warnings:
                    warnings.append(msg)

        def fetch() -> list[str]:
            cfg = cli._get_provider_config(provider, self.settings)
            module = cli._resolve_provider_module(provider, self.settings)
            # the provider's config warnings belong on the page, not in the terminal
            log = logging.getLogger("amplifier_module_" + module.replace("-", "_"))
            handler, propagate = Collect(logging.WARNING), log.propagate
            log.addHandler(handler)
            log.propagate = False
            try:
                return [str(getattr(m, "id", None) or getattr(m, "name", None) or m) for m in get_provider_models(module, collected_config=cfg, require_catalog=True)]
            finally:
                log.removeHandler(handler)
                log.propagate = propagate

        try:
            timeout = float(os.environ.get("AMPLIFIER_ROUTING_LIST_TIMEOUT", cli._LIVE_LIST_TIMEOUT_S))
        except ValueError:
            timeout = cli._LIVE_LIST_TIMEOUT_S
        started = time.monotonic()
        models, error = None, None
        try:
            models = run_with_timeout(fetch, timeout * 2)
        except TimeoutError:
            error = "The provider didn't answer in time."
        except ModelCatalogUnavailable as e:
            error = f"{e}. Amplifier can't load this provider's code outside a session, so its models can't be listed here (sessions are not affected)."
        except Exception as e:  # noqa: BLE001 -- auth / network errors: show the provider's own words
            error = f"{type(e).__name__}: {e}"[:300]
        return {"provider": provider, "models": models, "error": error, "warnings": warnings, "seconds": round(time.monotonic() - started, 1)}

    # -- `amplifier routing create` -------------------------------------------
    def save_matrix(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not cli.MATRIX_NAME_RE.fullmatch(name):
            raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Use lowercase letters, numbers, hyphens and underscores for the name (up to 64), starting with a letter.")
        roles = body.get("roles") if isinstance(body.get("roles"), dict) else {}
        missing = [r for r in REQUIRED_ROLES if r not in roles]
        if missing:
            raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, f"Every matrix needs these roles: {', '.join(missing)}.")
        clean: dict[str, Any] = {}
        for role, data in roles.items():
            cands = [c for c in (data or {}).get("candidates", []) if isinstance(c, dict)]
            if not cands:
                raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, f"The {role} role has no models to try.")
            clean[str(role)] = {
                "description": str((data or {}).get("description") or ""),
                "candidates": [{k: c[k] for k in ("provider", "model", "config") if c.get(k)} for c in cands],
            }
        target = get_custom_routing_dir() / f"{name}.yaml"
        if target.exists() and not body.get("overwrite"):
            raise SettingsError(HTTPStatus.CONFLICT, f"You already have a matrix called {name}.", exists=True)
        atomic_write_yaml(
            target,
            {"name": name, "description": str(body.get("description") or f"Custom matrix: {name}"), "updated": datetime.date.today().isoformat(), "roles": clean},
            sort_keys=False,
        )
        return {"name": name, "path": str(target)}

    def delete_matrix(self, name: str) -> dict[str, Any]:
        custom = get_custom_routing_dir()
        target = custom / f"{name}.yaml"
        # any file of yours, including one named before the hook's rule; never a path outside the folder
        if target.parent != custom or not target.is_file():
            raise SettingsError(HTTPStatus.NOT_FOUND, "Only matrices you created can be deleted.")
        target.unlink()
        return {"ok": True}
