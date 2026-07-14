from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.runtime.session_persistence import (
    InteractiveSessionPersistence,
)
from amplifier_app_cli.ui.interaction_state import TRUST_POLICY_VERSION
from amplifier_app_cli.ui.interaction_state import TrustState
from amplifier_app_cli.ui.interaction_runtime_state import InteractionRuntimeState
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger


@pytest.mark.asyncio
async def test_interactive_session_persistence_owns_runtime_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    context = MagicMock()
    context.get_messages = AsyncMock(
        return_value=[{"role": "user", "content": "hi"}, {"role": "assistant"}]
    )
    coordinator = MagicMock()
    coordinator.get.return_value = context
    coordinator.session_state = {"ui.show_debug": True}
    session = MagicMock()
    session.coordinator = coordinator
    store = MagicMock()
    store.get_metadata.return_value = {"name": "kept", "created": "earlier"}
    trust = TrustState(initial="build")
    interaction = InteractionRuntimeState(coordinator.session_state, trust)

    persistence = InteractiveSessionPersistence(
        session=session,
        store=store,
        session_id="session-1",
        bundle_name="foundation",
        config={"providers": [{"config": {"model": "gpt-test"}}]},
        interaction_state=interaction,
        outcome_ledger=OutcomeLedger(),
        runtime_status=None,
    )
    await persistence.save()

    _, messages, metadata = store.save.call_args.args
    assert messages[0]["content"] == "hi"
    assert metadata["name"] == "kept"
    assert metadata["created"] == "earlier"
    assert metadata["model"] == "gpt-test"
    assert metadata["turn_count"] == 1
    assert metadata["permission_posture"] == "build"
    assert metadata["permission_policy_version"] == TRUST_POLICY_VERSION
    assert metadata["session_cost_usd"] == str(Decimal("0"))
    assert metadata["ui_mode"] == "chat"


@pytest.mark.asyncio
async def test_persistence_skips_session_without_context() -> None:
    coordinator = MagicMock()
    coordinator.get.return_value = None
    session = MagicMock()
    session.coordinator = coordinator
    store = MagicMock()
    state: dict[str, object] = {}
    interaction = InteractionRuntimeState(state, TrustState())
    persistence = InteractiveSessionPersistence(
        session=session,
        store=store,
        session_id="session-1",
        bundle_name="foundation",
        config={},
        interaction_state=interaction,
        outcome_ledger=OutcomeLedger(),
        runtime_status=None,
    )

    await persistence.save()

    store.save.assert_not_called()
