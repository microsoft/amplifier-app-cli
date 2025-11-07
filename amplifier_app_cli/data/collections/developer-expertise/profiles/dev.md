---
profile:
  name: dev
  version: 1.2.0
  description: Development configuration with full toolset
  extends: foundation:profiles/base.md

session:
  orchestrator:
    module: loop-basic
    source: git+https://github.com/microsoft/amplifier-module-loop-basic@main
    config:
      extended_thinking: true
  context:
    module: context-simple

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
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
  - module: hooks-status-context
    source: git+https://github.com/microsoft/amplifier-module-hooks-status-context@main
    config:
      include_git: true
      git_include_status: true
      git_include_commits: 3
      git_include_branch: true
      git_include_main_branch: true
      include_datetime: true
      datetime_include_timezone: false
  - module: hooks-streaming-ui
    source: git+https://github.com/microsoft/amplifier-module-hooks-streaming-ui@main

agents:
  dirs:
    - ./agents
---

@foundation:context/shared/common-agent-base.md

Development context:

Development configuration with extended capabilities. You have web, search, and task delegation tools. Use extended thinking for complex analysis. Delegate to specialized agents (zen-architect, bug-hunter, researcher, modular-builder) for focused tasks. Follow the modular design philosophy and ruthless simplicity principles.
