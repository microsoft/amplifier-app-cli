"""CLI ownership and public mounting for Foundation's session media mechanism.

No vendor encoding, alternate inference path or new transcript layout lives here.
The host chooses explicit limits and a private native-session directory. Producer
enablement is opt-in until the selected dependency graph has been qualified.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import asyncio
import inspect
import os
from typing import Any
import uuid

from .session_store import SessionStore

_HOST = "cli.session_media"
_PREPARE = "cli.media.prepare_provider"


def media_configuration(config: Mapping) -> dict | None:
    """Use shared resource policy; only actual host-supplied quotas narrow it."""
    value = config.get("session_media")
    if value is None:
        return {"enabled": True}
    if not isinstance(value, dict) or set(value) - {
        "enabled", "max_bytes", "max_pixels", "max_images", "retention", "ttl_seconds"
    }:
        raise ValueError("Invalid session_media host policy")
    if "enabled" in value and type(value["enabled"]) is not bool:
        raise ValueError("session_media.enabled must be boolean")
    for key in ("max_bytes", "max_pixels", "max_images"):
        if value.get(key) is not None and (type(value[key]) is not int or value[key] <= 0):
            raise ValueError(f"session_media.{key} must be a positive integer or null")
    return dict(value)


def restrict_child_media(parent, child_config):
    """Narrow a fresh child's host policy; never inherit a live media owner."""
    from amplifier_foundation.session.media import MediaPolicy
    result = deepcopy(child_config)
    parent_policy = media_configuration(parent.config)
    child_policy = media_configuration(child_config)
    policies = [parent_policy, child_policy]
    current = parent.coordinator.get_capability(_HOST)
    if ("session_media" not in parent.config and "session_media" not in child_config
            and not isinstance(current, CLISessionMedia)):
        return result
    if isinstance(current, CLISessionMedia):
        if not current.durable:
            policies.append({"enabled": False})
        else:
            policies.append({
                name: getattr(current.store.policy, name)
                for name in ("max_bytes", "max_pixels", "max_images", "retention", "ttl_seconds")
            })
    narrowed = {"enabled": all(policy.get("enabled", True) for policy in policies)}
    for name in ("max_bytes", "max_pixels", "max_images", "ttl_seconds"):
        values = [policy[name] for policy in policies if policy.get(name) is not None]
        if values:
            narrowed[name] = min(values)
    rank = {"drop": 0, "request-only": 1, "durable": 2}
    # Validate every declaration before selecting the stricter one.
    for policy in policies:
        MediaPolicy(**{name: value for name, value in policy.items() if name != "enabled"})
    narrowed["retention"] = min(
        (policy.get("retention", "durable") for policy in policies), key=rank.__getitem__,
    )
    result["session_media"] = narrowed
    return result


