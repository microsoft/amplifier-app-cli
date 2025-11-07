# ADR-0004: Task Tool to App Layer Coupling (Technical Debt)

**Status**: Documented (Technical Debt)
**Date**: 2025-11-06
**Context**: vnext-64 implementation

---

## Problem

The `amplifier-module-tool-task` module imports directly from `amplifier-app-cli`:

```python
# amplifier-module-tool-task/__init__.py:160
from amplifier_app_cli.session_spawner import spawn_sub_session
```

This violates the repository awareness hierarchy defined in REPOSITORY_RULES.md:

> **Modules** → ONLY reference core (unaware of peers and apps)

**Why this is technical debt**:
- Module is tightly coupled to specific app implementation
- Cannot be used independently of amplifier-app-cli
- Violates "mechanism at edges" principle
- Makes module harder to test in isolation

---

## Current State (As-Implemented)

The task tool's `execute()` method imports spawner functions from app layer:

```python
# Import from app layer
from amplifier_app_cli.session_spawner import spawn_sub_session, resume_sub_session

# Call app layer function directly
result = await spawn_sub_session(
    agent_name=agent_name,
    instruction=instruction,
    parent_session=parent_session,
    agent_configs=agents,
    sub_session_id=sub_session_id,
)
```

**Impact on vnext-64**: The `resume_sub_session()` implementation follows this same pattern (consistency with existing architecture).

---

## Why We Accept This (For Now)

**Pragmatic reasons**:
1. **Existing pattern**: spawn_sub_session() already uses this approach
2. **Separation of concerns**: Don't mix refactoring with feature work
3. **Working implementation**: Functionality proven and tested
4. **Low immediate risk**: Task tool and app-cli are co-developed and versioned together

**Philosophy alignment**:
- ✅ Ruthless simplicity: Don't solve problems we don't have yet
- ✅ YAGNI: Decouple when proven necessary, not speculatively
- ✅ Vertical slices: Complete vnext-64 functionality first

---

## Future Solution (When Decoupling Justified)

### Option A: Capability-Based Protocol (Preferred)

**App layer registers spawner capability**:
```python
# In app initialization
coordinator.register_capability(
    "session.spawn",
    handler=spawn_sub_session
)

coordinator.register_capability(
    "session.resume",
    handler=resume_sub_session
)
```

**Task tool requests capability**:
```python
# In task tool execute()
spawner = self.coordinator.get_capability("session.spawn")
if not spawner:
    return ToolResult(
        success=False,
        error={"message": "session.spawn capability not available"}
    )

result = await spawner(
    agent_name=agent_name,
    instruction=instruction,
    parent_session=self.coordinator.session,
    agent_configs=self.coordinator.config.get("agents", {}),
)
```

**Benefits**:
- ✅ No direct import from app layer
- ✅ Module only depends on kernel
- ✅ Apps can provide different implementations
- ✅ Clean protocol boundary

**Cost**:
- Need to add capability registry to kernel (mechanism)
- ~100 lines in kernel, ~50 lines in modules

### Option B: Hook-Based Delegation

**App layer provides hook**:
```python
# Hooks observe delegation request
await hooks.emit("delegation:request", {
    "agent": agent_name,
    "instruction": instruction,
    "requester_id": self.coordinator.session_id,
})

# Hook handles spawning and emits response
# delegation:response event contains result
```

**Benefits**:
- ✅ Uses existing hook mechanism
- ✅ No new kernel concepts

**Cost**:
- ❌ Indirect (harder to reason about)
- ❌ Async response handling more complex

---

## Decision for vnext-64

**Accept current coupling** and document as technical debt:

- ✅ Follow existing pattern (consistency)
- ✅ Complete feature without mixing concerns
- ✅ Document the debt explicitly (this ADR)
- ⏭️ Address in future focused effort

**When to revisit**:
- If task tool needs to work with different apps
- If coupling causes maintenance issues
- If testing becomes problematic
- When someone has time for focused refactoring

---

## Recommendation for Future Work

**Timing**: After vnext-64 is stable and tested

**Approach**: Option A (capability-based protocol)
- More explicit than hooks
- Clean protocol boundary
- Testable in isolation
- Kernel mechanism enables app flexibility

**Estimated effort**: ~4 hours (kernel capability registry + module updates + tests)

**Priority**: Medium (improves architecture but not blocking)

---

## Related Decisions

- ADR-0003: Agent Delegation via Session Forking (establishes app layer ownership)
- vnext-64: Multi-Turn Sub-Session Resumption (adds resume_sub_session following pattern)

---

## References

- REPOSITORY_RULES.md - Module awareness hierarchy
- KERNEL_PHILOSOPHY.md - Mechanism not policy
- amplifier-module-tool-task/__init__.py:160 - Current coupling point

---

_This decision documents existing technical debt and defers refactoring to focused future effort. Pragmatic over perfect._
