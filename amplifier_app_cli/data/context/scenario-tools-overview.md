# Scenario Tools Overview

**Quick reference for AI agents on scenario tools concept**

---

## What Are Scenario Tools?

**Scenario tools** are sophisticated CLI applications that orchestrate multiple specialized AI sessions for complex, multi-stage tasks.

**Key distinction**:

| Aspect | LLM Tools (function calling) | Scenario Tools (CLI apps) |
|--------|------------------------------|---------------------------|
| Invoked by | LLM during chat | Human directly |
| Complexity | Single function call | Multi-stage orchestration |
| Configs | One (current session) | Multiple specialized |
| Examples | `read_file()`, `search_web()` | `tutorial_analyzer`, `memory_optimizer` |

---

## Core Pattern: Metacognitive Recipes

**Metacognitive recipes** = thinking processes encoded as code:

```
Analyze (precise, temp=0.3) →
Simulate (empathetic, temp=0.5) →
Diagnose (critical, temp=0.1) →
[HUMAN APPROVAL] →
Improve (creative, temp=0.7) →
Evaluate (judgmental, temp=0.2) →
LOOP or FINISH
```

**Code orchestrates** which AI config to use when, manages state, determines flow.

---

## Why Multiple Configs?

**Different cognitive tasks need different setups**:
- **Analysis** needs precision (temp=0.3, Sonnet)
- **Creativity** needs exploration (temp=0.7, Opus)
- **Evaluation** needs judgment (temp=0.2, Sonnet)

One config compromises all tasks. Multi-config pattern optimizes each.

---

## Structure in Collections

Collections can include scenario tools:

```
my-collection/
  scenario-tools/
    my_analyzer/              # Tool directory
      main.py                 # CLI entry point + orchestration
      analyzer/core.py        # ANALYZER_CONFIG
      synthesizer/core.py     # SYNTHESIZER_CONFIG
      state.py                # State management
      pyproject.toml          # Package metadata
      README.md               # User guide
      HOW_TO_BUILD.md         # Builder guide
```

**Installation**: Automatic when collection installed
```bash
amplifier collection add git+https://github.com/user/my-collection
# Tool now available: my-analyzer --help
```

---

## Key Concepts

**AmplifierSession**: Kernel mechanism for executing with any config
```python
async with AmplifierSession(config=ANALYZER_CONFIG) as session:
    result = await session.execute(prompt)
```

**Config specialization**: Each stage has its own optimized config
```python
ANALYZER_CONFIG = {"temperature": 0.3, "model": "sonnet", ...}
IMPROVER_CONFIG = {"temperature": 0.7, "model": "opus", ...}
```

**State management**: Checkpoint after expensive operations
```python
save_state({"stage1": result})  # Resumable
```

**Flow control**: Code decides routing
```python
if diagnosis["severity"] == "critical":
    return await emergency_rewrite(content)
else:
    return await incremental_improve(content)
```

---

## Temperature Guidelines

| Cognitive Role | Temperature | Model |
|----------------|-------------|-------|
| Analytical | 0.1-0.3 | Sonnet |
| Empathetic | 0.4-0.6 | Opus |
| Creative | 0.6-0.8 | Opus |
| Evaluative | 0.1-0.3 | Sonnet |
| Precision | 0.0-0.1 | Sonnet |

---

## Toolkit Integration

Scenario tools use toolkit utilities:

```python
from amplifier_app_cli.toolkit import (
    discover_files,          # Recursive file discovery
    ProgressReporter,        # Progress display
    validate_input_path,     # Validation
    read_with_retry,         # Cloud-sync aware I/O
)

# Use in scenario tools
files = discover_files(input_dir, "**/*.md")
progress = ProgressReporter(len(files), "Processing")
```

---

## Example: Tutorial Analyzer

**Purpose**: Multi-stage tutorial improvement

**Configs**: 6 specialized configs
- Analyzer (analytical, temp=0.3)
- Learner Simulator (empathetic, temp=0.5)
- Diagnostician (precise, temp=0.1)
- Improver (creative, temp=0.7)
- Critic (evaluative, temp=0.2)
- Synthesizer (analytical, temp=0.3)

**Flow**: Analyze → Simulate → Diagnose → [HUMAN] → Improve → Critique → Loop or Finalize

**Location**: `amplifier-app-cli/toolkit/examples/tutorial_analyzer/`

---

## For AI Agents

**When referencing scenario tools**:
- They are **CLI applications**, not LLM tools
- Invoked by humans directly
- Use multiple AI sessions internally
- Can save state and resume
- Include HOW_TO_BUILD.md for understanding the recipe

**When suggesting scenario tools**:
- For complex multi-stage tasks
- When different cognitive roles needed
- When human approval gates valuable
- When resumability important

**Don't confuse with**:
- LLM tools (function calling during chat)
- Simple scripts (single-purpose utilities)
- Modules (provider, tool, hook, orchestrator types)

---

**See**: [SCENARIO_TOOLS_GUIDE.md](../../docs/SCENARIO_TOOLS_GUIDE.md) for comprehensive guide and building instructions.
