# ADR-0001: Project-Scoped Self-Contained Session Storage

**Status**: Implemented
**Date**: 2025-10-14
**Authors**: Brian Krabach
**Deciders**: Amplifier Core Team

---

## Context

Users work in **projects**, not globally. When asking "what sessions do I have?", they mean "sessions for THIS project". Debugging requires all session data self-contained in one location for easy inspection, sharing, and cleanup.

---

## Decision

Session data is stored in a project-scoped structure under `~/.amplifier/`:

```
~/.amplifier/
  └── projects/
      └── <project-slug>/          # Based on CWD
          └── sessions/
              └── <session-id>/
                  ├── transcript.jsonl    # Anthropic message format
                  ├── events.jsonl        # All events for this session
                  └── metadata.json       # Session metadata
```

**Project detection**: Uses CWD (current working directory) to generate a deterministic project slug.

**Self-contained**: All session data—transcript, events, metadata—lives together in one directory.

---

## Rationale

### Project Scoping

Users work in project contexts. When they run `amplifier sessions list`, they want to see sessions relevant to their current work, not every session they've ever created across all projects.

**Example**: A developer working in `/home/user/repos/myapp` only cares about `myapp` sessions. Sessions from `/home/user/repos/other-project` are just noise.

### CWD-Based Project Slug

Using the current working directory provides:

- **Deterministic**: Same CWD always produces same slug
- **Simple**: No git dependency, works everywhere
- **Clear**: Users understand "where I am" = "my project"

Slug format: Replace path separators with hyphens

- `/home/user/repos/myapp` → `-home-user-repos-myapp`
- `/tmp` → `-tmp`
- `C:\projects\web-app` → `-C-projects-web-app` (Windows)

### Self-Contained Sessions

Every debugging session benefits from having all data in one place:

- **Debugging**: No need to correlate transcript with separate log files
- **Archival**: Copy session directory = complete backup
- **Sharing**: Tar up session for bug reports
- **Cleanup**: Delete session directory = all data gone

### Per-Session Event Logs

Events are written to `sessions/<id>/events.jsonl` instead of a global log file in the working directory. This:

- **Avoids clutter**: No log files dropped in project working directories
- **Enables correlation**: Events and transcript naturally paired
- **Simplifies debugging**: One location to inspect

---

## Implementation Details

### Directory Structure

```
~/.amplifier/
  └── projects/
      └── -home-user-repos-myapp/
          └── sessions/
              ├── abc-123-def-456/
              │   ├── transcript.jsonl
              │   ├── events.jsonl
              │   └── metadata.json
              └── xyz-789-ghi-012/
                  ├── transcript.jsonl
                  ├── events.jsonl
                  └── metadata.json
```

### Project Slug Generation

```python
def get_project_slug() -> str:
    """Generate project slug from current working directory."""
    cwd = Path.cwd().resolve()
    slug = str(cwd).replace("/", "-").replace("\\", "-").replace(":", "")
    if not slug.startswith("-"):
        slug = "-" + slug
    return slug
```

### Session Store Initialization

```python
class SessionStore:
    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            project_slug = get_project_slug()
            base_dir = Path.home() / ".amplifier" / "projects" / project_slug / "sessions"
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
```

### Logging Configuration

```yaml
hooks:
  - module: hooks-logging
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
```

All events for a session are written to its `events.jsonl` file. No global log in the working directory.

### CLI Commands

```bash
# List sessions for current project
$ cd ~/repos/myapp
$ amplifier sessions list
→ Shows sessions from ~/.amplifier/projects/-home-user-repos-myapp/sessions/

# List all sessions across all projects
$ amplifier sessions list --all-projects

# List sessions for specific project
$ amplifier sessions list --project /path/to/other/project
```

---

## Consequences

### Consequences

**✅ Positive**:

- Session list shows only relevant sessions
- Self-contained (one directory = complete session)
- Easy debugging (single location for all data)
- Simple cleanup (delete directory = remove session)
- Archival-friendly (copy directory = full backup)
- Scales with session count (project-scoped lists)
- No clutter in working directories

**⚠️ Negative**:

- CWD detection may not match mental model in edge cases
- Cross-project analysis requires multiple directory iteration

**Note**: Project settings (`.amplifier/` in CWD) separate from session storage (`~/.amplifier/projects/`).

---

## Configuration Options

### Session Storage

App layer resolves project slug and initializes SessionStore automatically:

```python
# In app initialization
from amplifier_app_cli.session_store import SessionStore

session_store = SessionStore()  # Auto-detects project from CWD
```

### Logging Hook

```yaml
# In profile
hooks:
  - module: hooks-logging
    config:
      mode: session-only # Write only to per-session logs
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
```

---

## Philosophy Alignment

| Principle                | Alignment                                            |
| ------------------------ | ---------------------------------------------------- |
| **Mechanism not Policy** | ✅ Zero kernel changes (pure app-layer)              |
| **Ruthless Simplicity**  | ✅ CWD-based, no complex algorithms                  |
| **Text-First**           | ✅ All files JSONL/JSON (human-readable)             |
| **Modular**              | ✅ SessionStore + LoggingHook compose independently  |
| **Clear Boundaries**     | ✅ User data (`~/`) ≠ Project config (`.amplifier/`) |

---

## Success Metrics

- Session list command returns in <100ms for projects with 1000+ sessions ✅
- Sessions are completely self-contained (copy directory = full backup) ✅
- No log files created in project working directories ✅
- Users report improved session discoverability ✅

---

## Related Decisions

- **Kernel ADRs**: Unified JSONL logging, event taxonomy

---

## References

- KERNEL_PHILOSOPHY.md: Policy at edges (this is pure app-layer)
- IMPLEMENTATION_PHILOSOPHY.md: Ruthless simplicity (favor clear boundaries)

---

## Review Triggers

This decision should be revisited if:

- Users report project detection doesn't match their workflow
- Performance issues with nested directory structure
- New session storage backend considered (database, cloud sync)
- Cross-machine session sync becomes a requirement

---

_This ADR documents the project-scoped session storage design that aligns system behavior with user mental models while maintaining architectural integrity._
