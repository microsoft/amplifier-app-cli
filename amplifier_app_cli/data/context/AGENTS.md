# Amplifier Project Context

This file contains project-specific context and instructions for AI assistants working with Amplifier. It's designed to be loaded via @mention from profile markdown bodies.

## Project Overview

Amplifier is a modular AI platform following kernel/module architecture inspired by Linux. It provides:

- Multi-provider LLM support (Anthropic, OpenAI, Azure, Ollama)
- Pluggable module system (providers, tools, orchestrators, context, hooks, agents)
- Profile-based configuration with inheritance
- Sub-session delegation via agent configurations
- Unified event system and JSONL logging

## Core Philosophy

Read these foundational documents:

- @ai_context/IMPLEMENTATION_PHILOSOPHY.md
- @ai_context/MODULAR_DESIGN_PHILOSOPHY.md
- @ai_context/KERNEL_PHILOSOPHY.md

Key principles:

**Ruthless Simplicity**:
- Keep implementations as simple as possible
- Minimize abstractions - every layer must justify existence
- Start minimal, grow as needed
- Avoid future-proofing

**Kernel Philosophy**:
- Mechanism, not policy (kernel provides mechanisms, modules decide policies)
- Small, stable, boring center
- Innovation at the edges (modules)
- Don't break userspace (backward compatibility sacred)

**Modular Design**:
- Bricks and studs (modules are self-contained, interfaces are stable)
- Regeneratable from spec
- Clear contracts

## Architecture

**Kernel** (`amplifier-core`):
- Session management and coordination
- Event system and hooks
- Module loading and mounting
- NO defaults, NO file I/O, NO policy decisions

**Modules** (amplifier-module-*):
- Providers: LLM integrations (Anthropic, OpenAI, etc.)
- Tools: Capabilities (filesystem, bash, web, search, task delegation)
- Orchestrators: Execution loops (basic, streaming, with planning)
- Context: Memory strategies (simple, persistent)
- Hooks: Observability, redaction, approval, streaming UI
- Agents: Specialized sub-session configurations

**App/CLI** (amplifier-app-cli):
- Profile system (loading, compilation, resolution)
- Module resolution and sourcing
- Configuration precedence (default < user < project < flags)
- JSONL logger initialization
- Context loading (profiles → system instructions)

## Development Guidelines

**Dependency Management**:
- Use `uv` for all Python dependencies
- Never manually edit `pyproject.toml` dependencies
- `cd` to module directory and run `uv add <package>`
- Path dependencies for amplifier-core in modules

**Build Commands**:
- `make install` - Install dependencies
- `make check` - Run all checks (lint, format, type)
- `make test` - Run tests
- `make lock-upgrade` - Upgrade dependency locks

**Code Style**:
- Python 3.11+ with type hints
- Line length: 120 characters
- Use Pydantic for data validation
- Follow existing patterns in codebase

**Testing**:
- Focus on integration and end-to-end tests
- Test pyramid: 60% unit, 30% integration, 10% e2e
- Test runtime invariants, not code inspection

**Event System**:
- Emit canonical events: `session:*`, `prompt:*`, `provider:*`, `tool:*`, etc.
- Use unified JSONL logging (no private log files)
- Redaction runs before logging

## Configuration Management

Single source of truth for all configuration:
- Every setting has exactly ONE authoritative location
- All other uses reference or derive from that source
- No duplicate configuration across files

## Documentation First

For major features, follow Document-Driven Development:
1. Planning & reconnaissance
2. Update ALL docs to target state (retcon writing)
3. Get approval
4. Implement code to match docs
5. Test as documented
6. Clean up and verify

Documentation IS the specification. Code implements what docs describe.

## Sub-Agent Delegation

When working on complex tasks:
- Delegate to specialized agents proactively
- Use zen-architect for planning and design
- Use modular-builder for implementation
- Use bug-hunter for debugging
- Launch agents in parallel when possible

## Common Patterns

**Efficient Batch Processing**:
- Use file crawling technique for processing many files
- Save progress after every item (not at intervals)
- Use fixed filenames that overwrite (not timestamps)

**Partial Failure Handling** (when appropriate):
- Continue on individual failures in batch jobs
- Save partial results (better than nothing)
- Track failure reasons for selective retry

**Zero-BS Principle**:
- Build working code, not stubs
- No `raise NotImplementedError` without implementation
- No `TODO` comments without accompanying code
- No placeholders

## Decision Tracking

Significant decisions documented in `ai_working/decisions/`. Check before proposing major changes to understand historical context.

## Resources

**For newcomers**: Start with `docs/README.md` and `docs/USER_ONBOARDING.md`

**For contributors**: See `docs/LOCAL_DEVELOPMENT.md` and `docs/MODULE_DEVELOPMENT.md`

**For architecture**: Read `docs/AMPLIFIER_AS_LINUX_KERNEL.md` and `docs/context/KERNEL_PHILOSOPHY.md`
