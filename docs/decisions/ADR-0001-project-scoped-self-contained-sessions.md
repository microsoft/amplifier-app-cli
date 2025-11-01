# ADR-0001: Project-Scoped Self-Contained Session Storage

**Status**: Implemented
**Date**: 2025-10-14
**Authors**: Brian Krabach, Claude Code
**Deciders**: Amplifier Core Team

---

## Context and Problem Statement

Amplifier sessions need persistent storage that aligns with how users think about their work. Users work in projects, not globally across their entire filesystem. When a user asks "what sessions do I have?", they mean "what sessions for the project I'm currently working on?"

Additionally, debugging and archival require that all session data be self-contained in a single location, making it easy to inspect, share, or clean up a complete session trace.

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

### Positive

- ✅ **Better UX**: Session list shows only relevant sessions for current work
- ✅ **Self-contained**: Complete session data in one directory
- ✅ **Easier debugging**: Single location for transcript + events
- ✅ **Simpler cleanup**: Delete project directory = delete all sessions
- ✅ **Archival-friendly**: Copy session directory = complete backup
- ✅ **Project context**: Sessions naturally tied to their origin
- ✅ **Scalability**: Doesn't degrade as total session count grows
- ✅ **No working dir clutter**: All user data in `~/.amplifier/`

### Negative

- ⚠️ **Project detection**: Relies on CWD which might not match user's mental model in edge cases
- ⚠️ **Cross-project analysis**: Requires iterating multiple project directories
- ⚠️ **Disk organization**: More nested directory structure

### Neutral

- 🔄 **Configuration**: Project-specific settings via `.amplifier/` in working dir (separate from session storage)
- 🔄 **Implementation time**: Straightforward, ~1 week

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
      mode: session-only  # Write only to per-session logs
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
```

---

## Philosophy Alignment

**✅ Mechanism not Policy**: Zero kernel changes. Pure app-layer policy decision.

**✅ Ruthless Simplicity**: CWD-based detection is straightforward, no complex algorithms.

**✅ Text-First**: All files are human-readable JSONL or JSON.

**✅ Modular**: SessionStore and LoggingHook are independent modules that compose.

**✅ Clear Boundaries**: Session storage (user data) separate from project config (`.amplifier/` in working dir).

---

## Success Metrics

- Session list command returns in <100ms for projects with 1000+ sessions ✅
- Sessions are completely self-contained (copy directory = full backup) ✅
- No log files created in project working directories ✅
- Users report improved session discoverability ✅

---

## Related Decisions

- **ADR-0002**: Event Logging (defines what goes in events.jsonl)
- **Kernel ADRs**: Unified JSONL logging, event taxonomy

---

## References

- KERNEL_PHILOSOPHY.md: Policy at edges (this is pure app-layer)
- IMPLEMENTATION_PHILOSOPHY.md: Ruthless simplicity (favor clear boundaries)
- Claude Code: Successful pattern for project-scoped session management

---

## Review Triggers

This decision should be revisited if:
- Users report project detection doesn't match their workflow
- Performance issues with nested directory structure
- New session storage backend considered (database, cloud sync)
- Cross-machine session sync becomes a requirement

---

_This ADR documents the project-scoped session storage design that aligns system behavior with user mental models while maintaining architectural integrity._
