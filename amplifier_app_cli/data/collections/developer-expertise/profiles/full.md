---
profile:
  name: full
  version: 1.0.0
  description: Full configuration with all available tools, hooks, and agents
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
      max_tokens: 200000
      compact_threshold: 0.9
      auto_compact: true

providers:
  - module: provider-openai
    source: git+https://github.com/microsoft/amplifier-module-provider-openai@main
    config:
      default_model: gpt-5-mini
  - module: provider-azure-openai
    source: git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main
    config:
      default_model: gpt-5-mini
  - module: provider-ollama
    source: git+https://github.com/microsoft/amplifier-module-provider-ollama@main
    config:
      default_model: llama3.2:3b

tools:
  - module: tool-web
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-task
    source: git+https://github.com/microsoft/amplifier-module-tool-task@main

agents:
  dirs:
    - ./agents

hooks:
  - module: hooks-approval
    source: git+https://github.com/microsoft/amplifier-module-hooks-approval@main
    config:
      patterns:
        - rm -rf
        - sudo
        - DELETE
        - DROP
      auto_approve: false
  - module: hooks-backup
    source: git+https://github.com/microsoft/amplifier-module-hooks-backup@main
    config:
      backup_dir: .amplifier/local/backups
      max_backups: 10
  - module: hooks-scheduler-cost-aware
    source: git+https://github.com/microsoft/amplifier-module-hooks-scheduler-cost-aware@main
    config:
      budget_limit: 10.0
      warn_threshold: 0.8
  - module: hooks-scheduler-heuristic
    source: git+https://github.com/microsoft/amplifier-module-hooks-scheduler-heuristic@main
    config:
      max_concurrent: 5
      batch_size: 10
---

# Core Instructions

@foundation:context/shared/common-profile-base.md

---

Full capability configuration with comprehensive context:

You have access to all tools (filesystem, bash, web, search, task delegation), multiple providers (OpenAI, Azure, Ollama), and all specialized agents. Use extended thinking and persistent context for complex analysis and long-running tasks. Dangerous operations require explicit approval. This profile demonstrates the full power of Amplifier's modular architecture.
