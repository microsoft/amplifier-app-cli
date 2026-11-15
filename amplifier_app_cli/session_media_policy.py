"""Configuration-only current declarations and restriction-only historical data.

Neither metadata nor a cached manifest grants source consent. Missing historical
capture cannot be reconstructed on resume. The metadata selector is a mismatch
join, NOT a cryptographic policy-to-transcript binding.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextvars import ContextVar
from contextlib import contextmanager
import hashlib
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

RESTRICTIONS_KEY = "cli_media_restrictions_v1"
_FIELDS = ("max_bytes", "max_pixels", "max_images", "retention", "ttl_seconds")
_RANK = {"drop": 0, "request-only": 1, "durable": 2}
_POLICY_READS = ContextVar("cli_policy_reads", default=None)
_POLICY_REUSE = ContextVar("cli_owner_policy_reuse", default=None)


def _environment_digest():
    digest = hashlib.sha256()
    for key, value in sorted(os.environ.items()):
        for item in (key, value):
            raw = item.encode("utf-8", "surrogatepass")
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    return digest.digest()


def _path_fact(path, content):
    """Bounded byte witness plus lexical namespace identity, never mtime-only."""
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    if stat.S_ISLNK(info.st_mode):
        return identity, os.readlink(path), _path_fact(os.path.realpath(path), content)
    if not content or stat.S_ISDIR(info.st_mode):
        return identity
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
        raise CurrentPolicyIncomplete()
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        start = os.fstat(stream.fileno())
        while chunk := stream.read(65536):
            digest.update(chunk)
        end = os.fstat(stream.fileno())
    if ((start.st_dev, start.st_ino, start.st_size, start.st_mtime_ns, start.st_ctime_ns)
            != (end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns, end.st_ctime_ns)
            or start.st_ino != info.st_ino or start.st_dev != info.st_dev):
        raise CurrentPolicyIncomplete()
    return identity, end.st_size, digest.digest()


@dataclass(frozen=True)
class PolicyReadWitness:
    """Operation-local immutable read set; no registry, config or secrets kept."""

    _entries: tuple
    _environment: bytes
    _cwd: str

    def check(self):
        return self._check()

    def _check(self, *, replaced_metadata=None):
        try:
            return (os.getcwd() == self._cwd
                    and _environment_digest() == self._environment
                    and all(_path_fact(path, content) == fact
                            for path, content, fact in self._entries
                            if path != replaced_metadata))
        except (OSError, ValueError, RuntimeError):
            return False


class _PolicyReadCapture:
    def __init__(self):
        self.entries = {}
        self.environment = _environment_digest()
        self.cwd = os.getcwd()
        self.complete = True
        from amplifier_foundation.paths.resolution import get_amplifier_home

        self.watch(get_amplifier_home(), content=False)
        # The public helper owns home selection and canonicalizes aliases.
        # Only a nonempty explicit input supplies lexical provenance we can
        # additionally observe without duplicating its default-home policy.
        raw_home = os.environ.get("AMPLIFIER_HOME")
        if raw_home:
            self.watch(Path(raw_home).expanduser(), content=False)
        else:
            # Default lexical aliases are not exposed by the public helper.
            # Current resolution remains useful, but every guard must resolve
            # again rather than reuse a potentially incomplete home witness.
            self.complete = False

    def watch(self, path, *, content=True):
        if path is None:
            return
        if ".." in Path(path).parts:
            # abspath collapses '..' lexically, whereas resolve may traverse a
            # symlink first. Without that lexical traversal witness, do not
            # cache this graph; ordinary fresh composition remains available.
            self.complete = False
        path = os.path.abspath(os.fspath(path))
        # Record lexical components too: retargeting a symlink/ancestor must
        # invalidate even when the selected file has identical bytes.
        parent = os.path.dirname(path)
        if parent != path:
            self.watch(parent, content=False)
        key = path, content
        fact = _path_fact(path, content)
        if key in self.entries and self.entries[key] != fact:
            raise CurrentPolicyIncomplete()
        self.entries[key] = fact

    def source(self, path):
        path = Path(path)
        self.watch(path)
        if path.is_dir():
            self.watch(path / "bundle.md")
            self.watch(path / "bundle.yaml")
        # Foundation's containing-root search may discover a new ancestor
        # manifest. Capture presence AND absence without parsing anything.
        for parent in path.parents:
            self.watch(parent / "bundle.md")
            self.watch(parent / "bundle.yaml")

    def freeze(self):
        if not self.complete:
            return None
        witness = PolicyReadWitness(
            tuple((path, content, fact) for (path, content), fact in self.entries.items()),
            self.environment, self.cwd,
        )
        if not witness.check():
            raise CurrentPolicyIncomplete()
        return witness


class OwnerPolicyReuse:
    """One cleanup owner's alternate-prefix projection, never cross-owner."""

    def __init__(self):
        self._entry = None
        self._invalidated = False

    def config(self, directory, session_id, expected):
        if self._invalidated:
            raise CurrentPolicyIncomplete()
        key = os.fspath(directory), session_id, expected
        if self._entry is not None:
            saved_key, declaration, witness = self._entry
            if key[:2] != saved_key[:2]:
                self._invalidated = True
                raise CurrentPolicyIncomplete()
            if key == saved_key:
                if not witness.check():
                    self._invalidated = True
                    raise CurrentPolicyIncomplete()
                return {"session_media": dict(declaration)}
            # An admitted owner may advance its exact selected prefix after a
            # typed ACK and auxiliary publication. Do not reuse the old record
            # or infer authority from that movement: fully resolve/capture the
            # new prefix before this guard can return current policy.
            self._entry = None
        config, witness = capture_closed_policy_config(directory, session_id, expected)
        declaration = tuple(config["session_media"].items())
        if witness is not None:
            self._entry = key, declaration, witness
        return {"session_media": dict(declaration)}

    def _publication_preimage(self):
        """Host writer only: obtain an already validated local witness."""
        if self._invalidated:
            raise CurrentPolicyIncomplete()
        return self._entry[2] if self._entry is not None else None

    def _install_publication(self, directory, session_id, expected, config, witness):
        # Called only after the cleanup owner has performed and verified its
        # restricted metadata publication. This is not refresh-on-change.
        if self._invalidated:
            raise CurrentPolicyIncomplete()
        self._entry = ((os.fspath(directory), session_id, expected),
                       tuple(config["session_media"].items()), witness) if witness is not None else None


