---
profile:
  name: base
  version: 1.1.0
  description: Base configuration with core functionality, tools, and hooks
  extends: foundation

session:
  orchestrator: loop-basic
  context: context-simple
  max_tokens: 100000
  compact_threshold: 0.8
  auto_compact: true

tools:
  - module: tool-filesystem
  - module: tool-bash

hooks:
  - module: hooks-redaction
    priority: 10
    enabled: true
    config:
      allowlist:
        - session_id
        - turn_id
        - span_id
        - parent_span_id
  - module: hooks-logging
    priority: 100
    enabled: true
    config:
      path: ./amplifier.log.jsonl
---

# Base Profile

The standard starting point for most Amplifier configurations. Extends foundation with essential tools and hooks that provide sensible defaults for development and production use.

**When to use**: As the parent profile for most custom configurations. Provides the minimum viable feature set for practical use.

**Extends**: foundation

**Key features**:
- Essential filesystem and bash tools for basic operations
- Redaction hook for security (runs early at priority 10)
- Logging hook for observability (runs late at priority 100)
- Session management with 100K token limit and auto-compaction at 80% threshold
- Infrastructure IDs (session_id, turn_id, etc.) are allowlisted from redaction