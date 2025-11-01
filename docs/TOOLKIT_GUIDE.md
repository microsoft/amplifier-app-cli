# Amplifier-Dev Toolkit Guide

## Philosophy: Metacognitive Recipes with Multi-Config Patterns

The **amplifier-dev-toolkit** teaches you how to build sophisticated AI tools using **metacognitive recipes** - orchestrated thinking processes where code manages workflow and multiple specialized AI configs handle different cognitive subtasks.

**Core Principle**: Code for orchestration, specialized configs for cognition

- **Code handles**: Flow control, state management, decisions, loops, human interaction
- **Multiple configs handle**: Each cognitive subtask (analytical, creative, precision, evaluative)
- **Amplifier-core provides**: The mechanism to execute any config
- **Toolkit provides**: Structural utilities (file discovery, progress, validation)

### What Is a Metacognitive Recipe?

A **metacognitive recipe** is a structured thinking process encoded as code that orchestrates multiple LLM sessions, each optimized for a specific cognitive role.

**Example**: Tutorial improvement tool

```python
# Stage 1: Content analysis (analytical config, temp=0.3)
async with AmplifierSession(config=ANALYZER_CONFIG) as session:
    analysis = await session.execute(f"Analyze tutorial structure: {content}")

# Stage 2: Learner simulation (empathetic config, temp=0.5)
async with AmplifierSession(config=LEARNER_SIMULATOR_CONFIG) as session:
    learner_experience = await session.execute(f"Simulate learner: {content}")

# Stage 3: Issue diagnosis (precision config, temp=0.1)
async with AmplifierSession(config=DIAGNOSTICIAN_CONFIG) as session:
    diagnosis = await session.execute(f"Diagnose issues: {learner_experience}")

# Stage 4: Improvement generation (creative config, temp=0.7)
async with AmplifierSession(config=IMPROVER_CONFIG) as session:
    improvements = await session.execute(f"Generate improvements: {diagnosis}")

# Code makes decision based on results
if needs_human_review(improvements):
    feedback = get_human_feedback(improvements)
    # Iterate with feedback...
```

**Key insight**: Each stage uses a different config optimized for its cognitive role. Code decides which config to use when, manages state across stages, and determines flow based on results.

### What Belongs Where

| Layer                           | Responsibility                                    | Examples                             |
| ------------------------------- | ------------------------------------------------- | ------------------------------------ |
| **Kernel** (amplifier-core)     | LLM orchestration mechanism                       | `AmplifierSession.execute()`         |
| **Tools**                       | Metacognitive recipes, multi-config orchestration | `tutorial_analyzer`                  |
| **Toolkit** (amplifier-app-cli) | Structural utilities                              | `discover_files`, `ProgressReporter` |

**Critical**: Don't wrap AmplifierSession. Use it directly with different configs for different cognitive subtasks.

## The Multi-Config Pattern

Sophisticated tools DON'T have one config - they have multiple specialized configs, each optimized for a specific cognitive subtask.

### Pattern Overview

