"""Configuration-only current declarations and restriction-only historical data.

Neither metadata nor a cached manifest grants source consent. Missing historical
capture cannot be reconstructed on resume. The metadata selector is a mismatch
join, NOT a cryptographic policy-to-transcript binding.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

RESTRICTIONS_KEY = "cli_media_restrictions_v1"
_FIELDS = ("max_bytes", "max_pixels", "max_images", "retention", "ttl_seconds")
_RANK = {"drop": 0, "request-only": 1, "durable": 2}


class CurrentPolicyIncomplete(ValueError):
    """Finite public policy failure, never an external exception payload."""

    def __init__(self):
        super().__init__("Current policy composition is incomplete")


def _native_file_uri_path(encoded_path):
    """Convert a local URI path once, including Windows drive syntax."""
    from amplifier_foundation.sources.policy import SourceResolutionDenied

    native = url2pathname(encoded_path)
    if native.replace("\\", "/").startswith("//"):
        raise SourceResolutionDenied("Nonlocal manifest")
    path = Path(native)
    if not path.is_absolute():
        raise SourceResolutionDenied("Nonlocal manifest")
    return path


def meet(*declarations):
    """Validate and intersect host declarations; never accept authority flags."""
    from amplifier_foundation.session.media import MediaPolicy
    from .session_media import media_configuration

    values = [media_configuration({"session_media": value}) for value in declarations]
    if not values:
        values = [{"enabled": True}]
    policies = [
        MediaPolicy(**{k: v for k, v in value.items() if k != "enabled"})
        for value in values
    ]
    result = {"enabled": all(value.get("enabled", True) for value in values)}
    for field in _FIELDS:
        if field == "retention":
            result[field] = min((p.retention for p in policies), key=_RANK.__getitem__)
        else:
            limits = [getattr(p, field) for p in policies if getattr(p, field) is not None]
            result[field] = min(limits) if limits else None
    return result


def settings_media_declarations(app_settings, *, session_id=None, project_slug=None):
    """Raw layers retain denials/minima even when a higher scope is wider."""
    from amplifier_foundation.settings import read_yaml
    from amplifier_foundation.paths.resolution import get_amplifier_home
    from copy import copy
    from .lib.env_vars import expand_env_vars
    from .lib.settings import AppSettings

    paths = copy(app_settings.paths)
    if session_id and project_slug and paths.session_settings is None:
        paths.session_settings = get_amplifier_home() / "projects" / project_slug / "sessions" / session_id / "settings.yaml"
    effective = AppSettings(paths)
    paths = effective.paths
    layers = [
        effective.get_merged_settings(),
        *(read_yaml(path) for path in (
            paths.global_settings, paths.project_settings, paths.local_settings,
            paths.session_settings,
        ) if path is not None),
    ]
    result = []
    for layer in layers:
        config = layer.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("Invalid host configuration")
        # Existing direct host input and config.session_media are the ONLY
        # supported settings declarations. Raw bundle YAML is not inferred.
        for mapping in (layer, config):
            if "session_media" in mapping:
                result.append(expand_env_vars(mapping["session_media"]))
    return result


def apply_settings_media_restrictions(config, app_settings, **context):
    declarations = settings_media_declarations(app_settings, **context)
    if declarations:
        config["session_media"] = meet(config.get("session_media", {}), *declarations)


def offline_discovery(app_settings):
    """Read registrations without migration, imports, downloads or cache writes."""
    from amplifier_foundation.registry import BundleRegistry
    from amplifier_foundation.settings import read_yaml
    from amplifier_foundation.paths.resolution import ResolvedSource, get_amplifier_home
    from amplifier_foundation.sources.policy import SourceResolutionDenied
    from .lib.bundle_loader.discovery import AppBundleDiscovery

    registry = BundleRegistry(persist=False, strict=True)
    cached = {}
    for name in registry.list_registered():
        state = registry.get_state(name)
        if state and state.local_path:
            cached[state.uri] = Path(state.local_path)

    # Simulate only migration's resulting registration precedence: legacy
    # replaces global, then project/local/session override it. Never rename.
    paths = app_settings.paths
    registrations = {}
    for index, path in enumerate((
        paths.global_settings, paths.project_settings, paths.local_settings, paths.session_settings,
    )):
        # A project can be the home itself. Simulate the migrated global value
        # on a repeated identical path, rather than rereading its old contents.
        if path is not None and (index == 0 or path != paths.global_settings):
            registrations.update(read_yaml(path).get("bundle", {}).get("added", {}))
        if index == 0:
            home = get_amplifier_home()
            legacy = home / "bundle-registry.yaml"
            if legacy.exists() and not (home / "bundle-registry.yaml.migrated").exists():
                for name, info in read_yaml(legacy).get("bundles", {}).items():
                    if isinstance(info, dict) and info.get("uri"):
                        registrations[name] = info["uri"]
    if any(type(k) is not str or type(v) is not str for k, v in registrations.items()):
        raise ValueError("Invalid bundle registrations")

    class OfflineDiscovery(AppBundleDiscovery):
        def __init__(self, **kwargs):
            from threading import Lock
            self._admission_lock = Lock()
            self._admission_failed = False
            self._admitted_uris = set()
            super().__init__(**kwargs)

        def admit_source(self, uri, *, base_path):
            try:
                source = self._local_source(uri, base_path=base_path)
            except Exception:
                # c2 may suppress a transitive dependency error even in strict
                # mode. Keep failure independently of the partial Bundle it
                # returns. No raw URI/error/notes become the public failure.
                with self._admission_lock:
                    self._admission_failed = True
                raise SourceResolutionDenied("Current manifest unavailable") from None
            with self._admission_lock:
                self._admitted_uris.add(uri)
            return source

        async def check_complete(self):
            # Source admission can observe unavailable files/remotes, but an
            # unresolved namespace can fail before the next admission callback.
            # Require every admitted member to finish its OWN public load too.
            # This is not a second composer/parser: Foundation resolves and
            # composes each member using the same registry and scoped policy.
            checked = set()
            while True:
                with self._admission_lock:
                    failed = self._admission_failed
                    pending = self._admitted_uris - checked
                if failed:
                    raise CurrentPolicyIncomplete() from None
                if not pending:
                    return
                for uri in sorted(pending):
                    try:
                        await self.registry.load(uri)
                    except Exception:
                        raise CurrentPolicyIncomplete() from None
                    checked.add(uri)

        def _local_source(self, uri, *, base_path):
            # Exact URI lookup, including ref/subdirectory. A nearby cached
            # checkout is never silently substituted for an unavailable source.
            if uri in cached:
                path = cached[uri]
            elif uri.startswith("file://"):
                parsed = urlsplit(uri)
                if parsed.netloc not in ("", "localhost") or parsed.query:
                    raise SourceResolutionDenied("Nonlocal manifest")
                path = _native_file_uri_path(parsed.path)
                if parsed.fragment:
                    # The supported local bundle form has one subdirectory,
                    # never a query or arbitrary fragment to silently discard.
                    from urllib.parse import parse_qs
                    fragment = parse_qs(parsed.fragment, strict_parsing=True)
                    if set(fragment) != {"subdirectory"} or len(fragment["subdirectory"]) != 1:
                        raise SourceResolutionDenied("Invalid local manifest fragment")
                    subdirectory = Path(fragment["subdirectory"][0])
                    if subdirectory.is_absolute() or ".." in subdirectory.parts:
                        raise SourceResolutionDenied("Local manifest escaped its root")
                    root = path.resolve(strict=True)
                    path = (root / subdirectory).resolve(strict=True)
                    if not root.is_dir() or not path.is_relative_to(root):
                        raise SourceResolutionDenied("Local manifest escaped its root")
                    return ResolvedSource(active_path=path, source_root=root)
            elif "://" not in uri and not uri.startswith(("git+", "zip+")):
                path = Path(uri).expanduser()
                if not path.is_absolute():
                    path = base_path / path
            else:
                raise SourceResolutionDenied("Manifest is not available offline")
            path = path.resolve(strict=True)
            return ResolvedSource(active_path=path, source_root=path.parent if path.is_file() else path)

    return OfflineDiscovery(registry=registry, registrations=registrations, probe_packages=False)


async def resolve_declared_config_async(bundle_name, app_settings, *, session_id, project_slug):
    """Project current host declarations, stopping before agent file metadata.

    This is not a current agent-overlay projection. Bundle agent descriptions
    and module overlays are not host ``session_media`` declarations; the closed
    owner additionally requires actual historical inherited restrictions and
    current per-session settings, never a permissive saved agent config.
    """
    from .runtime.config import resolve_bundle_config

    try:
        config, _ = await resolve_bundle_config(
            bundle_name, app_settings, session_id=session_id, project_slug=project_slug,
            _declarations_only=True,
        )
    except Exception:
        # Error causes/notes from configuration files are not public output.
        # Do not stringify them, including on the root (non-Click) entry point.
        raise CurrentPolicyIncomplete() from None
    return config


def restriction_record(value, *, session_id, expected):
    """Validate bounded restriction DATA and its current selector association."""
    if (type(value) is not dict or set(value) != {"version", "session_id", "prefix", "restrictions"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["session_id"] != session_id or value["prefix"] != asdict(expected)):
        raise ValueError("Historical media restrictions are unavailable or mismatched")
    restrictions = value["restrictions"]
    if type(restrictions) is not dict or set(restrictions) != {"enabled", *_FIELDS}:
        raise ValueError("Invalid historical restrictions")
    return meet(restrictions)


def publication_restrictions(directory, session_id, expected, media_store):
    """Only the exact actual bound store supplies new restriction values."""
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.metadata import SessionMetadataStore

    saved = SessionMetadataStore(directory).read().get(RESTRICTIONS_KEY)
    if saved is not None:
        historical = restriction_record(saved, session_id=session_id, expected=expected)
    elif expected.sequence == 1 and expected.message_count == 0:
        historical = None
    else:
        return None  # No retrospective first-bound truth, even with a fresh store.
    if type(media_store) is not SessionMediaStore:
        return None
    if media_store.session_id != session_id or media_store.root != directory / "media":
        # Do not substitute this auxiliary projection for Foundation's actual
        # source-bound ownership check (including its established refusal).
        return None
    actual = {"enabled": True, **{key: getattr(media_store.policy, key) for key in _FIELDS}}
    return meet(actual, *([historical] if historical is not None else []))


def closed_policy_config(directory, session_id, expected):
    """Current host declarations plus actual inherited restrictions.

    No history/ancestors or fresh agent-frontmatter policy is reconstructed.
    """
    import asyncio
    from amplifier_foundation.session.metadata import SessionMetadataStore
    from .lib.bundle_loader.discovery import DEFAULT_BUNDLE
    from .lib.settings import AppSettings
    from .project_utils import get_project_slug
    from .session_store import extract_session_mode

    metadata = SessionMetadataStore(directory).read()
    historical = restriction_record(
        metadata.get(RESTRICTIONS_KEY), session_id=session_id, expected=expected,
    )
    bundle, _ = extract_session_mode(metadata)
    project_slug = get_project_slug()
    settings = AppSettings().with_session(session_id, project_slug)
    def resolve():
        return asyncio.run(resolve_declared_config_async(
            bundle or DEFAULT_BUNDLE, settings, session_id=session_id, project_slug=project_slug,
        ))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        current = resolve()
    else:
        # Controller fences are synchronous callbacks inside Foundation's async
        # converter. Use a joined, read-only resolver worker, not nested loops.
        # Preserve any enclosing Foundation source-admission context.
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context
        with ThreadPoolExecutor(max_workers=1) as executor:
            current = executor.submit(copy_context().run, resolve).result()
    current["session_media"] = meet(current.get("session_media", {}), historical)
    return current