class CLISessionMedia:
    """One coordinator's ownership, never a parent closure or durable authority."""

    def __init__(self, session: Any, native: SessionStore, policy: dict | None) -> None:
        from amplifier_foundation.session.checkpoint import verified_checkpoint_refs
        from amplifier_foundation.session.media import (
            MediaPolicy, NeutralSessionMediaStore, SessionMediaStore, bind_session_media,
        )

        self.session = session
        self.native = native
        self.session_id = session.session_id
        self.generation = uuid.uuid4().hex
        self.wrappers: dict[int, tuple[Any, Any]] = {}
        self.closed = False
        self.durable = policy is not None and policy.get("enabled", True) and os.name == "posix"
        selected = {key: value for key, value in (policy or {}).items() if key != "enabled"}
        # SessionMediaStore validates no-follow path components, private mode,
        # owner and exclusive lifetime lock. Do not normalize away symlinks.
        root = native.base_dir.absolute() / self.session_id / "media"
        self.store = SessionMediaStore(
            root, self.session_id, policy=MediaPolicy(**selected),
            checkpoint_verifier=verified_checkpoint_refs,
            policy_verifier=self._source_policy,
        ) if self.durable else NeutralSessionMediaStore(self.session_id)
        try:
            from .session_hooks import session_hook_coordinator
            coordinator = session_hook_coordinator(session, "host.media")
            self.capability = bind_session_media(
                coordinator, store=self.store,
                enabled=self.durable,
            )
            if self.durable:
                native.bind_media_store(self.session_id, self.store)
                session.coordinator.register_capability(
                    "computer_use.media_policy", self._computer_policy
                )
            session.coordinator.register_cleanup(self.close)
        except BaseException:
            native.unbind_media_store(self.session_id, self.store)
            self.store.close()
            raise

    def _computer_owner(self, ref):
        tools = self.session.coordinator.get("tools") or {}
        for tool in tools.values():
            owns = getattr(tool, "_owns_media_ref", None)
            if callable(owns) and owns(ref) is True:
                return tool
        return None

    def _computer_policy(self, ref, *, purpose):
        """Source classification routes validation; it never restores consent."""
        from amplifier_foundation.session.media import MediaRefusal

        owner = self._computer_owner(ref)
        validate = getattr(owner, "_validate_media_ref", None)
        if not callable(validate):
            raise MediaRefusal("Original screenshot owner is unavailable", code="source_policy")
        try:
            if validate(ref, purpose=purpose) is not True:
                raise ValueError("Screenshot owner refused")
        except ValueError as error:
            raise MediaRefusal("Screenshot owner refused", code="source_policy") from error
        if purpose == "persist":
            self.store.classify_origin(ref, classification="computer-use")
        return True

    def _source_policy(self, ref, *, purpose):
        # Reopening/adoption never rewrites original provenance. A current
        # tighter host TTL is a delivery restriction over that original clock,
        # not permission to renew already-recorded pixels.
        ttl = self.store.policy.ttl_seconds
        if ttl is not None:
            created = datetime.fromisoformat(ref["provenance"]["created_at"])
            if created + timedelta(seconds=ttl) <= datetime.now(UTC):
                from amplifier_foundation.session.media import MediaRefusal
                raise MediaRefusal("Current host observation lifetime expired", code="source_policy")
        classification = self.store.get_origin_classification(ref)
        if classification == "computer-use":
            return self._computer_policy(ref, purpose=purpose)
        if classification is not None:
            from amplifier_foundation.session.media import MediaRefusal
            raise MediaRefusal("Unknown source policy owner", code="source_policy")
        return True

    async def _prepare_overlay(self, request, *, purpose, request_options):
        prepare = self.session.coordinator.get_capability("computer_use.prepare_request")
        if not callable(prepare):
            return {"request": request, "validate": lambda: True}
        result = prepare(request)
        if inspect.isawaitable(result):
            result = await result
        projected, witness = result

        def validate():
            if witness is not None:
                witness()
            return True

        return {"request": projected, "validate": validate}

    def prepare_provider(self, provider: Any, *, alias: str | None = None) -> Any:
        """Wrap the actual selected instance after its native owner installs it."""
        from amplifier_foundation.session.media_provider import MediaPreparedProvider

        if self.closed:
            raise RuntimeError("CLI session media is closed")
        # Reuse only within this coordinator. No parent-owned prepared cache can
        # be installed in a child or resumed coordinator.
        for original, wrapped in self.wrappers.values():
            if provider is wrapped:
                return wrapped
        entry = self.wrappers.get(id(provider))
        if entry is not None and entry[0] is provider:
            return entry[1]
        identity = lambda: {
            "provider": alias or getattr(provider, "name", type(provider).__name__),
            "account": alias or getattr(provider, "name", type(provider).__name__),
            "generation": self.generation,
        }
        bridge = self.session.coordinator.get_capability("session.media.tool_post")
        scope = getattr(bridge, "request_scope", None)
        scope_options = {"request_scope": scope} if callable(scope) else {}
        wrapped = MediaPreparedProvider(
            provider, self.capability, provider_identity=identity,
            execution_scope=None if self.durable else "text_only",
            prepare_overlay=self._prepare_overlay,
            **scope_options,
        )
        self.wrappers[id(provider)] = (provider, wrapped)
        return wrapped

    def invalidate_prepared(self):
        """Called only inside an admitted idle graph transition."""
        if self.durable:
            self.store.revoke()
        self.generation = uuid.uuid4().hex

    async def mount_providers(self) -> None:
        """Use Core's public provider mount, never mutate its mount dictionary."""
        from .session_hooks import session_hook_coordinator
        coordinator = session_hook_coordinator(self.session, "host.media")
        providers = self.session.coordinator.get("providers") or {}
        for alias, provider in list(providers.items()):
            wrapped = self.prepare_provider(provider, alias=alias)
            if wrapped is not provider:
                from .session_hooks import CLIHookGraph, HOOK_GRAPH_CAPABILITY
                graph = self.session.coordinator.get_capability(HOOK_GRAPH_CAPABILITY)
                if isinstance(graph, CLIHookGraph):
                    # Only the issuer can record the actual adapter/source edge.
                    # A generic replacement mount cannot attest this identity.
                    await coordinator.mount_adapter(
                        "providers", wrapped, previous=provider, name=alias,
                    )
                else:
                    await coordinator.mount("providers", wrapped, name=alias)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        errors = []
        try:
            for _, wrapper in self.wrappers.values():
                try:
                    await wrapper.aclose()
                except BaseException as error:
                    errors.append(error)
        finally:
            self.native.unbind_media_store(self.session_id, self.store)
            try:
                self.store.close()
            except BaseException as error:
                errors.append(error)
        if errors:
            # Retain all original handles for diagnosis; an uncertain close is
            # not automatically replayed. Every other wrapper was attempted.
            cancelled = next((error for error in errors if isinstance(error, asyncio.CancelledError)), None)
            if cancelled is not None:
                if len(errors) > 1:
                    raise cancelled from BaseExceptionGroup("Additional media cleanup failures", errors)
                raise cancelled
            raise BaseExceptionGroup("CLI session media cleanup failed", errors)
        self.wrappers.clear()


def install_session_media(
    session: Any, native: SessionStore, *, config: Mapping | None = None,
    restored_messages: list | None = None,
) -> CLISessionMedia | None:
    """Bind before initialize; no defaults, provider selection or host migration."""
    policy = media_configuration(session.config if config is None else config)
    host = CLISessionMedia(session, native, policy)
    session.coordinator.register_capability(_HOST, host)
    session.coordinator.register_capability(_PREPARE, host.prepare_provider)
    if restored_messages is not None and host.durable:
        try:
            native.acknowledge_saved(session.session_id, restored_messages)
        except BaseException:
            # No resumed pixels may be prepared from a stale/serialized receipt.
            native.unbind_media_store(session.session_id, host.store)
            host.store.close()
            raise
    return host


async def mount_session_media(session: Any) -> None:
    """Normal CLI mounts; loop-live performs its own later native selection."""
    host = session.coordinator.get_capability(_HOST)
    if not isinstance(host, CLISessionMedia):
        return
    orchestrator = (session.config.get("session") or {}).get("orchestrator")
    if isinstance(orchestrator, dict):
        orchestrator = orchestrator.get("module")
    if orchestrator == "loop-live":
        # Its CLI host adapter installs native/replacement providers at execute,
        # then consumes cli.media.prepare_provider on that exact selection.
        return
    await host.mount_providers()