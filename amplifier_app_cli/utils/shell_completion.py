"""Read-only candidates and installation helpers for Click shell completion."""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click
from click.shell_completion import CompletionItem, get_completion_class

from amplifier_foundation.paths.resolution import get_amplifier_home

from ..lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES
from ..lib.settings import AppSettings
from ..project_utils import get_project_slug

MANAGED_MARKER = "# amplifier shell completion (managed)"
_SUPPORTED_SHELLS = ("bash", "zsh", "fish")
_COMPLETION_HELP = {
    "bundle": "Available bundle",
    "provider": "Configured provider",
    "session": "Current-project session",
}


def _is_safe_candidate(value: object) -> bool:
    """Return whether *value* is safe to emit in Click's line protocol."""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return False
    if any(
        character.isspace()
        or character in ",*?[]$`'\";|&()<>{}!~#%"
        or character in {"/", "\\"}
        or unicodedata.category(character).startswith("C")
        for character in value
    ):
        return False
    return True


def _completion_items(
    values: set[str], incomplete: str, kind: str
) -> list[CompletionItem]:
    """Filter and deterministically render safe completion records."""
    return [
        CompletionItem(value, help=_COMPLETION_HELP[kind])
        for value in sorted(
            value
            for value in values
            if isinstance(value, str)
            and _is_safe_candidate(value)
            and value.startswith(incomplete)
        )
    ]


def _merged_settings() -> dict[str, Any]:
    """Load merged settings without calling mutation-capable convenience APIs."""
    try:
        settings = AppSettings().get_merged_settings()
    except Exception:
        return {}
    return settings if isinstance(settings, dict) else {}


def _added_bundle_names(settings: dict[str, Any]) -> set[str]:
    bundle = settings.get("bundle")
    added = bundle.get("added") if isinstance(bundle, dict) else None
    if not isinstance(added, dict):
        return set()
    return {
        name
        for name, uri in added.items()
        if _is_safe_candidate(name) and isinstance(uri, str) and uri.strip()
    }


def _legacy_bundle_names() -> set[str]:
    """Read unmigrated legacy bundle names without triggering their migration."""
    legacy_path = get_amplifier_home() / "bundle-registry.yaml"
    migrated_path = get_amplifier_home() / "bundle-registry.yaml.migrated"
    if migrated_path.exists() or not legacy_path.is_file():
        return set()
    try:
        import yaml

        data = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
        bundles = data.get("bundles") if isinstance(data, dict) else None
        if not isinstance(bundles, dict):
            return set()
        return {
            name
            for name, entry in bundles.items()
            if _is_safe_candidate(name)
            and isinstance(entry, dict)
            and isinstance(entry.get("uri"), str)
            and entry["uri"].strip()
        }
    except Exception:
        return set()


def _explicitly_requested_bundle_names() -> set[str]:
    """Read only user-requested cached registry roots, never transitive entries."""
    registry_path = get_amplifier_home() / "registry.json"
    if not registry_path.is_file():
        return set()
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        bundles = data.get("bundles") if isinstance(data, dict) else None
        if not isinstance(bundles, dict):
            return set()
        return {
            name
            for name, entry in bundles.items()
            if _is_safe_candidate(name)
            and isinstance(entry, dict)
            and entry.get("explicitly_requested") is True
            and isinstance(entry.get("uri"), str)
            and entry["uri"].strip()
        }
    except Exception:
        return set()


def _local_bundle_names() -> set[str]:
    """Return valid immediate bundle entries from the execution search paths."""
    package_bundles = Path(__file__).parent.parent / "data" / "bundles"
    search_paths = (
        Path.cwd() / ".amplifier" / "bundles",
        get_amplifier_home() / "bundles",
        package_bundles,
    )
    names: set[str] = set()
    for base_path in search_paths:
        try:
            if not base_path.is_dir():
                continue
            for entry in base_path.iterdir():
                if entry.is_dir():
                    if (entry / "bundle.md").is_file() or (
                        entry / "bundle.yaml"
                    ).is_file():
                        names.add(entry.name)
                elif entry.is_file() and entry.suffix in {".yaml", ".md"}:
                    names.add(entry.stem)
        except OSError:
            continue
    return names


