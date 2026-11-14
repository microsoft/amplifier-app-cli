"""CLI ownership and public mounting for Foundation's session media mechanism.

No vendor encoding, alternate inference path or new transcript layout lives here.
The host chooses explicit limits and a private native-session directory. Producer
enablement is opt-in until the selected dependency graph has been qualified.
"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
import os
from typing import Any
import uuid

from .session_store import SessionStore

_HOST = "cli.session_media"
_PREPARE = "cli.media.prepare_provider"


def media_configuration(config: Mapping) -> dict | None:
    """Validate explicit host policy; absence is the unchanged no-media policy."""
    value = config.get("session_media")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "enabled", "max_bytes", "max_pixels", "max_images", "retention", "ttl_seconds"
    }:
        raise ValueError("Invalid session_media host policy")
    if "enabled" in value and type(value["enabled"]) is not bool:
        raise ValueError("session_media.enabled must be boolean")
    for key in ("max_bytes", "max_pixels", "max_images"):
        if type(value.get(key)) is not int or value[key] <= 0:
            raise ValueError(f"session_media.{key} must be a positive integer")
    return dict(value)


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
        self.durable = policy is not None and os.name == "posix"
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
            self.capability = bind_session_media(
                session.coordinator, store=self.store,
                enabled=self.durable and policy.get("enabled", False),
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
        identity = {
            "provider": alias or getattr(provider, "name", type(provider).__name__),
            "account": alias or getattr(provider, "name", type(provider).__name__),
            "generation": self.generation,
        }
        wrapped = MediaPreparedProvider(
            provider, self.capability, provider_identity=identity,
            execution_scope=None if self.durable else "text_only",
            prepare_overlay=self._prepare_overlay,
        )
        self.wrappers[id(provider)] = (provider, wrapped)
        return wrapped

    async def mount_providers(self) -> None:
        """Use Core's public provider mount, never mutate its mount dictionary."""
        providers = self.session.coordinator.get("providers") or {}
        for alias, provider in list(providers.items()):
            wrapped = self.prepare_provider(provider, alias=alias)
            if wrapped is not provider:
                await self.session.coordinator.mount("providers", wrapped, name=alias)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            for _, wrapper in self.wrappers.values():
                await wrapper.aclose()
        finally:
            self.wrappers.clear()
            self.native.unbind_media_store(self.session_id, self.store)
            self.store.close()


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
    if orchestrator == "loop-live":
        # Its CLI host adapter installs native/replacement providers at execute,
        # then consumes cli.media.prepare_provider on that exact selection.
        return
    await host.mount_providers()