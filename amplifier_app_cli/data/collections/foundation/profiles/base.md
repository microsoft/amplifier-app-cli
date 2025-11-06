---
profile:
  name: base
  version: 1.1.0
  description: Base configuration with core functionality, tools, and hooks
  extends: foundation:profiles/foundation.md

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
      inject_git_context: true
      git_include_status: true
      git_include_commits: 5
      git_include_branch: true
      git_include_main_branch: true

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

@foundation:context/shared/common-agent-base.md

Project context:

- @AGENTS.md

Base configuration provides core development tools (filesystem, bash) and essential hooks (logging, redaction). Follow project conventions and coding standards.