```python
from amplifier_core import AmplifierSession
from pathlib import Path

# ==== MULTIPLE SPECIALIZED CONFIGS ====
# Each config optimized for its cognitive role

ANALYZER_CONFIG = {
    "session": {
        "orchestrator": "loop-basic",  # Simple analytical task
        "context": "context-simple",
    },
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,  # Precise analysis
            "system_prompt": "You are an expert tutorial content analyzer."
        }
    }],
}

LEARNER_SIMULATOR_CONFIG = {
    "session": {
        "orchestrator": "loop-streaming",  # Interactive simulation
        "context": "context-simple",
    },
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-opus-4-1",
            "temperature": 0.5,  # Empathetic simulation
            "system_prompt": "You are a learner encountering this tutorial for the first time."
        }
    }],
}

DIAGNOSTICIAN_CONFIG = {
    "session": {
        "orchestrator": "loop-basic",
        "context": "context-simple",
    },
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.1,  # Very precise
            "system_prompt": "You are a pedagogy expert identifying tutorial issues."
        }
    }],
}

# ==== CODE ORCHESTRATES THE METACOGNITIVE RECIPE ====

async def evolve_tutorial(tutorial_path: Path) -> dict:
    """Multi-stage metacognitive recipe for tutorial evolution."""

    content = tutorial_path.read_text()

    # Stage 1: Analytical subtask
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        analysis = await session.execute(
            f"Analyze tutorial structure:\n\n{content}"
        )

    # Stage 2: Simulation subtask
    async with AmplifierSession(config=LEARNER_SIMULATOR_CONFIG) as session:
        learner_experience = await session.execute(
            f"Simulate learning from:\nAnalysis: {analysis}\nContent: {content}"
        )

    # Stage 3: Precision subtask
    async with AmplifierSession(config=DIAGNOSTICIAN_CONFIG) as session:
        diagnosis = await session.execute(
            f"Diagnose issues:\nLearner: {learner_experience}\nAnalysis: {analysis}"
        )

    # Code makes decision based on diagnosis
    if diagnosis["severity"] == "critical":
        # Jump to different flow...
        pass

    return {
        "analysis": analysis,
        "learner_experience": learner_experience,
        "diagnosis": diagnosis
    }
```

### Key Principles

1. **Multiple configs, not one** - Each cognitive subtask gets its own optimized config
2. **Code orchestrates thinking** - When to use which config, how to combine results
3. **Specialized for role** - Analytical (temp=0.3), Creative (temp=0.7), Precision (temp=0.1)
4. **AmplifierSession per subtask** - Create new session with appropriate config
5. **State management in code** - Code tracks state across stages, decides flow
6. **Human-in-loop where valuable** - Code determines when human input needed

## System Prompts: Simplified vs Production

**⚠️ IMPORTANT**: System prompts shown in this guide are simplified for pedagogical clarity.

**For production-quality prompts**, see evolving examples in:

- `amplifier-app-cli/data/agents/*.md` - Agent instruction templates
- `amplifier-app-cli/data/context/*.md` - Context management prompts

These are living documents that improve over time. Use them as inspiration for sophisticated prompt engineering in your tools.

**Example simplified vs production**:

```python
# Simplified (for teaching)
"system_prompt": "You are an expert tutorial analyzer."

# Production (from data/agents/analyzer.md)
"system_prompt": """You are an expert tutorial content analyzer...
[500+ lines of detailed instructions, examples, edge cases, output formats]
"""
```

Reference production prompts for real tools, use simplified for learning.

## What the Toolkit Provides

### 1. File Operations (`utilities/file_ops.py`)

Robust file handling with cloud sync awareness:

```python
from amplifier_app_cli.toolkit import discover_files, read_with_retry, write_with_retry

# Discover files recursively
files = discover_files(Path("docs"), "**/*.md")

# Read with cloud sync retry (handles OneDrive/Dropbox delays)
content = read_with_retry(Path("config.json"))

# Write with retry
write_with_retry(results, Path("output.json"))
```

**Why cloud sync retry?**
OneDrive, Dropbox, and Google Drive can cause transient I/O errors when files aren't locally cached. The toolkit automatically retries with exponential backoff.

### 2. Progress Reporting (`utilities/progress.py`)

Clear, consistent progress display:

```python
from amplifier_app_cli.toolkit import ProgressReporter

progress = ProgressReporter(len(items), "Processing documents")
for item in items:
    process(item)
    progress.update()
progress.finish()
```

### 3. Validation (`utilities/validation.py`)

Input validation with clear error messages:

```python
from amplifier_app_cli.toolkit import (
    validate_input_path,
    validate_output_path,
    require_minimum_files
)

# Validate paths
validate_input_path(Path("docs"), must_be_dir=True)
validate_output_path(Path("results.json"))

# Validate file counts
files = discover_files(base_path, pattern)
require_minimum_files(files, minimum=2, context="analysis requires multiple files")
```

