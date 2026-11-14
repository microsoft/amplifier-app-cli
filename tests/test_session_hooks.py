"""CLI participant routing only; not positive Foundation bridge qualification."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from amplifier_app_cli.session_hooks import (
    CLIHookGraph, HOOK_GRAPH_CAPABILITY, seal_session_hook_graph, session_hooks,
)


class Registry:
    def __init__(self): self.rows = []
    def register(self, event, handler, priority=0, name=None):
        row = (event, handler, priority, name)
        self.rows.append(row)
        def remove():
            if row in self.rows: self.rows.remove(row)
        return remove
    async def emit(self, event, data): return None


def fixture():
    raw, owned = Registry(), Registry()
    view = SimpleNamespace(hooks=owned, get=lambda kind: owned if kind == "hooks" else None)
    construction = SimpleNamespace(participant=MagicMock(return_value=view), seal=MagicMock())
    caps = {}
    selected = SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=caps.get, get=lambda kind: raw if kind == "hooks" else None,
    ))
    graph = CLIHookGraph(selected, construction)
    caps[HOOK_GRAPH_CAPABILITY] = graph
    return selected, raw, owned, construction


def test_explicit_host_view_and_unregister_remain_owner_bound():
    selected, raw, owned, construction = fixture()
    hooks = session_hooks(selected, "host.incremental_save")
    assert hooks is owned and hooks is not raw
    remove = hooks.register("tool:post", lambda e, d: None, 900, "incremental_save")
    assert owned.rows[0][2:] == (900, "incremental_save")
    assert raw.rows == []
    assert session_hooks(selected, "host.incremental_save") is owned
    construction.participant.assert_called_once_with("host.incremental_save")
    remove()
    remove()
    assert owned.rows == []
    seal_session_hook_graph(selected)
    seal_session_hook_graph(selected)
    construction.seal.assert_called_once()


def test_foreign_graph_and_failed_seal_never_gain_local_readiness():
    selected, _, _, construction = fixture()
    graph = selected.coordinator.get_capability(HOOK_GRAPH_CAPABILITY)
    graph.session = object()
    with pytest.raises(RuntimeError, match="another session"):
        session_hooks(selected, "host.trace_collector")
    graph.session = selected
    construction.seal.side_effect = ValueError("unowned post hook")
    with pytest.raises(ValueError, match="unowned"):
        seal_session_hook_graph(selected)
    assert graph.sealed is False


def test_existing_text_hook_host_without_capabilities_preserves_registry():
    raw = Registry()
    selected = SimpleNamespace(coordinator=SimpleNamespace(get=lambda kind: raw))
    assert session_hooks(selected, "host.child_observers") is raw
