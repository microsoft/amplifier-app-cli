---
profile:
  name: test
  version: 1.0.0
  description: Test configuration with mock provider for testing scenarios
  extends: foundation:profiles/base.md

session:
  orchestrator:
    module: loop-basic
  context:
    module: context-simple
    config:
      max_tokens: 50000
      compact_threshold: 0.7
      auto_compact: true

providers:
  - module: provider-mock
    source: git+https://github.com/microsoft/amplifier-module-provider-mock@main
    config:
      default_response: This is a mock response for testing
      response_delay: 0.1
      fail_probability: 0.0

tools:
  - module: tool-task
    source: git+https://github.com/microsoft/amplifier-module-tool-task@main

# Example: Inline agent definition for testing
agents:
  inline:
    test-agent:
      name: test-agent
      description: Simple test agent for validation
      tools:
        - module: tool-filesystem
      system:
        instruction: "You are a test agent. Respond with 'Test successful' to any query."
---

# Core Instructions

@foundation:context/shared/common-profile-base.md

---

Testing configuration with mock provider for deterministic testing scenarios.

You are running with a mock provider for predictable, offline testing. Respond clearly and deterministically to test scenarios. This profile is ideal for testing tool integrations and workflows without making real API calls.
