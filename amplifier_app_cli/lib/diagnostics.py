"""Bounded, passive CLI provenance collection.

This is not a settings resolver or a health verdict. Only allowlisted facts
leave this module. Arbitrary declarations receive report-local equality refs,
never display names, URLs, or values. Paths describe local identity and are not
a promise that a report is suitable for public sharing.
"""

from __future__ import annotations

import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import url2pathname

import yaml

from ..project_utils import get_project_slug
from .venv_home_guard import SharedVenvHomeError, enforce_home_ownership

SETTINGS_LIMIT = 256 * 1024
METADATA_LIMIT = 32 * 1024
ENTRY_LIMIT = 200
_ABSENT = object()
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}", re.ASCII)
# Do not publish arbitrary local build labels from untrusted metadata.
_VERSION = re.compile(
    r"(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?",
    re.ASCII,
)


@dataclass(frozen=True)
class DiagnosticTarget:
    cwd: Path
    home: Path
    session_id: str | None = None


def valid_session_id(value: str) -> bool:
    return isinstance(value, str) and _SESSION.fullmatch(value) is not None


def current_target(session_id: str | None = None) -> DiagnosticTarget:
    """Resolve the same home/layout as settings, without importing Foundation."""
    if session_id is not None and not valid_session_id(session_id):
        raise ValueError("invalid_session_id")
    home = os.environ.get("AMPLIFIER_HOME")
    return DiagnosticTarget(
        Path.cwd().resolve(),
        Path(home).expanduser().resolve() if home else (Path.home() / ".amplifier").resolve(),
        session_id,
    )


def _safe_path(value, issues):
    text = str(value)
    # Refuse terminal controls, bidi/format controls and URI/credential forms.
    if (
        len(text) > 4096
        or any(unicodedata.category(c).startswith("C") for c in text)
        or re.search(r"[A-Za-z][A-Za-z0-9+.-]*://|[?&#@]|%[0-9A-Fa-f]{2}", text)
        or text.startswith(("//", "\\\\"))
    ):
        issues("path_withheld")
        return None
    return text


def _base_report():
    return {
        "schema_version": 1,
        "mode": "passive",
        "collection_status": "refused",
        "health": {"status": "not_assessed", "reason": "collection_only"},
        "target": {"cwd": None, "home": None, "session_selected": False},
        "ownership": {"status": "unknown", "foreign_count": None, "owned_count": None},
        "runtime": None,
        "scopes": [
            {"scope": scope, "path": None, "status": "not_selected"}
            for scope in ("global", "project", "local", "session")
        ],
        "declarations": [],
        "environment_channels": {"module_count": 0, "modules": []},
        "effective_settings": {"status": "unknown", "reason": "effective_resolution_not_performed"},
        "live_session": {"status": "unknown", "reason": "not_observed"},
        "issues": [],
    }


def unavailable_report():
    report = _base_report()
    report["issues"] = [{"code": "target_unavailable"}]
    return report


class _LimitError(ValueError):
    pass


class UnsupportedYamlError(ValueError):
    """Syntax outside the passive diagnostic parser's accepted subset."""


class _DiagnosticLoader(yaml.SafeLoader):
    """SafeLoader with diagnostic-only resource and syntax restrictions."""

    def __init__(self, stream):
        super().__init__(stream)
        self.depth = 0
        self.nodes = 0

    def compose_node(self, parent, index):
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "tag", None) is not None:
            raise UnsupportedYamlError()
        self.depth += 1
        self.nodes += 1
        if self.depth > 32 or self.nodes > 10000:
            raise _LimitError()
        if isinstance(event, yaml.ScalarEvent) and len(event.value.encode("utf-8")) > 16384:
            raise _LimitError()
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in keys:
                raise UnsupportedYamlError()
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def _read_regular(path: Path, limit: int):
    """Check before opening; nonblocking/nofollow also protect common races."""
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            return "unsupported_symlink", None
        if not stat.S_ISREG(mode):
            return "unsupported_file", None
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return "unsupported_file", None
            if info.st_size > limit:
                return "too_large", None
            data = stream.read(limit + 1)
        if len(data) > limit:
            return "too_large", None
        return "valid", data
    except FileNotFoundError:
        return "missing", None
    except (OSError, ValueError):
        return "unreadable", None


