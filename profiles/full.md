---
profile:
  name: full
  version: 1.0.0
  description: Full configuration with all available tools, hooks, and agents
  extends: base

session:
  orchestrator: loop-streaming
  context: context-persistent
  max_tokens: 200000
  compact_threshold: 0.9
  auto_compact: true

orchestrator:
  config:
    extended_thinking: true

providers:
  - module: provider-openai
    config:
      default_model: gpt-5-mini
  - module: provider-azure-openai
    config:
      default_model: gpt-5-mini
  - module: provider-ollama
    config:
      default_model: llama3.2:3b

tools:
  - module: tool-web
  - module: tool-search
  - module: tool-task

agents:
  dirs:
    - ./agents

hooks:
  - module: hooks-approval
    config:
      patterns:
        - rm -rf
        - sudo
        - DELETE
        - DROP
      auto_approve: false
  - module: hooks-backup
    config:
      backup_dir: .amplifier/local/backups
      max_backups: 10
  - module: hooks-scheduler-cost-aware
    config:
      budget_limit: 10.0
      warn_threshold: 0.8
  - module: hooks-scheduler-heuristic
    config:
      max_concurrent: 5
      batch_size: 10
---

# Full Profile

Kitchen sink configuration with all available modules for comprehensive testing and maximum capabilities.

**When to use**: For testing all features, demonstrating full capabilities, or when you need every available tool and hook.

**Extends**: base (inherits filesystem, bash, redaction, and logging)

**Key features**:
- Multiple providers: OpenAI, Azure OpenAI, and Ollama (in addition to Anthropic from base)
- All available tools: web, search, and task delegation
- Maximum token capacity (200K) with persistent context
- Approval hook for dangerous operations (rm -rf, sudo, DELETE, DROP)
- Backup hook for automatic state preservation
- Cost-aware scheduler to manage API spending ($10 budget with 80% warning)
- Heuristic scheduler for performance optimization (5 concurrent operations, batch size 10)
- Loads all agents from ./agents directory for task delegation