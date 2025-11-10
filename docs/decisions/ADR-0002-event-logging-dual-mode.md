# ADR-0002: Per-Session Event Logging

**Status**: Implemented
**Date**: 2025-10-14
**Authors**: Brian Krabach
**Deciders**: Amplifier Core Team
**Relates to**: ADR-0001 (Project-Scoped Sessions)

---

## Context

Session debugging requires complete visibility: transcript + events (tool calls, approvals, errors, timing, tokens). Splitting across files makes correlation hard and prevents self-contained sessions.

---

## Decision

All events for a session are written to a per-session `events.jsonl` file within the session directory:

```
~/.amplifier/projects/<project-slug>/sessions/<session-id>/events.jsonl
```

This makes each session completely self-contained with all its data in one directory.

---

## Rationale

### Self-Contained Sessions

Having transcript + events + metadata in the same directory provides:

- **Complete traces**: Everything about a session in one place
- **Easy debugging**: No need to grep through global logs or correlate across files
- **Simple archival**: `tar -czf session.tar.gz ~/.amplifier/projects/.../sessions/abc-123/` = complete backup
- **Clean sharing**: Send session directory for bug reports with full context
- **Atomic cleanup**: Delete session directory = all data gone

### No Working Directory Clutter

Writing logs to `~/.amplifier/projects/` instead of `./amplifier.log.jsonl` in the working directory:

- **Cleaner projects**: No log files dropped in git repos or user projects
- **Centralized data**: All amplifier user data in `~/.amplifier/`
- **Clear separation**: Project code (working dir) vs user data (home dir)

### Session-Only Mode

The logging hook operates in `session-only` mode by default, writing events exclusively to per-session logs. This:

- **Simplifies**: One logging path, not dual-mode complexity
- **Aligns with UX**: Users debug specific sessions, not cross-session patterns
- **Reduces I/O**: Write once per event, not twice

---

## Implementation Details

### Logging Hook Configuration

```yaml
hooks:
  - module: hooks-logging
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
```

### Event Writing

The logging hook receives events from the kernel and writes them to the session-specific log:

```python
class LoggingHook:
    def __init__(self, config):
        self.mode = config.get("mode", "session-only")
        self.session_log_template = config.get(
            "session_log_template",
            "~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl"
        )

    async def handle_event(self, event: str, data: dict):
        """Write event to session log."""
        session_id = data.get("session_id")
        if not session_id:
            return  # No session context, skip

        project_slug = get_project_slug()
        session_log_path = Path(
            self.session_log_template.format(
                project=project_slug,
                session_id=session_id
            )
        ).expanduser()

        session_log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(session_log_path, "a") as f:
            json.dump({"ts": ..., "event": event, **data}, f, ensure_ascii=False)
            f.write("\n")
```

### Session Directory Structure

Complete self-contained session:

```
~/.amplifier/projects/-home-user-repos-myapp/sessions/abc-123/
├── transcript.jsonl       # Anthropic messages (for resumption)
├── events.jsonl          # All amplifier events (for debugging)
└── metadata.json         # Session info

# events.jsonl contains:
{"ts": "...", "event": "session:start", "session_id": "abc-123", ...}
{"ts": "...", "event": "prompt:submit", "data": {"prompt": "..."}, ...}
{"ts": "...", "event": "tool:pre", "data": {"tool": "bash", "args": {...}}, ...}
{"ts": "...", "event": "tool:post", "data": {"result": {...}}, ...}
{"ts": "...", "event": "approval:required", ...}
{"ts": "...", "event": "approval:granted", ...}
{"ts": "...", "event": "provider:response", "data": {"usage": {...}}, ...}
{"ts": "...", "event": "session:end", ...}
```

---

## Benefits for Debugging

**Unified debugging**:

```bash
$ cd ~/.amplifier/projects/-home-user-repos-myapp/sessions/abc-123/
$ cat events.jsonl | jq 'select(.event | contains("tool"))'
# All tool events right here - calls, results, errors, timing

$ cat transcript.jsonl | jq '.[] | select(.role == "user") | .content'
# User messages

$ cat metadata.json
# Session configuration, profile used, etc.
```

Everything in one directory, easy to inspect and correlate.

---

## Examples

### Use Case 1: Debug Why Tool Failed

```bash
$ cd ~/repos/myproject
$ amplifier sessions list
abc-123  "Build feature X"  5 messages  2025-10-14

$ cd ~/.amplifier/projects/-home-user-repos-myproject/sessions/abc-123/
$ cat events.jsonl | jq 'select(.event | startswith("tool:"))'
# Shows: tool:pre, approval:required, approval:granted, tool:post, tool:error
# Complete timeline of what happened
```

### Use Case 2: Archive Session for Bug Report

```bash
$ cd ~/.amplifier/projects/-home-user-repos-myapp/sessions/abc-123/
$ tar -czf ~/bug-report-session.tar.gz .
# Complete session trace for sharing
```

### Use Case 3: Project Cleanup

```bash
$ rm -rf ~/.amplifier/projects/-home-user-repos-old-project/
# Removes all sessions and logs for old-project
```

---

## Alternative Considered: Dual-Mode Logging

We considered writing events to BOTH per-session logs AND a global log file. This was rejected because:

- **Complexity**: More code, more configuration, more failure modes
- **Unnecessary**: Users debug specific sessions, not cross-session patterns
- **I/O overhead**: Writing every event twice
- **Working dir clutter**: Global log in project directory

If cross-session analysis is needed in the future, we can add project-level event aggregation as an optional feature.

---

## Configuration Options

### Default (Session-Only)

```yaml
hooks:
  - module: hooks-logging
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
```

### Future: Optional Project-Level Aggregation

```yaml
hooks:
  - module: hooks-logging
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
      # Optional: aggregate all project sessions
      project_log: ~/.amplifier/projects/{project}/events.jsonl
```

Not implemented initially—add only if users request it.

---

## Philosophy Alignment

| Principle                | Alignment                            |
| ------------------------ | ------------------------------------ |
| **Mechanism not Policy** | ✅ Hook module (edge), not kernel    |
| **Ruthless Simplicity**  | ✅ Session-only (no dual-mode)       |
| **Text-First**           | ✅ JSONL (human + tool readable)     |
| **Non-Interference**     | ✅ Log failures don't crash sessions |
| **Clear Boundaries**     | ✅ User data (`~/`) ≠ Working dir    |

---

## Success Metrics

- Sessions are completely self-contained ✅
- Debugging time reduced by single-location inspection ✅
- No log files dropped in project working directories ✅
- Event correlation is straightforward (same directory) ✅

---

## Related Decisions

- **ADR-0001**: Project-Scoped Sessions (provides the directory structure)
- **Kernel ADRs**: Unified JSONL schema, event taxonomy

---

## References

- KERNEL_PHILOSOPHY.md: Observability as mechanism (policy at edges)
- IMPLEMENTATION_PHILOSOPHY.md: Ruthless simplicity
- Amplifier Event Schema v1 (specs/events/)

---

## Review Triggers

This decision should be revisited if:

- Users request cross-session analysis features
- Performance issues with per-session event writing
- Alternative storage backends considered (database, streaming)

---

_This ADR documents per-session event logging that makes sessions self-contained and debugging straightforward._
