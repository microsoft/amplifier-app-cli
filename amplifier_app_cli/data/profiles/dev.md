---
profile:
  name: dev
  version: 1.2.0
  description: Development configuration with full toolset
  extends: base

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/robotdad/amplifier-module-loop-streaming@fix/preserve-newlines-in-tokenize-stream
    config:
      extended_thinking: true
      max_iterations: 50
  context:
    module: context-simple

task:
  max_recursion_depth: 1

ui:
  show_thinking_stream: true
  show_tool_lines: 5
  tool_output_lines: 3
  max_arg_length: 100
  show_elapsed_time: true
  use_tree_formatting: true
  render_markdown: true

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
- Enhanced UI with markdown rendering, live progress feedback, and tree-style formatting
- Configurable tool output truncation (first 3 lines by default)
- Live elapsed time display during LLM operations