def _read_settings(path):
    status, data = _read_regular(path, SETTINGS_LIMIT)
    if status != "valid":
        return status, None
    try:
        loader = _DiagnosticLoader(data.decode("utf-8"))
        try:
            parsed = loader.get_single_data()
        finally:
            loader.dispose()
        if parsed is None:
            return "empty", None
        if not isinstance(parsed, dict):
            return "invalid_shape", parsed
        return ("empty" if not parsed else "valid"), parsed
    except _LimitError:
        return "too_large", None
    except UnsupportedYamlError:
        return "unsupported_yaml", None
    except Exception:
        return "invalid_yaml", None


def _shape(value):
    if value is _ABSENT:
        return "absent"
    if value is None:
        return "null"
    if value is False:
        return "false"
    if value is True:
        return "true"
    if isinstance(value, str):
        return "string" if value else "empty_string"
    if isinstance(value, dict):
        return "mapping" if value else "empty_mapping"
    if isinstance(value, list):
        return "list" if value else "empty_list"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "other_scalar"


def _source_kind(value):
    if not isinstance(value, str):
        return "not_string"
    if value.startswith(("git+", "git://", "git@")):
        return "git"
    if value.startswith("file:"):
        return "file"
    if value.startswith(("http:", "https:")):
        return "http"
    if value.startswith(("/", ".", "~", "\\")) or re.match(r"^[A-Za-z]:", value):
        return "path"
    return "opaque"


def _declarations(data, scope, emit, ref):
    """Fixed fields and parsed-order indices are inspection locators, not selectors."""
    def get(parent, key):
        return parent.get(key, _ABSENT) if isinstance(parent, dict) else _ABSENT

    def field(container, member, value, **extra):
        row = {"scope": scope, "container": container, "member": member, "shape": _shape(value), **extra}
        if value is not _ABSENT and not isinstance(value, (dict, list)):
            row["value_ref"] = ref(value)
            row["source_kind"] = _source_kind(value)
        emit(row)

    for container, member in (
        ("bundle", "active"), ("bundle", "app"), ("routing", "matrix"),
        ("sources", "modules"), ("sources", "bundles"),
        ("overrides", "modules"), ("overrides", "bundles"),
    ):
        parent = data
        for key in container.split("."):
            parent = get(parent, key)
        value = get(parent, member)
        field(container, member, value, container_shape=_shape(parent))
        if container == "bundle" and member == "app" and isinstance(value, list):
            for index, item in enumerate(value):
                field("bundle.app", "item", item, index=index)
        if container == "sources" and isinstance(value, dict):
            for index, (identity, item) in enumerate(value.items()):
                field("sources." + member, "source", item, index=index, identity_ref=ref(identity))

    overrides = get(data, "overrides")
    field("overrides", "entries", overrides)
    if isinstance(overrides, dict):
        for index, (identity, item) in enumerate(overrides.items()):
            if identity in ("modules", "bundles"):
                # Development maps: report presence/shape only, not their targets.
                continue
            field("overrides", "source", get(item, "source"), index=index, identity_ref=ref(identity))

    for container in ("modules", "config"):
        parent = get(data, container)
        providers = get(parent, "providers")
        field(container, "providers", providers, container_shape=_shape(parent))
        if isinstance(providers, list):
            for index, item in enumerate(providers):
                extra = {}
                for key in ("id", "module"):
                    identity = get(item, key)
                    if identity is not _ABSENT:
                        extra[key + "_ref"] = ref(identity)
                field(
                    container + ".providers", "source", get(item, "source"),
                    index=index, entry_shape=_shape(item), **extra,
                )


