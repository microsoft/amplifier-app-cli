---
profile:
  name: dev
  version: 1.2.0
  description: Development configuration with full toolset
  extends: base

session:
  orchestrator: loop-streaming
  context: context-simple

orchestrator:
  config:
    extended_thinking: true

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

# Development Profile

A fully-featured development configuration with streaming UI, extended thinking capabilities, and specialized agents for common development tasks.

**When to use**: Primary profile for development work with full access to web, search, and task delegation capabilities.

**Extends**: base (inherits filesystem, bash, redaction, and logging)

**Key features**:
- Streaming orchestrator with extended thinking blocks enabled for Claude Sonnet 4.5
- Web browsing and search tools for research
- Task delegation tool with configurable recursion depth
- Streaming UI hook for real-time display of thinking and tool output
- Three specialized agents: zen-architect, bug-hunter, and researcher
- UI configuration to show thinking stream and up to 5 lines of tool output