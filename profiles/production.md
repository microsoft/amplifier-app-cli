---
profile:
  name: production
  version: 1.1.0
  description: Production configuration optimized for reliability
  extends: base

session:
  orchestrator: loop-streaming
  context: context-persistent
  max_tokens: 150000
  compact_threshold: 0.9
  auto_compact: true

orchestrator:
  config:
    extended_thinking: true

tools:
  - module: tool-web
---

# Production Profile

An optimized configuration for production use with enhanced reliability, persistence, and higher capacity limits.

**When to use**: For production deployments where persistence and reliability are critical.

**Extends**: base (inherits filesystem, bash, redaction, and logging)

**Key features**:
- Streaming orchestrator with extended thinking enabled
- Persistent context manager for maintaining state across sessions
- Increased token limit (150K) with higher compaction threshold (90%)
- Web tool for external data access
- Inherits security and observability hooks from base profile