## What the Toolkit Does NOT Provide

### ❌ Don't Wrap AmplifierSession

**WRONG**:

```python
class LLMHelper:
    def __init__(self):
        self.session = AmplifierSession(...)  # Don't do this!

    async def ask(self, prompt: str) -> str:
        return await self.session.execute(prompt)
```

**RIGHT**:

```python
# Use AmplifierSession directly with specialized configs
async with AmplifierSession(config=ANALYZER_CONFIG) as session:
    response = await session.execute(prompt)
```

**Why**: AmplifierSession IS the interface. Wrapping it violates kernel philosophy ("use mechanisms, don't wrap them").

### ❌ Don't Create Generic State Managers

**WRONG**:

```python
class ToolStateManager:
    """Don't create general state management!"""
    def __init__(self):
        self.state = {}
```

**RIGHT**:

```python
# Each tool owns its state
STATE_FILE = ".tutorial_analyzer_state.json"

def save_state(data: dict):
    Path(STATE_FILE).write_text(json.dumps(data, indent=2))

def load_state() -> dict:
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text())
    return {}
```

**Why**: State management is tool-specific policy, not toolkit mechanism.

## Complete Example: tutorial_analyzer

The toolkit includes `tutorial_analyzer` as the pedagogical exemplar showing the complete multi-config metacognitive recipe pattern.

### Structure

```
toolkit/examples/tutorial_analyzer/
  main.py                     # Main orchestration
  state.py                    # State management

  analyzer/core.py            # ANALYZER_CONFIG (analytical, temp=0.3)
  learner_simulator/core.py   # LEARNER_SIMULATOR_CONFIG (empathetic, temp=0.5)
  diagnostician/core.py       # DIAGNOSTICIAN_CONFIG (precise, temp=0.1)
  improver/core.py            # IMPROVER_CONFIG (creative, temp=0.7)
  critic/core.py              # CRITIC_CONFIG (evaluative, temp=0.2)
  synthesizer/core.py         # SYNTHESIZER_CONFIG (analytical, temp=0.3)

  pyproject.toml             # Package metadata for uvx
  README.md                  # User guide
```

### Pipeline

```
Analyze → Simulate Learner → Diagnose Issues →
→ Plan Improvements [HUMAN] → Apply Fixes →
→ Re-Simulate → Score Quality → Decide [HYBRID] →
→ Loop or Finalize
```

### Key Teachings

1. **Six specialized configs** - Each optimized for its cognitive role
2. **Code orchestrates flow** - When to use which config, loop control
3. **Human-in-loop** - Strategic decision points where human input valuable
4. **State management** - Checkpointing after each stage for resumability
5. **Evaluative loops** - Re-simulate and score to decide if done
6. **Complex flow control** - Nested loops, conditional jumps, context accumulation

See `toolkit/examples/tutorial_analyzer/README.md` for complete documentation.

## Common Patterns

### Pattern 1: Simple Multi-Stage Pipeline

```python
from amplifier_core import AmplifierSession
from amplifier_app_cli.toolkit import discover_files, ProgressReporter
from pathlib import Path

# Define specialized configs
EXTRACTOR_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {"model": "claude-sonnet-4-5", "temperature": 0.3}
    }],
}

SYNTHESIZER_CONFIG = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {"model": "claude-opus-4-1", "temperature": 0.7}
    }],
}

async def analyze_documents(doc_dir: Path):
    """Two-stage analysis: extract then synthesize."""

    # Stage 1: Extraction (analytical config)
    files = discover_files(doc_dir, "**/*.md")
    extractions = []

    async with AmplifierSession(config=EXTRACTOR_CONFIG) as session:
        progress = ProgressReporter(len(files), "Extracting")
        for file in files:
            extraction = await session.execute(f"Extract key concepts:\n\n{file.read_text()}")
            extractions.append(extraction)
            progress.update()
        progress.finish()

    # Stage 2: Synthesis (creative config)
    async with AmplifierSession(config=SYNTHESIZER_CONFIG) as session:
        synthesis = await session.execute(
            f"Synthesize these extractions into a coherent summary:\n\n{extractions}"
        )

    return synthesis
```

