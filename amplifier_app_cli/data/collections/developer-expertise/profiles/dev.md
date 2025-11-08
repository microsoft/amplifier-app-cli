---
profile:
  name: dev
  version: 1.2.0
  description: Development configuration with full toolset
  extends: foundation:profiles/base.md

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      debug: true

ui:
  show_thinking_stream: true
  show_tool_lines: 5

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main

hooks:
  - module: hooks-status-context
    config:
      include_git: true
      git_include_status: true
      git_include_commits: 3
      git_include_branch: true
      git_include_main_branch: true
---

@foundation:context/shared/common-agent-base.md