def _direct_url(dist, issue):
    result = {"status": "missing"}
    try:
        # Use metadata APIs only: never import or load installed entry points.
        entry = next(
            (p for p in (dist.files or ()) if len(p.parts) == 2
             and p.parts[0].endswith(".dist-info") and p.parts[1] == "direct_url.json"),
            None,
        )
        if entry is None:
            return result
        status, data = _read_regular(Path(dist.locate_file(entry)), METADATA_LIMIT)
        if status != "valid":
            issue("distribution_metadata_unavailable")
            return {"status": status}
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError()
        result = {"status": "valid", "editable": None, "local_target": None, "commit": None}
        directory = parsed.get("dir_info")
        if isinstance(directory, dict) and type(directory.get("editable")) is bool:
            result["editable"] = directory["editable"]
        url = parsed.get("url")
        if isinstance(url, str):
            if any(unicodedata.category(c).startswith("C") for c in url):
                raise ValueError()
            parts = urlsplit(url)
            if parts.scheme == "file" and not parts.netloc and not parts.query and not parts.fragment:
                # url2pathname performs exactly one percent decode.
                native = url2pathname(parts.path)
                if Path(native).is_absolute():
                    result["local_target"] = _safe_path(native, issue)
        vcs = parsed.get("vcs_info")
        commit = vcs.get("commit_id") if isinstance(vcs, dict) else None
        if isinstance(commit, str) and re.fullmatch(r"[a-fA-F0-9]{7,64}", commit):
            result["commit"] = commit
        return result
    except Exception:
        issue("distribution_metadata_unavailable")
        return {"status": "unreadable"}


def _runtime(issue):
    distributions = []
    for name in ("amplifier", "amplifier-app-cli", "amplifier-core", "amplifier-foundation"):
        row = {"name": name, "status": "missing"}
        try:
            dist = metadata.distribution(name)
            version = dist.version
            row = {
                "name": name, "status": "present",
                "version": version if isinstance(version, str) and len(version) <= 128 and _VERSION.fullmatch(version) else None,
                "location": _safe_path(dist.locate_file(""), issue),
                "direct_url": _direct_url(dist, issue),
            }
            if row["version"] is None:
                issue("distribution_version_withheld")
        except metadata.PackageNotFoundError:
            pass
        except Exception:
            row["status"] = "unreadable"
            issue("distribution_metadata_unavailable")
        distributions.append(row)
    return {
        "argv0": _safe_path(sys.argv[0], issue),
        "interpreter": _safe_path(sys.executable, issue),
        "python": ".".join(str(n) for n in sys.version_info[:3]),
        "prefix": _safe_path(sys.prefix, issue),
        "base_prefix": _safe_path(sys.base_prefix, issue),
        "distributions": distributions,
    }


def collect_diagnostics(target: DiagnosticTarget) -> dict:
    """Collect bounded local facts. Complete means collected, not healthy."""
    report = _base_report()

    def issue(code, scope=None):
        row = {"code": code}
        if scope is not None:
            row["scope"] = scope
        if row not in report["issues"]:
            report["issues"].append(row)

    report["target"] = {
        "cwd": _safe_path(target.cwd, issue),
        "home": _safe_path(target.home, issue),
        "session_selected": target.session_id is not None,
    }
    if target.session_id is not None and not valid_session_id(target.session_id):
        issue("invalid_session_id")
        return report
    try:
        conflict = enforce_home_ownership(["doctor"], current_home=target.home)
        report["ownership"] = {
            "status": "override" if conflict is not None else "clear",
            "foreign_count": len(conflict.foreign) if conflict is not None else 0,
            "owned_count": len(conflict.owned) if conflict is not None else None,
        }
    except SharedVenvHomeError as error:
        report["ownership"] = {
            "status": "refused", "foreign_count": len(error.conflict.foreign),
            "owned_count": len(error.conflict.owned),
        }
        issue("ownership_conflict")
        return report
    except Exception:
        issue("ownership_unknown")
        return report

    report["runtime"] = _runtime(issue)
    refs = {}

    def ref(value):
        # YAML values never leave this local dictionary. Typed repr also
        # distinguishes null, false, empty, and numeric/string identities.
        key = (type(value).__name__, repr(value))
        if key not in refs:
            refs[key] = f"r{len(refs) + 1}"
        return refs[key]

    def emit(row):
        if len(report["declarations"]) < ENTRY_LIMIT:
            report["declarations"].append(row)
        else:
            issue("declarations_truncated")

    paths = [
        target.home / "settings.yaml",
        target.cwd / ".amplifier" / "settings.yaml",
        target.cwd / ".amplifier" / "settings.local.yaml",
        None,
    ]
    if target.session_id is not None:
        # get_project_slug is a pure cwd helper; explicit targets need the
        # same transform without changing process cwd.
        slug = get_project_slug() if target.cwd == Path.cwd().resolve() else (
            str(target.cwd.resolve()).replace("/", "-").replace("\\", "-").replace(":", "")
        )
        if not slug.startswith("-"):
            slug = "-" + slug
        paths[3] = target.home / "projects" / slug / "sessions" / target.session_id / "settings.yaml"
    for row, path in zip(report["scopes"], paths):
        if path is None:
            continue
        scope = row["scope"]
        row["path"] = _safe_path(path, issue)
        try:
            if scope == "session" and any(p.is_symlink() for p in (path, *path.parents)):
                status, data = "unsupported_symlink", None
            else:
                status, data = _read_settings(path)
        except Exception:
            status, data = "unreadable", None
        row["status"] = status
        if status in ("valid", "empty", "invalid_shape"):
            row["shape"] = _shape(data)
        if status not in ("valid", "empty", "missing"):
            issue("settings_" + status, scope)
        if status in ("valid", "empty"):
            _declarations(data or {}, scope, emit, ref)

    channels = report["environment_channels"]
    for name in ("AMPLIFIER_GIT_HOST", "AMPLIFIER_HOME_ENV"):
        channels[name] = {"present": name in os.environ}
    for name in os.environ:
        if name.startswith("AMPLIFIER_MODULE_"):
            channels["module_count"] += 1
            if len(channels["modules"]) < ENTRY_LIMIT:
                channels["modules"].append({"identity_ref": ref(name), "present": True})
            else:
                issue("environment_channels_truncated")
    report["collection_status"] = "partial" if report["issues"] else "complete"
    return report


