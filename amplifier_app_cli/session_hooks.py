"""Explicit CLI registration view for the host-owned cooperative hook graph.

This accessor grants no graph completeness or image authority. The owning
before-initialize installer supplies the view; absent it, ordinary hook behavior
is preserved. Core's read-only registry/coordinator properties are never assigned.
"""

from contextlib import contextmanager

HOOK_GRAPH_CAPABILITY = "cli.hooks.participating_graph"


class CLIHookGraph:
    """Host construction handle; receipts remain opaque to the CLI."""

    def __init__(self, session, construction):
        self.session = session
        self.construction = construction
        self.views = {}
        self.sealed = False

    def hooks(self, participant):
        return self.coordinator(participant).hooks

    def coordinator(self, participant):
        if participant not in self.views:
            self.views[participant] = self.construction.participant(participant)
        return self.views[participant]

    def seal(self):
        if not self.sealed:
            self.construction.seal()
            self.sealed = True

    @contextmanager
    def transition(self):
        # Entry must be admitted by the issuer BEFORE any mutation. A failed
        # transition remains the successor's failed generation, never old reseal.
        with self.construction.transition() as successor:
            self.construction = successor
            self.sealed = False
            host = self.session.coordinator.get_capability("cli.session_media")
            if host is not None:
                host.invalidate_prepared()
            yield
        self.sealed = True


def install_session_hook_graph(session, *, owner, host_participants):
    """Install the shared owner before initialization, never infer readiness.

    The public Foundation API owns actual-session/config/loader/native-endpoint
    validation and its issuer-bound receipt. Older dependencies preserve the
    neutral closed lane; a malformed new installer must not silently fall back.
    """
    try:
        from amplifier_foundation.session.media_hook_bridge import (
            install_media_hook_bridge, participating_module_ids,
        )
    except ImportError:
        return None
    from amplifier_core import ModuleCoordinator
    if type(session.coordinator) is not ModuleCoordinator:
        return None  # Legacy test doubles cannot establish a participating graph.
    bridge = install_media_hook_bridge(session.coordinator)
    begin = getattr(bridge, "begin_participating_graph", None)
    if not callable(begin):
        return None
    # Structural inventory comes from the same owner that checks actual loads.
    # It is not independent source approval or permission to retain media.
    participants = list(participating_module_ids(session))
    host_participants = tuple(host_participants) + ("host.configurator", "host.mode")
    if "loop-live" in participants:
        host_participants += ("host.loop-live",)
    participants.extend(name for name in host_participants if name not in participants)
    graph = CLIHookGraph(session, begin(session, owner=owner, participants=tuple(participants)))
    # Retain the declared host views now, including optional/per-turn observers.
    # This is enrollment only: Foundation separately records actual callbacks,
    # mounts and initialization when issuing its opaque construction receipt.
    for name in host_participants:
        graph.coordinator(name)
    session.coordinator.register_capability(HOOK_GRAPH_CAPABILITY, graph)
    # Portable contributors (notably the mode tool) use the host-neutral key.
    # Keep the CLI spelling as an alias to the exact same owner/factory.
    transition_factory = lambda: transition_session_hook_graph(session)
    session.coordinator.register_capability(
        "session.media.graph_transition", transition_factory
    )
    session.coordinator.register_capability(
        "cli.media.graph_transition", transition_factory
    )
    if "loop-live" in participants:
        session.coordinator.register_capability(
            "cli.media.execution_registration", lambda: execution_registration(session)
        )
    return graph


def session_hooks(session, participant):
    return session_hook_coordinator(session, participant).get("hooks")


def session_hook_coordinator(session, participant):
    coordinator = session.coordinator
    getter = getattr(coordinator, "get_capability", None)
    graph = getter(HOOK_GRAPH_CAPABILITY) if callable(getter) else None
    if isinstance(graph, CLIHookGraph):
        if graph.session is not session:
            raise RuntimeError("CLI hook graph belongs to another session")
        return graph.coordinator(participant)
    return coordinator


def seal_session_hook_graph(session):
    graph = session.coordinator.get_capability(HOOK_GRAPH_CAPABILITY)
    if isinstance(graph, CLIHookGraph):
        if graph.session is not session:
            raise RuntimeError("CLI hook graph belongs to another session")
        graph.seal()


@contextmanager
def configure_session_hook_graph(session):
    """Keep post-initialize saved settings inside the initial construction."""
    graph = session.coordinator.get_capability(HOOK_GRAPH_CAPABILITY)
    if isinstance(graph, CLIHookGraph):
        if graph.session is not session:
            raise RuntimeError("CLI hook graph belongs to another session")
        with graph.construction.configure():
            yield
    else:
        yield


@contextmanager
def transition_session_hook_graph(session):
    graph = session.coordinator.get_capability(HOOK_GRAPH_CAPABILITY)
    if isinstance(graph, CLIHookGraph):
        if graph.session is not session:
            raise RuntimeError("CLI hook graph belongs to another session")
        with graph.transition():
            yield
    else:
        yield


@contextmanager
def execution_registration(session):
    """Injected loop-live late registration boundary; never run inference here."""
    graph = session.coordinator.get_capability(HOOK_GRAPH_CAPABILITY)
    if not isinstance(graph, CLIHookGraph) or graph.session is not session:
        raise RuntimeError("Late media registration requires this session's graph")
    if graph.sealed:
        with graph.transition():
            yield graph.coordinator("host.loop-live")
    else:
        with graph.construction.configure():
            yield graph.coordinator("host.loop-live")
        graph.seal()
