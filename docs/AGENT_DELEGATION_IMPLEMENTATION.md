---
last_updated: 2025-11-06
status: stable
audience: developer
layer: App-Layer Implementation
---

# Agent Delegation - amplifier-app-cli Implementation

How the CLI implements agent delegation using amplifier-profiles library and amplifier-core session forking.

**Agent concepts & authoring**: **→ [amplifier-profiles](https://github.com/microsoft/amplifier-profiles/blob/main/docs/AGENT_AUTHORING.md)**
**Kernel mechanism**: **→ [SESSION_FORK_SPECIFICATION.md](https://github.com/microsoft/amplifier-core/blob/main/docs/SESSION_FORK_SPECIFICATION.md)**

---

## Overview

amplifier-app-cli implements agent delegation by:

1. Using `AgentResolver` and `AgentLoader` from amplifier-profiles
2. Resolving agent files from CLI-specific search paths
3. Supporting environment variable overrides for testing
4. Compiling agent configs via `compile_profile_to_mount_plan`
5. Using amplifier-core's `session.fork()` for sub-session creation

---

## CLI-Specific Search Paths

**First-match-wins resolution** (highest → lowest priority):

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Environment Variables                                     │
│    AMPLIFIER_AGENT_ZEN_ARCHITECT=~/test-zen.md             │
│    → For testing changes before committing                  │
├─────────────────────────────────────────────────────────────┤
│ 2. User Directory                                            │
│    ~/.amplifier/agents/zen-architect.md                     │
│    → Personal overrides and custom agents                   │
├─────────────────────────────────────────────────────────────┤
│ 3. Project Directory                                         │
│    .amplifier/agents/project-reviewer.md                    │
│    → Project-specific agents (committed to git)             │
├─────────────────────────────────────────────────────────────┤
│ 4. Collection Agents                                         │
│    collections/developer-expertise/agents/zen-architect.md  │
│    → Agents bundled with collections                        │
└─────────────────────────────────────────────────────────────┘
```

**Environment variable format**: `AMPLIFIER_AGENT_<NAME>` (uppercase, dashes → underscores)

```bash
# Testing agent changes
export AMPLIFIER_AGENT_ZEN_ARCHITECT=~/test-zen.md
amplifier run "design system"  # Uses test version
```

---

## Implementation Details

### Agent Resolution

```python
from amplifier_profiles import AgentResolver
from pathlib import Path
import os

# Build search paths (CLI-specific)
# Collection agents discovered via collection system
# Then project and user directories
search_paths = [
    Path(".amplifier/agents"),                        # Project
    Path.home() / ".amplifier" / "agents",           # User
    # Collection agents added via collection discovery
]

# Create resolver
resolver = AgentResolver(search_paths=search_paths)

# Check environment variable override first (CLI-specific)
agent_name = "zen-architect"
env_var = f"AMPLIFIER_AGENT_{agent_name.upper().replace('-', '_')}"
agent_path = os.environ.get(env_var)

if not agent_path:
    # Fall back to resolver
    agent_path = resolver.resolve(agent_name)

# Load agent
from amplifier_profiles import AgentLoader
loader = AgentLoader(resolver=resolver)
agent = loader.load_agent(agent_name)
```

### Session Forking

Uses amplifier-core's `session.fork()` with agent config overlay:

```python
# In parent session
parent_session = AmplifierSession(config=parent_mount_plan)

# Load agent config
agent = agent_loader.load_agent("zen-architect")
agent_mount_plan_fragment = agent.to_mount_plan_fragment()

# Fork session with agent overlay
sub_session = await parent_session.fork(
    config_overlay=agent_mount_plan_fragment,
    task_description="Design authentication system"
)

# Execute in sub-session
result = await sub_session.execute("Design the auth system")
```

### Multi-Turn Sub-Session Resumption

Sub-sessions support multi-turn conversations through automatic state persistence. When a sub-session completes, its state (transcript and configuration) is saved to persistent storage, enabling the parent session to resume the conversation across multiple turns.

#### State Persistence

The system automatically persists sub-session state after each execution:

```python
# After sub-session execution, before cleanup
from amplifier_app_cli.session_store import SessionStore

# Capture current state
context = child_session.coordinator.get("context")
transcript = await context.get_messages() if context else []

metadata = {
    "session_id": sub_session_id,
    "parent_id": parent_session.session_id,
    "agent_name": agent_name,
    "created": datetime.now(UTC).isoformat(),
    "config": merged_config,  # Full merged mount plan
    "agent_overlay": agent_config,  # Original agent config
}

# Persist to storage
store = SessionStore()  # Project-scoped: ~/.amplifier/projects/{project}/sessions/
store.save(sub_session_id, transcript, metadata)
```

**Storage Location**: `~/.amplifier/projects/{project-slug}/sessions/{session-id}/`
- `transcript.jsonl` - Conversation history
- `metadata.json` - Session configuration and metadata
- `profile.md` - Profile snapshot (if applicable)

#### Resuming Existing Sessions

Resume a previous sub-session by providing its `session_id`:

```python
from amplifier_app_cli.session_spawner import resume_sub_session

# Resume by session ID
result = await resume_sub_session(
    sub_session_id="parent-123-zen-architect-abc456",
    instruction="Now add OAuth 2.0 support"
)
# Returns: {"output": str, "session_id": str}
```

**Resume Process**:
1. Load transcript and metadata from `SessionStore`
2. Recreate `AmplifierSession` with stored configuration
3. Restore transcript to context via `add_message()`
4. Execute new instruction with full conversation history
5. Save updated state
6. Cleanup and return

**Key Design Points**:
- **Stateless**: Each resume loads fresh from disk (no in-memory caching)
- **Deterministic**: Uses stored merged config (independent of parent changes)
- **Self-contained**: All state needed for reconstruction persists with session
- **Resumable**: Survives parent session restarts and crashes

#### Task Tool Integration

The task tool provides a unified interface for both spawning new sub-sessions and resuming existing ones:

**Spawn new sub-session** (agent parameter required):
```python
# Via task tool
result = tool_execute({
    "agent": "zen-architect",
    "instruction": "Design authentication system"
})
# Returns: {"response": str, "session_id": "parent-123-zen-architect-abc456"}
```

**Resume existing sub-session** (session_id parameter triggers resume):
```python
# Via task tool - note session_id instead of agent
result = tool_execute({
    "session_id": "parent-123-zen-architect-abc456",  # From previous spawn
    "instruction": "Add OAuth 2.0 support"
})
# Returns: {"response": str, "session_id": "parent-123-zen-architect-abc456"}
```

**Input Schema**:
```python
{
    "agent": str,          # Optional - required for spawn, not needed for resume
    "instruction": str,     # Required - task for agent to execute
    "session_id": str,     # Optional - when provided, triggers resume instead of spawn
}
```

**Routing Logic**: If `session_id` provided → `resume_sub_session()`, else → `spawn_sub_session()`

#### Multi-Turn Example

```python
# Turn 1: Initial delegation
response1 = await task_tool.execute({
    "agent": "zen-architect",
    "instruction": "Design a caching system"
})
session_id = response1["session_id"]  # Save for later

# Turn 2: Resume with refinement
response2 = await task_tool.execute({
    "session_id": session_id,
    "instruction": "Add TTL support to the cache"
})

# Turn 3: Continue iteration
response3 = await task_tool.execute({
    "session_id": session_id,
    "instruction": "Add eviction policies"
})

# Each turn builds on previous context
```

#### Error Handling

**Missing Session**:
```python
# Attempting to resume non-existent session
try:
    await resume_sub_session("fake-id", "test")
except FileNotFoundError as e:
    print(f"Session not found: {e}")
    # Error: "Sub-session 'fake-id' not found. Session may have expired..."
```

**Corrupted Metadata**:
```python
# If metadata.json is corrupted
try:
    await resume_sub_session("corrupted-id", "test")
except RuntimeError as e:
    print(f"Session corrupted: {e}")
    # Error: "Corrupted session metadata for 'corrupted-id'..."
```

**Observability**: Resume operations emit `session:resume` events for monitoring and debugging.

---

## CLI Commands

### Agent Listing

```bash
# List all agents (includes collection agents)
amplifier agent list

# Example output:
# zen-architect                           developer-expertise
# bug-hunter                              developer-expertise
# design-intelligence:art-director        user-collection
# project-reviewer                        project
```

### Agent Inspection

```bash
# Show agent configuration
amplifier agent show zen-architect

# Output: Agent config in YAML format
```

### Using Agents in Sessions

Agents are loaded automatically when profiles specify them:

```yaml
# In profile.md (Smart Single Value format)
agents: all   # Load all discovered agents
# Or: agents: [zen-architect, bug-hunter]  # Load specific agents
# Or: agents: none                          # Disable agents
```

Then use via task tool delegation within sessions (see amplifier-profiles docs for details).

---

## Environment Variable Override Examples

### Testing Agent Changes

```bash
# Test modified agent without committing
export AMPLIFIER_AGENT_ZEN_ARCHITECT=~/work/test-zen.md
amplifier run "design system"  # Uses test version

# Unset to use normal resolution
unset AMPLIFIER_AGENT_ZEN_ARCHITECT
```

### Temporary Agent Injection

```bash
# Use one-off agent for specific task
export AMPLIFIER_AGENT_SPECIAL_ANALYZER=/tmp/special.md
amplifier run "analyze codebase"
unset AMPLIFIER_AGENT_SPECIAL_ANALYZER
```

---

## Integration with amplifier-profiles Library

amplifier-app-cli uses amplifier-profiles library for ALL agent functionality:

**What CLI provides** (policy):
- CLI-specific search paths (user, project, collection directories)
- Environment variable override mechanism
- CLI commands for listing/showing agents
- Integration with profile and collection systems

**What library provides** (mechanism):
- Agent file format parsing (markdown + YAML frontmatter)
- AgentResolver (path-based resolution)
- AgentLoader (loading and parsing agent files)
- Agent schemas (Agent, AgentMeta, SystemConfig)
- First-match-wins resolution logic

**Boundary**: CLI calls library APIs, library doesn't know about CLI.

---

## Related Documentation

**Agent Concepts**:
- **→ [amplifier-profiles](https://github.com/microsoft/amplifier-profiles)** - Agent system design and API
- **→ [Agent Authoring Guide](https://github.com/microsoft/amplifier-profiles/blob/main/docs/AGENT_AUTHORING.md)** - How to create agents

**Kernel Mechanism**:
- **→ [SESSION_FORK_SPECIFICATION.md](https://github.com/microsoft/amplifier-core/blob/main/docs/SESSION_FORK_SPECIFICATION.md)** - Session forking contract

---

**Document Version**: 1.1
**Last Updated**: 2025-11-06 (Added multi-turn sub-session resumption)
