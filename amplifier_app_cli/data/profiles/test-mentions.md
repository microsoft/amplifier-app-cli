---
profile:
  name: test-mentions
  version: 1.0.0
  description: Test profile for @mention system
  extends: foundation

session:
  orchestrator:
    module: loop-basic
    source: local
  context:
    module: context-simple
    source: local

providers:
  - module: provider-anthropic
    source: local
    config:
      debug: true

hooks:
  - module: hooks-logging
    source: local
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
---

You are a test assistant for Amplifier's @mention system.

Context loaded from bundled files:
- @AGENTS.md
- @DISCOVERIES.md

When asked what context you have, list the files you can see.
