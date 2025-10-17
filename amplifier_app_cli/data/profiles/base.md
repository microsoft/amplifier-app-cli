---
profile:
  name: base
  version: 1.1.0
  description: Base configuration with core functionality, tools, and hooks
  extends: foundation

session:
  orchestrator:
    module: loop-basic
    source: git+https://github.com/microsoft/amplifier-module-loop-basic@main
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 100000
      compact_threshold: 0.8
      auto_compact: true

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main

hooks:
  - module: hooks-redaction
    source: git+https://github.com/microsoft/amplifier-module-hooks-redaction@main
    config:
      allowlist:
        - session_id
        - turn_id
        - span_id
        - parent_span_id
  - module: hooks-logging
    source: git+https://github.com/microsoft/amplifier-module-hooks-logging@main
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
---

# Base Profile

The standard starting point for most Amplifier configurations. Extends foundation with essential tools and hooks that provide sensible defaults for development and production use.

**When to use**: As the parent profile for most custom configurations. Provides the minimum viable feature set for practical use.

**Extends**: foundation

**Key features**:
- Essential filesystem and bash tools for basic operations
- Redaction hook for security
- Logging hook for observability
- Session management with 100K token limit and auto-compaction at 80% threshold
- Infrastructure IDs (session_id, turn_id, etc.) are allowlisted from redaction