def render_text(report: dict) -> str:
    """Render the allowlisted report, with no resolution or health inference."""
    lines = [
        "Amplifier doctor (passive)",
        "Collection: " + report["collection_status"] + " (not a health verdict)",
        "Health: not assessed. Issues describe collection limits, not all environment defects.",
        "Paths identify this local environment; review before sharing.",
        "CWD: " + (report["target"]["cwd"] or "withheld"),
        "Home: " + (report["target"]["home"] or "withheld"),
        "Ownership: " + report["ownership"]["status"],
    ]
    runtime = report["runtime"]
    if runtime is not None:
        lines.extend((
            "Interpreter: " + (runtime["interpreter"] or "withheld"),
            "Launcher: " + (runtime["argv0"] or "withheld"),
            "Python: " + runtime["python"],
            "Prefix: " + (runtime["prefix"] or "withheld"),
            "Base prefix: " + (runtime["base_prefix"] or "withheld"),
        ))
        for row in runtime["distributions"]:
            lines.append(row["name"] + ": " + row["status"] + " " + (row.get("version") or ""))
            if row.get("location") is not None:
                lines.append("  Location: " + row["location"])
            direct = row.get("direct_url", {})
            if direct.get("status") == "valid":
                editable = direct["editable"]
                lines.append("  Editable: " + ("unknown" if editable is None else str(editable).lower()))
                if direct["local_target"] is not None:
                    lines.append("  Local target: " + direct["local_target"])
                if direct["commit"] is not None:
                    lines.append("  VCS commit: " + direct["commit"])
    for row in report["scopes"]:
        lines.append(row["scope"] + ": " + row["status"] + " " + (row["path"] or ""))
    for row in report["declarations"]:
        index = f"[{row['index']}]" if "index" in row else ""
        refs = " ".join(f"{key}={row[key]}" for key in ("identity_ref", "id_ref", "module_ref", "value_ref") if key in row)
        kind = " kind=" + row["source_kind"] if "source_kind" in row else ""
        lines.append(f"{row['scope']} {row['container']}{index}.{row['member']}: {row['shape']}{kind} {refs}")
    channels = report["environment_channels"]
    lines.append(f"Environment module channels: {channels['module_count']} (values not read)")
    for row in channels["modules"]:
        lines.append("  Module channel: " + row["identity_ref"] + " present")
    for name in ("AMPLIFIER_GIT_HOST", "AMPLIFIER_HOME_ENV"):
        if name in channels:
            lines.append(name + ": " + ("present" if channels[name]["present"] else "absent"))
    lines.extend((
        "Effective settings: unknown (effective_resolution_not_performed)",
        "Live session: unknown (not_observed)",
        "Refs express equality only within this report; no precedence or mounted-state claims.",
    ))
    for issue in report["issues"]:
        lines.append("Issue: " + issue["code"] + (" (" + issue["scope"] + ")" if "scope" in issue else ""))
    return "\n".join(lines)