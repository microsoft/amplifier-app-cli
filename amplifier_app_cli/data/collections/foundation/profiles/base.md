---
profile:
  name: base
  version: 1.1.0
  description: Base configuration with core functionality, tools, and hooks
  extends: foundation:profiles/foundation.md

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
    config:
      extended_thinking: true
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 400000
      compact_threshold: 0.8
      auto_compact: true
  injection_budget_per_turn: null
  injection_size_limit: null

task:
  max_recursion_depth: 1

tools:
  - module: tool-web
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-task
    source: git+https://github.com/microsoft/amplifier-module-tool-task@main
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main

hooks:
  - module: hooks-status-context
    source: git+https://github.com/microsoft/amplifier-module-hooks-status-context@main
    config:
      include_datetime: true
      datetime_include_timezone: false
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
  - module: hooks-todo-reminder
    source: git+https://github.com/microsoft/amplifier-module-hooks-todo-reminder@main
    config:
      inject_role: user
      priority: 10
  - module: hooks-streaming-ui
    source: git+https://github.com/microsoft/amplifier-module-hooks-streaming-ui@main

agents:
  dirs:
    - ./agents
---

@foundation:context/shared/common-agent-base.md