### Pattern 2: Evaluative Loop with Quality Thresholds

```python
GENERATOR_CONFIG = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {"model": "claude-opus-4-1", "temperature": 0.7}  # Creative
    }],
}

EVALUATOR_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {"model": "claude-sonnet-4-5", "temperature": 0.2}  # Evaluative
    }],
}

async def generate_with_quality_loop(prompt: str, quality_threshold: float = 0.8):
    """Generate → Evaluate → Improve loop until quality threshold met."""

    max_iterations = 3
    for iteration in range(max_iterations):
        # Generate with creative config
        async with AmplifierSession(config=GENERATOR_CONFIG) as session:
            draft = await session.execute(prompt)

        # Evaluate with precision config
        async with AmplifierSession(config=EVALUATOR_CONFIG) as session:
            evaluation = await session.execute(
                f"Score this output 0-1 and explain:\n\nPrompt: {prompt}\n\nOutput: {draft}"
            )

        score = float(evaluation["score"])
        if score >= quality_threshold:
            return draft  # Success

        # Improve prompt for next iteration
        prompt = f"{prompt}\n\nPrevious attempt scored {score}. Issues: {evaluation['issues']}"

    # Max iterations reached
    return draft  # Return best attempt
```

### Pattern 3: Parallel Analysis with Convergence

```python
import asyncio

CLARITY_CONFIG = {"providers": [{"config": {"temperature": 0.3}}], ...}
TECHNICAL_CONFIG = {"providers": [{"config": {"temperature": 0.2}}], ...}
PEDAGOGICAL_CONFIG = {"providers": [{"config": {"temperature": 0.4}}], ...}

async def multi_perspective_analysis(content: str):
    """Analyze from multiple perspectives in parallel, then converge."""

    # Fork into parallel analyses
    async with asyncio.TaskGroup() as tg:
        clarity_task = tg.create_task(analyze_with_config(content, CLARITY_CONFIG))
        technical_task = tg.create_task(analyze_with_config(content, TECHNICAL_CONFIG))
        pedagogical_task = tg.create_task(analyze_with_config(content, PEDAGOGICAL_CONFIG))

    # Converge results
    clarity_result = await clarity_task
    technical_result = await technical_task
    pedagogical_result = await pedagogical_task

    # Code makes routing decision based on which found issues
    if clarity_result.has_issues and pedagogical_result.has_issues:
        # Both failed - fundamental restructure needed
        return await restructure_fundamentally(content, clarity_result, pedagogical_result)
    elif technical_result.has_issues:
        # Only technical issues - targeted fix
        return await fix_technical(content, technical_result)
    else:
        # All passed - proceed to finalization
        return await finalize(content)
```

### Pattern 4: Human-in-Loop at Strategic Points

```python
PLANNER_CONFIG = {"providers": [{"config": {"temperature": 0.3}}], ...}
IMPLEMENTER_CONFIG = {"providers": [{"config": {"temperature": 0.7}}], ...}

async def implement_with_human_approval(task_description: str):
    """AI plans, human approves strategy, AI implements."""

    # Stage 1: AI plans (autonomous)
    async with AmplifierSession(config=PLANNER_CONFIG) as session:
        plan = await session.execute(
            f"Create implementation plan for: {task_description}"
        )

    # Stage 2: Human approval (critical decision point)
    print(f"Proposed Plan:\n{plan}\n")
    approval = input("Approve plan? (yes/no/modify): ")

    if approval == "no":
        return {"status": "rejected", "plan": plan}
    elif approval == "modify":
        modifications = input("What modifications? ")
        plan = f"{plan}\n\nModifications requested: {modifications}"

    # Stage 3: AI implements (autonomous with approved plan)
    async with AmplifierSession(config=IMPLEMENTER_CONFIG) as session:
        implementation = await session.execute(
            f"Implement this approved plan:\n\n{plan}"
        )

    return {"status": "implemented", "plan": plan, "implementation": implementation}
```

