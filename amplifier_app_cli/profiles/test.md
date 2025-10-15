---
profile:
  name: test
  version: 1.0.0
  description: Test configuration with mock provider for testing scenarios
  extends: base

session:
  orchestrator: loop-basic
  context: context-simple
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

# Test Profile

A testing configuration with mock provider for automated testing and development scenarios without API calls.

**When to use**: For running tests, CI/CD pipelines, or development without consuming API tokens.

**Extends**: base (inherits filesystem, bash, redaction, and logging)

**Key features**:

- Mock provider with configurable responses and failure simulation
- Reduced token limits (50K) for faster test execution
- Lower compaction threshold (70%) for testing compaction logic
- Task tool enabled for testing sub-session delegation
- Configurable response delay and failure probability for testing error handling
