---
profile:
  name: dev
  version: 1.2.0
  description: Development configuration with full toolset
  extends: base

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
    config:
      extended_thinking: true
  context:
    module: context-simple

providers:
  - module: provider-anthropic
    config:
      debug: true

task:
  max_recursion_depth: 1

ui:
  show_thinking_stream: true
  show_tool_lines: 5

tools:
  - module: tool-web
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-task
    source: git+https://github.com/microsoft/amplifier-module-tool-task@main

hooks:
  - module: hooks-streaming-ui
    source: git+https://github.com/microsoft/amplifier-module-hooks-streaming-ui@main

agents:
  dirs:
    - ./agents
---

{{parent_instruction}}

Additional context for development:
- @DISCOVERIES.md
- @ai_context/KERNEL_PHILOSOPHY.md
- @ai_context/IMPLEMENTATION_PHILOSOPHY.md

You have web, search, and task delegation tools. Use extended thinking for complex analysis. You can delegate to specialized agents (zen-architect, bug-hunter, researcher) for focused tasks.