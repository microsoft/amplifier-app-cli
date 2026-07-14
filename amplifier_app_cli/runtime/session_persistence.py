"""Durable interactive-session metadata and transcript persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.runtime.session_state import coordinator_session_state
from amplifier_app_cli.runtime.session_access import session_coordinator
from amplifier_app_cli.ui.interaction_state import TRUST_POLICY_VERSION
from amplifier_app_cli.ui.interaction_runtime_state import InteractionRuntimeState
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger
from amplifier_app_cli.ui.runtime_status import RuntimeStatusTracker


class InteractiveSessionPersistence:
    """Persist one interactive session without coupling storage to the REPL."""

    def __init__(
        self,
        *,
        session: object,
        store: SessionStore,
        session_id: str,
        bundle_name: str,
        config: dict[str, Any],
        interaction_state: InteractionRuntimeState,
        outcome_ledger: OutcomeLedger,
        runtime_status: RuntimeStatusTracker | None,
    ) -> None:
        self._coordinator = session_coordinator(session)
        self._store = store
        self._session_id = session_id
        self._bundle_name = bundle_name
        self._config = config
        self._interaction_state = interaction_state
        self._outcome_ledger = outcome_ledger
        self._runtime_status = runtime_status

    async def save(self) -> None:
        context = self._coordinator.get("context")
        if context is None or not hasattr(context, "get_messages"):
            return
        messages = await context.get_messages()
        try:
            existing = self._store.get_metadata(self._session_id) or {}
        except FileNotFoundError:
            existing = {}
        state = coordinator_session_state(self._coordinator)
        interaction = self._interaction_state.snapshot
        trust = self._interaction_state.trust
        session_cost = (
            self._runtime_status.telemetry_snapshot().session.cost_usd
            if self._runtime_status is not None
            else None
        )
        metadata = {
            **existing,
            "session_id": self._session_id,
            "created": existing.get("created", datetime.now(UTC).isoformat()),
            "bundle": self._bundle_name,
            "model": self._model_name(),
            "turn_count": sum(message.get("role") == "user" for message in messages),
            "working_dir": str(Path.cwd().resolve()),
            "active_mode": interaction.bundle_mode,
            "ui_mode": interaction.ui_mode,
            "permission_posture": interaction.permission_posture,
            "permission_profile": trust.snapshot(),
            "permission_policy_version": TRUST_POLICY_VERSION,
            "show_debug": bool(state.get("ui.show_debug")),
            "session_cost_usd": str(session_cost or Decimal("0")),
            "outcome_ledger": self._outcome_ledger.as_records(),
        }
        self._store.save(self._session_id, messages, metadata)

    def _model_name(self) -> str:
        providers = self._config.get("providers")
        if not isinstance(providers, list) or not providers:
            return "unknown"
        first_provider = providers[0]
        if not isinstance(first_provider, dict):
            return "unknown"
        provider_config = first_provider.get("config")
        if not isinstance(provider_config, dict):
            return "unknown"
        value = provider_config.get("model") or provider_config.get(
            "default_model", "unknown"
        )
        return str(value)


__all__ = ["InteractiveSessionPersistence"]
