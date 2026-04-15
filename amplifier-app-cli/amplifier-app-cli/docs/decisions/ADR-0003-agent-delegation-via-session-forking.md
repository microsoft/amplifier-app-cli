# ADR-0003: Agent Delegation via Session Forking and Configuration Overlays

**Status**: Approved
**Date**: 2025-10-14
**Deciders**: Amplifier Core Team

---

## Context

Need clean approach for delegating specialized tasks to focused execution environments (agents) without kernel complexity.

---

## Decision

Agent delegation is implemented via **session forking with configuration inheritance and overlays**.

**Architecture**:
1. Agents are named configuration overlays (partial mount plans)
2. Sub-sessions are created by forking parent session (`parent_id` parameter)
3. Agent configuration is merged with parent's configuration
4. Everything in mount plan can be overridden (orchestrator, providers, tools, hooks, context)
5. Kernel provides only mechanism (parent_id tracking, session:fork event)
6. All policy lives in app layer (config format, merging, task tool)

---

## Rationale

### Pure Mechanism/Policy Separation

**Kernel provides**:
- `parent_id` parameter on `AmplifierSession`
- `session:fork` event emission
- Parent tracking in child events

**App layer provides**:
- Agent config format (TOML overlays)
- Config merging logic
- Task tool (delegation interface)
- Multi-turn management
- Discovery

**Result**: ~15 lines in kernel, ~200 lines in app layer. Perfect separation.

### Agents as Config Overlays

Agents are partial mount plans, not special abstractions:

```yaml
agents:
  zen-architect:
    description: System design with ruthless simplicity
    providers:
      - module: provider-anthropic
        config:
          model: claude-sonnet-4-5
    tools:
      - module: tool-filesystem
    system:
      instruction: "You are a zen architect..."
```

This is just session configuration. No special types, no protocols, no abstractions.

### Everything Overridable

No arbitrary restrictions:
- Override orchestrator (custom execution loops)
- Override hooks (different observability)
- Override context (different memory)
- Override providers, tools (different capabilities)

**Enables**: Composition through configuration, not flags.

### Ruthless Simplicity

```
Eliminated:
  - Agent protocol (~100 lines from kernel)
  - Agent mount point (~50 lines from kernel)
  - Agent abstractions (~1000 lines from modules)

Added:
  + parent_id tracking (~15 lines in kernel)
  + Config merging (~200 lines in app layer)

Net: -1000 lines, philosophy score 10/10
```

---

## Implementation

### Kernel Changes (Minimal)

```python
# amplifier-core/session.py
class AmplifierSession:
    def __init__(self, config, loader=None, session_id=None, parent_id=None):
        self.parent_id = parent_id  # NEW: Track parent

    async def initialize(self):
        if self.parent_id:
            await self.hooks.emit("session:fork", {"parent": self.parent_id})
        await self.hooks.emit("session:start", {...})

# amplifier-core/events.py
SESSION_FORK = "session:fork"  # NEW
```

**Total**: ~15 lines

### App Layer Implementation

**Config format**: TOML files (agents/*.toml)
**Merging**: Standard dict merge with overlay precedence
**Task tool**: Loads configs, merges, spawns via `parent_id` parameter
**Storage**: Sub-sessions in parent's directory structure

**Total**: ~200 lines

---

## Consequences

### Positive

- ✅ Minimal kernel (~15 lines added)
- ✅ No agent abstraction (agents are config data)
- ✅ Perfect mechanism/policy separation
- ✅ Everything composable through config
- ✅ Text-first (TOML overlays)
- ✅ Supports both simple (config) and complex (custom orchestrator) agents
- ✅ Multi-turn sub-session engagement
- ✅ Session lineage tracking

### Negative

- None identified

### Risks

- Config merging logic must be correct
- Sub-session management adds app complexity
- Multi-turn requires careful session tracking

**Mitigation**: All risks in app layer (can be fixed without kernel changes)

---

## Alternatives Considered

### Alternative 1: Agent Protocol in Kernel

Add `Agent` protocol, agent modules, agent mount point to kernel.

**Rejected**: Violates "policy at edges" - agent behavior is policy, shouldn't be in kernel.

### Alternative 2: No Agent Concept

Just manual session creation for different personas.

**Rejected**: Users need ergonomic delegation. Config overlays provide this without kernel complexity.

### Alternative 3: Capabilities-Based Discovery

Agent registry as hook exposing capabilities.

**Rejected**: Works around kernel mechanisms instead of using them. Adds unnecessary indirection.

---

## Details

### Mount Plan Integration

Agents defined in mount plan as first-class data:

```python
{
    "session": {...},
    "providers": [...],
    "tools": [...],
    "agents": {  # App-layer data (kernel passes through)
        "zen-architect": {
            "description": "...",
            "providers": [...],  # Overlay
            "tools": [...],      # Overlay
            "system": {"instruction": "..."}
        }
    }
}
```

### Sub-Session Creation

```python
# Merge configs
child_config = merge_configs(parent.config, agent_overlay)

# Create child
child = AmplifierSession(
    config=child_config,
    parent_id=parent.session_id  # Kernel mechanism
)

await child.initialize()
# Inject system instruction, execute task
```

---

## Success Metrics

- Parent-child sessions traceable via parent_id
- Config merging works for all mount plan keys
- Task tool enables delegation in <5 lines of user code
- No kernel changes needed for new agent types
- Zero coupling between kernel and agent concept

---

## Related Decisions

- Kernel philosophy: Mechanism not policy
- Session architecture: Minimal kernel additions
- Configuration: Text-first TOML

---

## References

- **→ [SESSION_FORK_SPECIFICATION.md](https://github.com/microsoft/amplifier-core/blob/main/docs/SESSION_FORK_SPECIFICATION.md)** - Kernel mechanism
- **→ [Agent Delegation Implementation](https://github.com/microsoft/amplifier-app-cli/blob/main/docs/AGENT_DELEGATION_IMPLEMENTATION.md)** - App layer policy
- **→ [Bundle Guide](https://github.com/microsoft/amplifier-foundation/blob/main/docs/BUNDLE_GUIDE.md)** - Bundle and agent concepts

---

_This decision establishes agent delegation as session forking with config overlays, keeping the kernel minimal while enabling powerful sub-session patterns at the app layer._