def _extract_app_bundle_name(uri: object) -> str | None:
    """Match bundle removal's URI-to-name interpretation without exposing URIs."""
    if not isinstance(uri, str):
        return None
    # Do not derive display text from credential-bearing or query-bearing URIs.
    # Keep the execution command's name interpretation below, but only for
    # unambiguous URI forms and local names/paths. Other values remain manual.
    try:
        parsed = urlsplit(uri.removeprefix("git+"))
        if (
            parsed.scheme not in {"", "file", "https", "http"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or (parsed.scheme in {"https", "http"} and not parsed.hostname)
            or (not parsed.scheme and parsed.netloc)
        ):
            return None
    except ValueError:
        return None
    if uri.startswith("file://"):
        name = uri[7:].rstrip("/").split("/")[-1]
    elif "github.com" in uri or "gitlab.com" in uri:
        parts = uri.split("/")
        name = ""
        for index, part in enumerate(parts):
            if ("github.com" in part or "gitlab.com" in part) and index + 2 < len(
                parts
            ):
                name = parts[index + 2].split("@")[0].split("#")[0]
                for prefix in ("amplifier-bundle-", "amplifier-", "bundle-"):
                    if name.startswith(prefix):
                        name = name[len(prefix) :]
                        break
                break
    else:
        name = uri.split("/")[-1].split("@")[0].split("#")[0]
    return name if _is_safe_candidate(name) else None


def _app_bundle_names(settings: dict[str, Any]) -> set[str]:
    bundle = settings.get("bundle")
    app_bundles = bundle.get("app") if isinstance(bundle, dict) else None
    if not isinstance(app_bundles, list):
        return set()
    return {name for uri in app_bundles if (name := _extract_app_bundle_name(uri))}


def complete_bundle_names(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete selectable local bundle names without discovery or migration."""
    settings = _merged_settings()
    names = {
        name
        for name, info in WELL_KNOWN_BUNDLES.items()
        if info.get("show_in_list", True)
    }
    names.update(_added_bundle_names(settings))
    names.update(_legacy_bundle_names())
    names.update(_explicitly_requested_bundle_names())
    names.update(_local_bundle_names())
    return _completion_items(names, incomplete, "bundle")


def complete_removable_bundle_names(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete configured registrations that the current remove mode can remove."""
    settings = _merged_settings()
    app_names = _app_bundle_names(settings)
    app_mode = bool(ctx.params.get("app"))
    names = app_names if app_mode else _added_bundle_names(settings) | app_names
    if not app_mode:
        names.update(_legacy_bundle_names())
    return _completion_items(names, incomplete, "bundle")


def _configured_provider_names(providers: object) -> set[str]:
    if not isinstance(providers, list):
        return set()
    names: set[str] = set()
    for entry in providers:
        if not isinstance(entry, dict):
            continue
        module = entry.get("module")
        if (
            not isinstance(module, str)
            or not module.startswith("provider-")
            or not _is_safe_candidate(module.removeprefix("provider-"))
        ):
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            identifier = module.removeprefix("provider-")
        if _is_safe_candidate(identifier):
            names.add(identifier)
    return names


def _configured_provider_entries() -> list[dict[str, Any]]:
    """Read providers using the same scope-aware merge as provider commands."""
    try:
        providers = AppSettings().get_provider_overrides()
    except Exception:
        return []
    return providers if isinstance(providers, list) else []


def complete_configured_provider_names(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete configured execution identities, not provider modules or sources."""
    return _completion_items(
        _configured_provider_names(_configured_provider_entries()),
        incomplete,
        "provider",
    )


def _current_project_session_ids(incomplete: str) -> list[str]:
    """List at most 100 matching top-level sessions without constructing SessionStore."""
    sessions_dir = get_amplifier_home() / "projects" / get_project_slug() / "sessions"
    try:
        entries = [
            (entry.name, entry.stat().st_mtime)
            for entry in sessions_dir.iterdir()
            if entry.is_dir()
            and not entry.name.startswith(".")
            and "_" not in entry.name
            and _is_safe_candidate(entry.name)
            and entry.name.startswith(incomplete)
        ]
    except OSError:
        return []
    entries.sort(key=lambda entry: (-entry[1], entry[0]))
    return [name for name, _mtime in entries[:100]]


def complete_current_project_session_ids(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete recent current-project root sessions, based only on directory stats."""
    return [
        CompletionItem(value, help=_COMPLETION_HELP["session"])
        for value in _current_project_session_ids(incomplete)
    ]


def detect_shell() -> str | None:
    """Detect one of the supported shells from ``$SHELL``."""
    shell_name = Path(os.environ.get("SHELL", "")).name.lower()
    return next((shell for shell in _SUPPORTED_SHELLS if shell in shell_name), None)


def get_shell_config_file(shell: str) -> Path:
    """Return the conventional completion destination for *shell*."""
    home = Path.home()
    if shell == "bash":
        bashrc = home / ".bashrc"
        profile = home / ".bash_profile"
        # A fresh interactive non-login Bash reads .bashrc. Retain an existing
        # profile-only setup rather than silently moving a user's installation.
        return profile if profile.exists() and not bashrc.exists() else bashrc
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "completions" / "amplifier.fish"
    return home / f".{shell}rc"


def is_completion_installed(config_file: Path, shell: str) -> bool:
    """Recognize managed and legacy Click completion snippets without modifying files."""
    try:
        content = config_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    lines = {line.strip().removesuffix(";") for line in content.splitlines()}
    if shell in {"bash", "zsh"}:
        directive = f'eval "$(_AMPLIFIER_COMPLETE={shell}_source amplifier)"'
        return directive in lines
    if shell == "fish":
        return "function _amplifier_completion" in lines and any(
            line.startswith("complete --no-files --command amplifier ")
            and "_amplifier_completion" in line
            for line in lines
        )
    return False


def can_safely_modify(config_file: Path, shell: str) -> bool:
    """Check for a writable destination without creating directories as a probe."""
    try:
        if config_file.exists():
            if (
                shell == "fish"
                and config_file.stat().st_size
                and not is_completion_installed(config_file, shell)
            ):
                return False
            return os.access(config_file, os.W_OK)
    except OSError:
        return False
    parent = config_file.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def completion_source(command: click.Command, shell: str) -> str:
    """Render Click's native completion source in-process, never via ``PATH``."""
    completion_class = get_completion_class(shell)
    if completion_class is None:
        raise ValueError(f"Unsupported shell: {shell}")
    return completion_class(command, {}, "amplifier", "_AMPLIFIER_COMPLETE").source()


def install_completion_to_config(
    config_file: Path, shell: str, command: click.Command
) -> bool:
    """Install native source once, preserving unmanaged Fish completion files."""
    if shell not in _SUPPORTED_SHELLS or not can_safely_modify(config_file, shell):
        return False
    if is_completion_installed(config_file, shell):
        return True
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        if shell == "fish":
            config_file.write_text(
                f"{MANAGED_MARKER}\n{completion_source(command, shell)}",
                encoding="utf-8",
            )
        else:
            with config_file.open("a", encoding="utf-8") as config:
                config.write(
                    f"\n{MANAGED_MARKER}\n"
                    f'eval "$(_AMPLIFIER_COMPLETE={shell}_source amplifier)"\n'
                )
        return True
    except OSError:
        return False


def manual_installation_instruction(shell: str, config_file: Path) -> str:
    """Return a non-overwriting command to activate completion in this shell."""
    if shell == "fish":
        return "_AMPLIFIER_COMPLETE=fish_source amplifier | source"
    return f'eval "$(_AMPLIFIER_COMPLETE={shell}_source amplifier)"'


def is_click_completion_instruction(value: str | None) -> bool:
    """Return whether *value* is a native instruction that Click can handle."""
    if not value or "_" not in value:
        return False
    shell, instruction = value.split("_", 1)
    return shell in _SUPPORTED_SHELLS and instruction in {"source", "complete"}
