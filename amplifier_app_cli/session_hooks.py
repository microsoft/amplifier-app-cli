"""Explicit CLI registration view for the host-owned cooperative hook graph.

This accessor grants no graph completeness or image authority. The owning
before-initialize installer supplies the view; absent it, ordinary hook behavior
is preserved. Core's read-only registry/coordinator properties are never assigned.
"""

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


def install_session_hook_graph(session, *, owner, host_participants):
    """Install the shared owner before initialization, never infer readiness.

    The public Foundation API owns actual-session/config/loader/native-endpoint
    validation and its issuer-bound receipt. Older dependencies preserve the
    neutral closed lane; a malformed new installer must not silently fall back.
    """
    try:
        from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge
    except ImportError:
        return None
    from amplifier_core import ModuleCoordinator
    if type(session.coordinator) is not ModuleCoordinator:
        return None  # Legacy test doubles cannot establish a participating graph.
    bridge = install_media_hook_bridge(session.coordinator)
    begin = getattr(bridge, "begin_participating_graph", None)
    if not callable(begin):
        return None
    config = session.config
    specs = [(config.get("session") or {}).get(kind, default) for kind, default in (
        ("orchestrator", "loop-basic"), ("context", "context-simple"),
    )]
    specs.extend(row for kind in ("providers", "tools", "hooks") for row in config.get(kind, []))
    participants = []
    for spec in specs:
        identity = spec if isinstance(spec, str) else spec.get("module") if isinstance(spec, dict) else None
        if identity and identity not in participants:
            participants.append(identity)
    participants.extend(name for name in host_participants if name not in participants)
    graph = CLIHookGraph(session, begin(session, owner=owner, participants=tuple(participants)))
    # Retain the declared host views now, including optional/per-turn observers.
    # This is enrollment only: Foundation separately records actual callbacks,
    # mounts and initialization when issuing its opaque construction receipt.
    for name in host_participants:
        graph.coordinator(name)
    session.coordinator.register_capability(HOOK_GRAPH_CAPABILITY, graph)
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
