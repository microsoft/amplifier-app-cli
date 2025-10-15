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
    config:
      default_response: This is a mock response for testing
      response_delay: 0.1
      fail_probability: 0.0

tools:
  - module: tool-task
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