@contextmanager
def owner_policy_scope(reuse):
    token = _POLICY_REUSE.set(reuse)
    try:
        yield
    finally:
        _POLICY_REUSE.reset(token)


def _watch(path):
    capture = _POLICY_READS.get()
    if capture is not None:
        capture.watch(path)


def _watch_settings(settings):
    for path in vars(settings.paths).values():
        _watch(path)


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
    _watch_settings(effective)
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

    capture = _POLICY_READS.get()
    _watch_settings(app_settings)
    home = get_amplifier_home()
    for name in ("registry.json", "bundle-registry.yaml", "bundle-registry.yaml.migrated"):
        _watch(home / name)
    class WitnessRegistry(BundleRegistry):
        def set_include_source_resolver(self, resolver):
            def observe(source):
                if capture is not None and "://" not in source:
                    capture.complete = False
                return resolver(source) if resolver is not None else None
            super().set_include_source_resolver(observe)

    registry = WitnessRegistry(persist=False, strict=True)
    registry.set_include_source_resolver(None)
    cached = {}
    for name in registry.list_registered():
        state = registry.get_state(name)
        if capture is not None and state is not None and not state.local_path:
            # Construction may have cleared a persisted missing local_path
            # before this public enumeration. Its original lexical absence is
            # no longer observable without interpreting registry internals.
            # Do not cache a graph with that uncertainty: a restored old path
            # can change exact URI selection despite unchanged registry bytes.
            capture.complete = False
        if state and state.local_path:
            cached[state.uri] = Path(state.local_path)
            if capture is not None:
                capture.watch(state.local_path, content=False)

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
        def _default_search_paths(self):
            if capture is not None:
                for path in (
                    Path.cwd() / ".amplifier" / "bundles", home / "bundles",
                    Path(__file__).parent / "data" / "bundles",
                ):
                    capture.watch(path, content=False)
            return super()._default_search_paths()

        def find(self, name):
            if capture is not None:
                for root in (
                    Path.cwd() / ".amplifier" / "bundles", home / "bundles",
                    Path(__file__).parent / "data" / "bundles",
                ):
                    for suffix in ("", ".yaml", ".md", "/bundle.md", "/bundle.yaml"):
                        capture.watch(root / (name + suffix))
            return super().find(name)

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
            if capture is not None:
                capture.source(source.active_path)
                capture.source(source.source_root)
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
                        bundle = await self.registry.load(uri)
                    except Exception:
                        raise CurrentPolicyIncomplete() from None
                    if capture is not None:
                        # No public lexical namespace read set exists in c2.
                        # Indirect graphs keep full composition on every guard.
                        for include in bundle.includes:
                            value = include if isinstance(include, str) else include.get("bundle")
                            if not isinstance(value, str) or "://" not in value:
                                capture.complete = False
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
                    if capture is not None:
                        capture.source(path / subdirectory)
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
            if capture is not None:
                capture.source(path)
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
    reuse = _POLICY_REUSE.get()
    if reuse is not None and _POLICY_READS.get() is None:
        return reuse.config(directory, session_id, expected)
    import asyncio
    from amplifier_foundation.session.metadata import SessionMetadataStore
    from .lib.bundle_loader.discovery import DEFAULT_BUNDLE
    from .lib.settings import AppSettings
    from .project_utils import get_project_slug
    from .session_store import extract_session_mode

    _watch(directory / "metadata.json")
    metadata = SessionMetadataStore(directory).read()
    historical = restriction_record(
        metadata.get(RESTRICTIONS_KEY), session_id=session_id, expected=expected,
    )
    bundle, _ = extract_session_mode(metadata)
    project_slug = get_project_slug()
    settings = AppSettings().with_session(session_id, project_slug)
    _watch_settings(settings)
    capture = _POLICY_READS.get()
    if capture is not None:
        # The CLI selects packaged/source behavior layout before load_bundle.
        from .runtime.config import _APP_CLI_PACKAGE_DIR, _APP_CLI_BEHAVIOR_RELPATH
        for root in (_APP_CLI_PACKAGE_DIR / "_bundle", _APP_CLI_PACKAGE_DIR.parent):
            capture.watch(root / _APP_CLI_BEHAVIOR_RELPATH)
        # Runtime's optional user routing directory selection is also an input.
        from .lib.settings import get_custom_routing_dir
        capture.watch(get_custom_routing_dir(), content=False)
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


def capture_closed_policy_config(directory, session_id, expected):
    """Resolve once and return a verified per-owner read set, or no-cache."""
    capture = _PolicyReadCapture()
    token = _POLICY_READS.set(capture)
    try:
        config = closed_policy_config(directory, session_id, expected)
        return config, capture.freeze()
    finally:
        _POLICY_READS.reset(token)