## Advanced Flow Control

Metacognitive recipes support arbitrarily complex flow control. Code can implement sophisticated thinking patterns including nested loops, conditional jumps, and context-carrying state transitions.

### Nested Evaluation Loops

```python
# Outer loop: Overall quality improvement
for overall_iteration in range(max_overall_iterations):

    # Inner loop: Section-by-section refinement
    for section in tutorial.sections:
        async with AmplifierSession(config=SECTION_ANALYZER) as session:
            issues = await session.execute(f"Analyze section: {section}")

        if has_critical_issues(issues):
            # Jump to specialized repair flow
            section = await repair_critical_section(section, issues)

        # Inner quality check
        score = await evaluate_section(section)
        if score < threshold:
            # Re-analyze with different config
            async with AmplifierSession(config=DEEP_ANALYZER) as session:
                deep_issues = await session.execute(f"Deep analyze: {section}")
            section = await apply_fixes(section, deep_issues)

    # Outer quality check after all sections refined
    overall_score = await evaluate_tutorial(tutorial)
    if overall_score > target:
        break  # Exit outer loop early
```

### Conditional Jump with Context (Goto-Style)

```python
# State tracks current stage and jump context
state = {
    "stage": "ANALYZE",
    "context": {},
    "return_to": None,  # For goto-style returns
}

while True:
    if state["stage"] == "ANALYZE":
        analysis = await analyze(tutorial)
        state["context"]["analysis"] = analysis

        # Conditional jump based on analysis
        if analysis["has_code_examples"]:
            state["stage"] = "VERIFY_CODE"  # Jump to code verification
            state["return_to"] = "DIAGNOSE"  # Remember where to return
        else:
            state["stage"] = "DIAGNOSE"  # Skip code verification

    elif state["stage"] == "VERIFY_CODE":
        code_issues = await verify_code_examples(
            tutorial,
            state["context"]["analysis"]  # Context from earlier stage
        )
        state["context"]["code_issues"] = code_issues
        state["stage"] = state["return_to"]  # Goto DIAGNOSE

    elif state["stage"] == "DIAGNOSE":
        diagnosis = await diagnose(
            tutorial,
            analysis=state["context"]["analysis"],
            code_issues=state["context"].get("code_issues")  # May or may not exist
        )

        # Conditional jump based on diagnosis severity
        if diagnosis["severity"] == "critical":
            state["stage"] = "EMERGENCY_REWRITE"
        else:
            state["stage"] = "IMPROVE"
```

See `toolkit/METACOGNITIVE_RECIPES.md` for complete advanced flow control patterns.

## Best Practices

### 1. Always Use Recursive Patterns

```python
# GOOD: Searches all subdirectories
files = discover_files(base_path, "**/*.md")

# BAD: Only searches top level
files = list(base_path.glob("*.md"))
```

### 2. Validate Early

```python
def analyze(input_path: Path):
    # Validate inputs BEFORE creating expensive sessions
    validate_input_path(input_path, must_be_dir=True)
    files = discover_files(input_path, "**/*.md")
    require_minimum_files(files, minimum=2, context="analysis requires comparison")

    # Now safe to create sessions
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        # ...process files
```

### 3. Save State After Every Stage

