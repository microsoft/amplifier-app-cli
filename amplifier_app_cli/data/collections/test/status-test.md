---
profile:
  name: test-status
  extends: foundation:profiles/base.md

session:
  orchestrator:
    module: loop-basic
  context:
    module: context-simple

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main

hooks:
  - module: hooks-status-context
    config:
      include_git: true
      git_include_status: true
      git_include_commits: 3
      git_include_branch: true
      git_include_main_branch: true
      include_datetime: true
---

Test profile for status context hook.
