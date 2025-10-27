# Toolkit Templates

## Philosophy: Multi-Config Metacognitive Recipes

These templates demonstrate how to build sophisticated AI tools using **metacognitive recipes** - code-orchestrated multi-stage thinking processes where each stage uses a specialized AI config optimized for its cognitive role.

**Core Principle**: Don't use one config - use multiple specialized configs orchestrated by code.

## The Multi-Config Pattern

```python
from amplifier_core import AmplifierSession
from amplifier_app_cli.toolkit import discover_files, ProgressReporter

# Multiple specialized configs (not one!)
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4",
            "temperature": 0.3,  # Analytical precision
            "system_prompt": "You are an expert content analyzer."
        }
    }],
}

SYNTHESIZER_CONFIG = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-opus-4-1",
            "temperature": 0.7,  # Creative synthesis
            "system_prompt": "You are a creative content synthesizer."
        }
    }],
}

# Code orchestrates thinking across configs
async def my_tool(input_dir: Path):
    # Toolkit utilities for structure
    files = discover_files(input_dir, "**/*.md")
    progress = ProgressReporter(len(files), "Processing")

    # Stage 1: Analytical config
    extractions = []
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        for file in files:
            extraction = await session.execute(f"Extract key concepts: {file.read_text()}")
            extractions.append(extraction)
            progress.update()

    # Stage 2: Creative config
    async with AmplifierSession(config=SYNTHESIZER_CONFIG) as session:
        synthesis = await session.execute(f"Synthesize these concepts: {extractions}")

    return synthesis
```

## Using the Template

### 1. Copy and Customize

```bash
cp amplifier-app-cli/toolkit/templates/standalone_tool.py my_tool.py
```

### 2. Define Your Specialized Configs

Think about the cognitive roles needed for your tool:

**Analytical tasks** (temp=0.2-0.3):
```python
ANALYZER_CONFIG = {
    "providers": [{
        "config": {
            "model": "claude-sonnet-4",
            "temperature": 0.3,
            "system_prompt": "You are an expert analyzer."
        }
    }],
    ...
}
```

**Creative tasks** (temp=0.6-0.8):
```python
CREATOR_CONFIG = {
    "providers": [{
        "config": {
            "model": "claude-opus-4-1",
            "temperature": 0.7,
            "system_prompt": "You are a creative generator."
        }
    }],
    ...
}
```

**Evaluative tasks** (temp=0.1-0.2):
```python
EVALUATOR_CONFIG = {
    "providers": [{
        "config": {
            "model": "claude-sonnet-4",
            "temperature": 0.2,
            "system_prompt": "You are a quality evaluator."
        }
    }],
    ...
}
```

### 3. Write Orchestration Logic

Code decides:
- Which config to use when
- How to combine results across stages
- When to loop or iterate
- When to ask for human input

```python
async def orchestrate(input_data):
    # Stage 1: Analyze
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        analysis = await session.execute(...)

    # Stage 2: Create
    async with AmplifierSession(config=CREATOR_CONFIG) as session:
        creation = await session.execute(...)

    # Stage 3: Evaluate
    async with AmplifierSession(config=EVALUATOR_CONFIG) as session:
        evaluation = await session.execute(...)

    # Code makes decision
    if evaluation["score"] < threshold:
        # Iterate with feedback...
        pass

    return creation
```

### 4. Add State Management

For multi-stage tools, checkpoint after each stage:

```python
STATE_FILE = ".my_tool_state.json"

async def process_with_resumability():
    state = load_state()

    # Stage 1
    if "analysis" not in state:
        async with AmplifierSession(config=ANALYZER_CONFIG) as session:
            state["analysis"] = await session.execute(...)
        save_state(state)  # Checkpoint

    # Stage 2
    if "creation" not in state:
        async with AmplifierSession(config=CREATOR_CONFIG) as session:
            state["creation"] = await session.execute(...)
        save_state(state)  # Checkpoint

    return state
```

## What the Template Shows

### ✅ Correct Patterns

**Multi-config orchestration**:
- Multiple specialized configs (not one!)
- Each optimized for its cognitive role
- Code orchestrates between configs

**State management**:
- Tool-specific state structure
- Checkpoint after each stage
- Resumability on failure

**Error handling**:
- Graceful failures per stage
- Continue processing when possible
- Return partial results

**Toolkit utilities**:
- File discovery with `discover_files`
- Progress reporting with `ProgressReporter`
- Validation with `validate_input_path`, `require_minimum_files`

### ❌ Anti-Patterns to Avoid

**Don't use single config**:
```python
# WRONG: One config trying to do everything
ONE_CONFIG = {"temperature": 0.5}  # Compromise

async with AmplifierSession(config=ONE_CONFIG) as session:
    # Analyze (needs low temp)
    analysis = await session.execute("Analyze...")
    # Create (needs high temp)
    creation = await session.execute("Create...")
```

**Don't wrap AmplifierSession**:
```python
# WRONG: Wrapping kernel mechanism
class Helper:
    def __init__(self):
        self.session = AmplifierSession(...)  # Violation!
```

**Don't create state frameworks**:
```python
# WRONG: Generalizing state management
class StateManager:
    """Don't do this!"""
```

**Don't bypass amplifier-core**:
```python
# WRONG: Direct LLM calls
import anthropic
response = anthropic.Client().messages.create(...)  # Violation!
```

## Complete Example

See `toolkit/examples/tutorial_analyzer/` for a complete working exemplar:
- 6 specialized configs (analyzer, learner_simulator, diagnostician, improver, critic, synthesizer)
- Multi-stage orchestration with code managing flow
- Human-in-loop at strategic decision points
- State management with checkpointing
- Evaluative loops with quality thresholds
- Complex flow control (nested loops, conditional jumps)

## Quick Start Checklist

When creating a new tool:

- [ ] Identify cognitive stages (analyze, create, evaluate, etc.)
- [ ] Define specialized config for each stage
- [ ] Optimize temperature per role (analytical=0.3, creative=0.7, evaluative=0.2)
- [ ] Write orchestration code (which config when)
- [ ] Add state management (checkpoint after each stage)
- [ ] Add error handling (continue on non-critical failures)
- [ ] Use toolkit utilities (discover_files, ProgressReporter, validation)
- [ ] Test with real inputs
- [ ] Package for distribution (see toolkit/PACKAGING_GUIDE.md)

## Philosophy References

- **TOOLKIT_GUIDE.md** - Complete guide to multi-config pattern
- **METACOGNITIVE_RECIPES.md** - Deep dive on recipes and configuration spectrum
- **HOW_TO_CREATE_YOUR_OWN.md** - Step-by-step creation guide
- **BEST_PRACTICES.md** - Strategic guidance and decomposition patterns
- **PHILOSOPHY.md** - Why multi-config, mechanism vs policy

## Configuration Levels

Start with **Level 1 (Fixed Configs)** - hardcoded CONFIG constants (90% of tools).

Only advance to Level 2 (Code-Modified) or Level 3 (AI-Generated) when you need runtime adaptability.

See `toolkit/METACOGNITIVE_RECIPES.md` for complete configuration sophistication spectrum.

## Remember

**Sophisticated AI tools are built from simple, specialized AI sessions orchestrated by straightforward code.**

- Each config is simple (optimized for one role)
- Each stage is simple (one focused task)
- Sophistication emerges from composition

Start simple. Add complexity only when you need it.