```python
async def multi_stage_with_resumability(input_path: Path):
    """Save after each stage for resumability."""

    state = load_state()

    # Stage 1: Analyze
    if "analysis" not in state:
        async with AmplifierSession(config=ANALYZER_CONFIG) as session:
            state["analysis"] = await session.execute(...)
        save_state(state)  # Checkpoint

    # Stage 2: Simulate
    if "simulation" not in state:
        async with AmplifierSession(config=SIMULATOR_CONFIG) as session:
            state["simulation"] = await session.execute(...)
        save_state(state)  # Checkpoint

    # Continue...
```

### 4. Choose Config Temperature for Cognitive Role

| Role             | Temperature | Use Case                                        |
| ---------------- | ----------- | ----------------------------------------------- |
| **Analytical**   | 0.1-0.3     | Structure extraction, classification, diagnosis |
| **Empathetic**   | 0.4-0.6     | User simulation, perspective-taking             |
| **Creative**     | 0.6-0.8     | Content generation, improvement suggestions     |
| **Evaluative**   | 0.1-0.3     | Quality assessment, scoring, critique           |
| **Synthesizing** | 0.3-0.5     | Combining information, summarization            |

### 5. Handle Errors Gracefully

```python
async def robust_multi_stage(input_path: Path):
    """Continue processing on stage failures."""

    results = {}
    errors = {}

    # Stage 1: Analyze
    try:
        async with AmplifierSession(config=ANALYZER_CONFIG) as session:
            results["analysis"] = await session.execute(...)
    except Exception as e:
        errors["analysis"] = str(e)
        # Continue to next stage - maybe it can work without analysis

    # Stage 2: Synthesize (even if analysis failed)
    try:
        async with AmplifierSession(config=SYNTHESIZER_CONFIG) as session:
            # Use analysis if available, otherwise work with raw input
            results["synthesis"] = await session.execute(...)
    except Exception as e:
        errors["synthesis"] = str(e)

    return {
        "status": "partial" if errors else "success",
        "results": results,
        "errors": errors
    }
```

## Philosophy Deep Dive

### Why Multi-Config?

**Different cognitive tasks need different cognitive setups.**

Just like humans, AI performs different kinds of thinking:

- **Analytical thinking** (temp=0.3): Breaking down structure, classifying, extracting patterns
- **Empathetic thinking** (temp=0.5): Simulating perspectives, understanding users
- **Creative thinking** (temp=0.7): Generating novel content, exploring possibilities
- **Evaluative thinking** (temp=0.2): Judging quality, scoring, critiquing

**One config can't optimize for all of these.** Multi-config pattern lets you optimize each cognitive subtask independently.

### Why Code Orchestration?

**Code is better than AI at:**

- Flow control (loops, conditionals, state machines)
- State management (checkpointing, resumability)
- Deterministic decisions (thresholds, routing)
- Human interaction (approval gates, feedback loops)

**AI is better than code at:**

- Understanding natural language
- Pattern recognition in unstructured data
- Generating natural language
- Reasoning about complex domains

**Metacognitive recipes** use each for what it does best: code orchestrates thinking, specialized AI configs do the thinking.

### Why Not One Big Prompt?

**Alternative**: One huge prompt with all instructions

**Problems**:

