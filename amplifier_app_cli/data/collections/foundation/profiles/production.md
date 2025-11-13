---
profile:
  name: production
  version: 1.1.0
  description: Production configuration optimized for reliability
  extends: foundation:profiles/base.md

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
    config:
      extended_thinking: true
  context:
    module: context-persistent
    source: git+https://github.com/microsoft/amplifier-module-context-persistent@main
    config:
      max_tokens: 150000
      compact_threshold: 0.9
      auto_compact: true

tools:
  - module: tool-web
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main

# Example: Selective agent loading for production
# Only researcher (no bug-hunter, modular-builder, zen-architect)
agents:
  dirs:
    - ./agents
  include:
    - researcher
---

# Core Instructions

@foundation:context/shared/common-profile-base.md

---

Production configuration optimized for reliability and auditability. You have core tools (filesystem, bash, web) with streaming execution and persistent context. Extended thinking is enabled for critical decisions. Only the researcher agent is available for focused analysis. Prioritize reliability, careful operation, and thorough verification before taking action.
