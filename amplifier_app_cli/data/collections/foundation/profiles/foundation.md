---
profile:
  name: foundation
  version: 1.0.0
  description: Foundation configuration with only essential components

session:
  orchestrator:
    module: loop-basic
    source: git+https://github.com/microsoft/amplifier-module-loop-basic@main
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-5
---

You are an AI assistant powered by Amplifier.

Be helpful, accurate, and efficient in your responses.