- Attention dilution (model tries to do everything at once)
- Temperature compromise (can't optimize for each subtask)
- Context overflow (all context must fit in one window)
- Brittle (one failure kills entire flow)
- Unresumable (can't checkpoint between stages)

**Multi-config approach**:

- Focused attention (each session has one job)
- Optimized temperature (per cognitive role)
- Unlimited context (each stage starts fresh)
- Resilient (failures isolated to one stage)
- Resumable (checkpoint after every stage)

### Mechanism vs Policy

The toolkit provides **mechanisms** (capabilities):

- **Mechanism**: "Here's how to discover files recursively"
- **NOT Policy**: "You must process files in alphabetical order"

- **Mechanism**: "Here's how to create sessions with different configs"
- **NOT Policy**: "You must use exactly 6 configs"

- **Mechanism**: "Here's how to track progress"
- **NOT Policy**: "You must report every 10 items"

**Tools decide policies for their specific needs.**

## Getting Started

### 1. Study the Exemplar

The `tutorial_analyzer` example shows the complete pattern:

- Six specialized configs (analyzer, learner_simulator, diagnostician, improver, critic, synthesizer)
- Multi-stage orchestration with code managing flow
- Human-in-loop at strategic decision points
- State management with checkpointing
- Evaluative loops with quality thresholds
- Complex flow control (nested loops, conditional jumps)

See `toolkit/examples/tutorial_analyzer/README.md` for complete documentation.

### 2. Review the Template

Study `toolkit/templates/standalone_tool.py` to see:

- How to structure multiple configs
- How code orchestrates between configs
- State management patterns
- Error handling across stages
- Toolkit utility usage

### 3. Understand Configuration Levels

See `toolkit/METACOGNITIVE_RECIPES.md` for the configuration sophistication spectrum:

- **Level 1: Fixed configs** (90% of tools) - Hardcoded, predictable
- **Level 2: Code-modified configs** (8% of tools) - Adaptive within bounds
- **Level 3: AI-generated configs** (2% of tools) - Exploratory, low-stakes

Start with Level 1 (fixed configs). Only advance to Level 2/3 when you need adaptability.

### 4. Create Your Own Tool

```bash
# Study the exemplar
cd toolkit/examples/tutorial_analyzer
cat README.md

# Copy the template
cp toolkit/templates/standalone_tool.py my_tool.py

# Customize:
# 1. Define your specialized configs
# 2. Write orchestration logic
# 3. Add state management
# 4. Test with real inputs

# Package for distribution
# See toolkit/PACKAGING_GUIDE.md
```

## Troubleshooting

### Issue: "Source URL required for module"

**Cause**: Config missing `source` field for modules.

**Solution**: Every module reference must include `source` git URL:

```python
"providers": [{
    "module": "provider-anthropic",
    "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",  # Required!
    "config": {...}
}]
```

### Issue: Configs feel too similar

**Cause**: Not differentiating configs enough for their roles.

**Solution**: Ask "What is the cognitive role of this stage?"

- Analytical? → temp=0.2-0.3, precise system prompt
- Creative? → temp=0.6-0.8, generative system prompt
- Evaluative? → temp=0.1-0.2, critical system prompt

### Issue: Flow getting too complex

**Cause**: Trying to handle too many cases in one tool.

**Solution**: Decompose into smaller tools or simplify flow.

- Can some stages be removed?
- Can complex routing be simplified?
- Can subtasks become separate tools?

See `toolkit/BEST_PRACTICES.md` for decomposition strategies.

## Summary

The amplifier-dev toolkit teaches you to build sophisticated AI tools using:

1. **Metacognitive recipes** - Code-orchestrated multi-stage thinking processes
2. **Multi-config pattern** - Specialized configs for each cognitive subtask
3. **Structural utilities** - File ops, progress, validation
4. **Philosophy alignment** - Mechanism not policy, ruthless simplicity

Build tools that:

- **Use AmplifierSession correctly** - Multiple configs, no wrappers
- **Follow multi-config pattern** - Optimize each cognitive subtask
- **Orchestrate with code** - Flow control, state, decisions
- **Stay composable** - Clear stages, resumable, testable

**Key insight**: Sophisticated AI tools are built from simple, specialized AI sessions orchestrated by straightforward code. Each piece is simple; the sophistication emerges from composition.

**Next steps**:

1. Study `toolkit/examples/tutorial_analyzer/` - Complete working exemplar
2. Read `toolkit/METACOGNITIVE_RECIPES.md` - Deep dive on patterns
3. Read `toolkit/HOW_TO_CREATE_YOUR_OWN.md` - Step-by-step creation guide
4. Read `toolkit/BEST_PRACTICES.md` - Strategic guidance

**Remember**: The toolkit teaches patterns, not prescriptions. Use these ideas to build tools that solve YOUR problems in YOUR way.
