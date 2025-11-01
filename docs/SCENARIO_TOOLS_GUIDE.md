---
last_updated: 2025-10-26
status: stable
audience: users, tool-developers
---

# Scenario Tools Guide

## What Are Scenario Tools?

**Scenario tools** are sophisticated CLI applications that orchestrate multiple AI sessions for complex, multi-stage tasks. They differ fundamentally from simple LLM tools (function calling):

| Aspect                | LLM Tools            | Scenario Tools                    |
| --------------------- | -------------------- | --------------------------------- |
| **Invoked by**        | LLM during chat      | Human directly (CLI)              |
| **Complexity**        | Single function call | Multi-stage orchestrated workflow |
| **AI configs**        | Uses current session | Multiple specialized configs      |
| **State**             | Stateless            | Can maintain state across stages  |
| **Human interaction** | Via LLM              | Direct (approval gates, feedback) |
| **Resumability**      | No                   | Yes (checkpoints)                 |

**Example**: Tutorial analyzer is a scenario tool. It orchestrates 6 specialized AI configs through a multi-stage pipeline with human approval gates.

---

## Why Scenario Tools?

### Problem: Single-Config Limitations

A single AI session (one config) can't optimize for different cognitive tasks:

- **Analysis** needs precision (temp=0.3)
- **Creativity** needs exploration (temp=0.7)
- **Evaluation** needs judgment (temp=0.2)

One config compromises all tasks.

### Solution: Metacognitive Recipes

**Metacognitive recipes** are thinking processes encoded as code that orchestrate multiple specialized AI sessions:

```
Analyze (precise) → Simulate (empathetic) → Diagnose (critical) →
→ Plan (strategic) [HUMAN APPROVAL] → Implement (creative) →
→ Evaluate (judgmental) → LOOP or FINISH
```

Each arrow is a specialized AI config optimized for its cognitive role. Code decides which config to use when, manages state across stages, and determines flow based on results.

---

## Scenario Tools in Collections

Collections can package scenario tools in the `scenario-tools/` directory:

```
my-collection/
  scenario-tools/
    my_analyzer/
      main.py                 # CLI entry point
      pyproject.toml          # Package metadata

      analyzer/core.py        # Stage 1: ANALYZER_CONFIG
      synthesizer/core.py     # Stage 2: SYNTHESIZER_CONFIG

      README.md               # User guide
      HOW_TO_BUILD.md         # Builder guide
```

**Installation**: When you install a collection, scenario tools are automatically installed via `uv tool install`:

```bash
# Install collection
amplifier collection add git+https://github.com/user/my-collection

# Tools available immediately
my-analyzer --help

# Or use via uvx
uvx --from my-collection my-analyzer [args]
```

---

## Anatomy of a Scenario Tool

### Directory Structure

```
scenario-tools/tutorial_analyzer/
  # Entry point
  main.py                     # CLI interface, orchestration logic

  # Specialized cognitive modules
  analyzer/core.py            # ANALYZER_CONFIG (analytical, temp=0.3)
  learner_simulator/core.py   # LEARNER_SIMULATOR_CONFIG (empathetic, temp=0.5)
  diagnostician/core.py       # DIAGNOSTICIAN_CONFIG (precise, temp=0.1)
  improver/core.py            # IMPROVER_CONFIG (creative, temp=0.7)
  critic/core.py              # CRITIC_CONFIG (evaluative, temp=0.2)
  synthesizer/core.py         # SYNTHESIZER_CONFIG (analytical, temp=0.3)

  # State management
  state.py                    # Checkpoint/resume logic

  # Packaging
  pyproject.toml              # Package metadata for uv
  README.md                   # User documentation
  HOW_TO_BUILD.md             # Builder documentation
```

### Key Components

**1. Multiple Specialized Configs**

Each cognitive stage has its own config:

```python
# analyzer/core.py
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic", "context": "context-simple"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,  # Precise analysis
            "system_prompt": "You are an expert content analyzer..."
        }
    }],
}

# improver/core.py
IMPROVER_CONFIG = {
    "session": {"orchestrator": "loop-streaming", "context": "context-simple"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-opus-4-1",
            "temperature": 0.7,  # Creative generation
            "system_prompt": "You are a creative improvement generator..."
        }
    }],
}
```

**2. Code Orchestration**

Main logic orchestrates which config to use when:

```python
# main.py
async def evolve_tutorial(tutorial_path: Path) -> dict:
    """Multi-stage metacognitive recipe."""

    content = tutorial_path.read_text()

    # Stage 1: Analytical subtask
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        analysis = await session.execute(f"Analyze: {content}")

    # Stage 2: Empathetic subtask
    async with AmplifierSession(config=LEARNER_SIMULATOR_CONFIG) as session:
        learner_exp = await session.execute(f"Simulate learner: {content}")

    # Stage 3: Critical subtask
    async with AmplifierSession(config=DIAGNOSTICIAN_CONFIG) as session:
        diagnosis = await session.execute(f"Diagnose: {learner_exp}")

    # Code makes routing decision
    if diagnosis["severity"] == "critical":
        # Jump to different flow
        return await handle_critical_issues(content, diagnosis)

    # Stage 4: Creative subtask (with human approval)
    print(f"Diagnosis: {diagnosis}")
    if not get_human_approval():
        return {"status": "rejected"}

    async with AmplifierSession(config=IMPROVER_CONFIG) as session:
        improvements = await session.execute(f"Improve: {diagnosis}")

    return improvements
```

**3. State Management**

Scenario tools manage state across stages:

```python
# state.py
STATE_FILE = ".tutorial_analyzer_state.json"

def save_state(data: dict):
    """Save state after each stage for resumability."""
    Path(STATE_FILE).write_text(json.dumps(data, indent=2))

def load_state() -> dict:
    """Load state to resume from checkpoint."""
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text())
    return {}

# Usage in main.py
async def resume_or_start(tutorial_path: Path):
    """Resume from checkpoint or start fresh."""
    state = load_state()

    # Stage 1: Analyze (skip if already done)
    if "analysis" not in state:
        async with AmplifierSession(config=ANALYZER_CONFIG) as session:
            state["analysis"] = await session.execute(...)
        save_state(state)  # Checkpoint

    # Stage 2: Simulate (skip if already done)
    if "simulation" not in state:
        async with AmplifierSession(config=SIMULATOR_CONFIG) as session:
            state["simulation"] = await session.execute(...)
        save_state(state)  # Checkpoint

    # Continue from where we left off...
```

---

## Building Your Own Scenario Tool

### Step 1: Design the Metacognitive Recipe

**Identify cognitive stages**:

- What different kinds of thinking does this task need?
- Which stages need precision? Creativity? Judgment?
- Where does human input add value?

**Example - Document analyzer**:

1. **Analyze structure** (precise, temp=0.3)
2. **Extract insights** (analytical, temp=0.4)
3. **Generate summary** (creative, temp=0.6)
4. **Score quality** (evaluative, temp=0.2)
5. **Refine if needed** (loop)

### Step 2: Create Config Modules

One module per cognitive stage:

```
my_analyzer/
  analyzer/core.py          # Stage 1 config
  extractor/core.py         # Stage 2 config
  synthesizer/core.py       # Stage 3 config
  evaluator/core.py         # Stage 4 config
```

Each module exports a `CONFIG` constant:

```python
# analyzer/core.py
from amplifier_core import AmplifierSession

ANALYZER_CONFIG = {
    "session": {
        "orchestrator": "loop-basic",
        "context": "context-simple"
    },
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,
            "system_prompt": """You are an expert document structure analyzer.

Your task is to analyze document structure and organization.

Output JSON with:
- sections: list of sections found
- clarity_score: 0-1
- issues: list of structural issues
"""
        }
    }],
}
```

### Step 3: Implement Orchestration

```python
# main.py
import asyncio
import click
from pathlib import Path
from amplifier_core import AmplifierSession

from analyzer.core import ANALYZER_CONFIG
from synthesizer.core import SYNTHESIZER_CONFIG
from state import save_state, load_state


@click.command()
@click.argument("input_dir", type=click.Path(exists=True))
@click.option("--resume", is_flag=True, help="Resume from checkpoint")
def main(input_dir: str, resume: bool):
    """Analyze documents with multi-stage AI pipeline."""

    input_path = Path(input_dir)

    if resume:
        state = load_state()
    else:
        state = {"stage": "ANALYZE", "results": {}}

    asyncio.run(run_pipeline(input_path, state))


async def run_pipeline(input_path: Path, state: dict):
    """Execute multi-stage pipeline."""

    # Stage 1: Analyze (if not already done)
    if "analysis" not in state["results"]:
        click.echo("Stage 1: Analyzing structure...")

        async with AmplifierSession(config=ANALYZER_CONFIG) as session:
            analysis = await session.execute(f"Analyze: {input_path}")

        state["results"]["analysis"] = analysis
        save_state(state)  # Checkpoint

    # Stage 2: Synthesize
    if "synthesis" not in state["results"]:
        click.echo("Stage 2: Synthesizing insights...")

        async with AmplifierSession(config=SYNTHESIZER_CONFIG) as session:
            synthesis = await session.execute(
                f"Synthesize: {state['results']['analysis']}"
            )

        state["results"]["synthesis"] = synthesis
        save_state(state)  # Checkpoint

    # Output results
    click.echo(f"\nResults: {state['results']['synthesis']}")


if __name__ == "__main__":
    main()
```

### Step 4: Add Package Metadata

```toml
# pyproject.toml
[project]
name = "my-analyzer"
version = "1.0.0"
description = "Multi-stage document analyzer"

dependencies = [
    "click>=8.1.0",
]

[project.scripts]
my-analyzer = "my_analyzer.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Step 5: Document for Users and Builders

**README.md** (user-facing):

```markdown
# My Analyzer

Multi-stage document analyzer using specialized AI configs.

## Installation

uv tool install my-analyzer

# Or: uvx --from my-analyzer my-analyzer [args]

## Usage

# Analyze directory

my-analyzer docs/

# Resume from checkpoint

my-analyzer docs/ --resume

## What It Does

1. Analyzes structure (precise AI)
2. Extracts insights (analytical AI)
3. Synthesizes findings (creative AI)
4. Outputs results
```

**HOW_TO_BUILD.md** (contributor-facing):

```markdown
# How to Build My Analyzer

## Metacognitive Recipe

Stage 1: ANALYZER (temp=0.3, analytical)
↓
Stage 2: SYNTHESIZER (temp=0.6, creative)
↓
Output

## Configs

- analyzer/core.py: Structure analysis
- synthesizer/core.py: Insight synthesis

## Flow Control

Coded in main.py:

- Sequential execution
- Checkpointing after each stage
- Resume capability via --resume flag

## Testing

pytest tests/
uv run my-analyzer test_data/
```

### Step 6: Test and Iterate

```bash
# Local testing
cd scenario-tools/my_analyzer
uv run python main.py test_input/

# Install locally
uv tool install .

# Test installed version
my-analyzer test_input/

# Uninstall
uv tool uninstall my-analyzer
```

---

## Multi-Config Patterns

### Pattern 1: Simple Pipeline

**Use case**: Sequential stages, each needs different cognitive role

```python
async def simple_pipeline(input: str):
    # Stage 1: Extract (precise)
    async with AmplifierSession(config=EXTRACTOR_CONFIG) as session:
        extracted = await session.execute(f"Extract: {input}")

    # Stage 2: Synthesize (creative)
    async with AmplifierSession(config=SYNTHESIZER_CONFIG) as session:
        synthesis = await session.execute(f"Synthesize: {extracted}")

    return synthesis
```

### Pattern 2: Quality Loop

**Use case**: Generate → evaluate → improve until threshold met

```python
async def quality_loop(prompt: str, threshold: float = 0.8):
    max_iterations = 3

    for iteration in range(max_iterations):
        # Generate (creative config)
        async with AmplifierSession(config=GENERATOR_CONFIG) as session:
            draft = await session.execute(prompt)

        # Evaluate (critical config)
        async with AmplifierSession(config=EVALUATOR_CONFIG) as session:
            score = await session.execute(f"Score 0-1: {draft}")

        if score >= threshold:
            return draft  # Good enough

        # Improve prompt for next iteration
        prompt = f"{prompt}\n\nPrevious scored {score}. Improve it."

    return draft  # Return best attempt
```

### Pattern 3: Parallel Analysis with Convergence

**Use case**: Analyze from multiple perspectives, then decide

```python
async def multi_perspective(content: str):
    # Fork into parallel analyses
    async with asyncio.TaskGroup() as tg:
        clarity = tg.create_task(
            analyze_with(content, CLARITY_CONFIG)
        )
        technical = tg.create_task(
            analyze_with(content, TECHNICAL_CONFIG)
        )
        pedagogical = tg.create_task(
            analyze_with(content, PEDAGOGICAL_CONFIG)
        )

    # Converge: code decides based on results
    if await clarity.has_issues and await pedagogical.has_issues:
        return await restructure_fundamentally(content)
    elif await technical.has_issues:
        return await fix_technical(content)
    else:
        return await finalize(content)
```

### Pattern 4: Human-in-Loop at Strategic Points

**Use case**: AI does analysis, human makes decisions, AI implements

```python
async def human_approved_workflow(task: str):
    # Stage 1: AI analyzes and plans
    async with AmplifierSession(config=PLANNER_CONFIG) as session:
        plan = await session.execute(f"Plan: {task}")

    # Stage 2: Human approval (strategic decision point)
    print(f"Proposed Plan:\n{plan}\n")
    approval = input("Approve? (yes/no/modify): ")

    if approval == "no":
        return {"status": "rejected"}
    elif approval == "modify":
        mods = input("What modifications? ")
        plan = f"{plan}\n\nModifications: {mods}"

    # Stage 3: AI implements approved plan
    async with AmplifierSession(config=IMPLEMENTER_CONFIG) as session:
        result = await session.execute(f"Implement: {plan}")

    return result
```

---

## Config Temperature Guide

Choose temperature based on cognitive role:

| Role             | Temperature | Model Suggestion | Use Cases                                       |
| ---------------- | ----------- | ---------------- | ----------------------------------------------- |
| **Analytical**   | 0.1-0.3     | Sonnet           | Structure extraction, classification, diagnosis |
| **Empathetic**   | 0.4-0.6     | Opus             | User simulation, perspective-taking             |
| **Creative**     | 0.6-0.8     | Opus             | Content generation, ideation                    |
| **Evaluative**   | 0.1-0.3     | Sonnet           | Quality scoring, critique                       |
| **Synthesizing** | 0.3-0.5     | Sonnet/Opus      | Combining information, summarization            |
| **Precision**    | 0.0-0.1     | Sonnet           | Code generation, factual extraction             |

**Example config selection**:

```python
# Extracting facts from documents → Precision
EXTRACTOR_CONFIG = {"providers": [{"config": {"temperature": 0.1, "model": "claude-sonnet-4-5"}}]}

# Generating creative improvements → Creative
IMPROVER_CONFIG = {"providers": [{"config": {"temperature": 0.7, "model": "claude-opus-4-1"}}]}

# Scoring quality → Evaluative
CRITIC_CONFIG = {"providers": [{"config": {"temperature": 0.2, "model": "claude-sonnet-4-5"}}]}
```

---

## State Management

### Checkpointing Pattern

Save state after every expensive operation:

```python
async def multi_stage_with_checkpoints(input_path: Path):
    """Process with checkpoints for resumability."""

    state = load_state()

    # Stage 1
    if "stage1" not in state:
        result = await process_stage1(input_path)
        state["stage1"] = result
        save_state(state)  # Checkpoint

    # Stage 2
    if "stage2" not in state:
        result = await process_stage2(state["stage1"])
        state["stage2"] = result
        save_state(state)  # Checkpoint

    # Continue...
    return state
```

**Benefits**:

- Resume after interruption
- Skip completed stages
- Debug specific stages
- Preserve expensive work

### Progress Tracking

Show progress to users:

```python
from amplifier_app_cli.toolkit import ProgressReporter

async def process_many_items(items: list):
    progress = ProgressReporter(len(items), "Processing")

    for item in items:
        result = await process_item(item)
        progress.update()

    progress.finish()
```

---

## Error Handling

### Graceful Degradation

Continue processing on failures:

```python
async def robust_multi_stage(items: list):
    """Continue on individual failures."""

    results = {}
    errors = {}

    for item in items:
        try:
            async with AmplifierSession(config=PROCESSOR_CONFIG) as session:
                results[item.name] = await session.execute(...)
        except Exception as e:
            errors[item.name] = str(e)
            # Continue to next item

    return {
        "status": "partial" if errors else "success",
        "results": results,
        "errors": errors,
        "summary": f"Processed {len(results)}/{len(items)} items successfully"
    }
```

### Clear Error Messages

```python
async def validate_and_process(input_path: Path):
    """Validate early, fail fast with clear messages."""

    from amplifier_app_cli.toolkit import (
        validate_input_path,
        require_minimum_files
    )

    # Validate before expensive operations
    validate_input_path(input_path, must_be_dir=True)

    files = list(input_path.glob("**/*.md"))
    require_minimum_files(
        files,
        minimum=2,
        context="analysis requires multiple files for comparison"
    )

    # Now safe to proceed
    return await process_files(files)
```

---

## Packaging for Collections

### Directory Structure in Collection

```
my-collection/
  scenario-tools/
    my_analyzer/              # Tool directory
      main.py
      pyproject.toml

      analyzer/core.py        # Configs
      synthesizer/core.py

      README.md               # User guide
      HOW_TO_BUILD.md         # Builder guide
```

### pyproject.toml for Tool

```toml
[project]
name = "my-analyzer"
version = "1.0.0"
description = "Document analyzer scenario tool"

dependencies = [
    "click>=8.1.0",
]

[project.scripts]
my-analyzer = "my_analyzer.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Installation Process

When collection is installed:

```bash
amplifier collection add git+https://github.com/user/my-collection
```

Amplifier automatically:

1. Discovers `scenario-tools/*/pyproject.toml`
2. Installs each tool via `uv tool install`
3. Tools become available in PATH

Users can then:

```bash
# Direct usage
my-analyzer docs/

# Via uvx
uvx --from my-collection my-analyzer docs/
```

---

## Toolkit Integration

Use toolkit utilities for common needs:

```python
from amplifier_app_cli.toolkit import (
    discover_files,          # Recursive file discovery
    read_with_retry,         # Cloud-sync aware reading
    write_with_retry,        # Cloud-sync aware writing
    ProgressReporter,        # Progress display
    validate_input_path,     # Path validation
    require_minimum_files    # Input validation
)

async def my_tool(input_dir: Path):
    """Use toolkit utilities for robustness."""

    # Validate input
    validate_input_path(input_dir, must_be_dir=True)

    # Discover files recursively
    files = discover_files(input_dir, "**/*.md")
    require_minimum_files(files, minimum=2, context="need multiple files")

    # Process with progress
    progress = ProgressReporter(len(files), "Analyzing")
    results = []

    for file in files:
        # Cloud-sync aware reading
        content = read_with_retry(file)

        # Process
        async with AmplifierSession(config=PROCESSOR_CONFIG) as session:
            result = await session.execute(f"Process: {content}")

        results.append(result)
        progress.update()

    progress.finish()

    # Cloud-sync aware writing
    write_with_retry(results, Path("results.json"))

    return results
```

See [TOOLKIT_GUIDE.md](TOOLKIT_GUIDE.md) for complete toolkit documentation.

---

## Best Practices

### 1. One Config Per Cognitive Role

**GOOD**: Separate configs for separate thinking

```python
ANALYZER_CONFIG = {"temperature": 0.3, ...}      # Analytical
IMPROVER_CONFIG = {"temperature": 0.7, ...}      # Creative
EVALUATOR_CONFIG = {"temperature": 0.2, ...}     # Critical
```

**BAD**: One config tries to do everything

```python
GENERIC_CONFIG = {"temperature": 0.5, ...}  # Compromises all tasks
```

### 2. Checkpoint After Expensive Operations

```python
# GOOD: Save after each LLM call
async with AmplifierSession(config=CONFIG) as session:
    result = await session.execute(expensive_prompt)
save_state({"stage1": result})  # Checkpoint immediately

# BAD: Save only at end
results = await process_all_stages()
save_state(results)  # Lost everything if crashed
```

### 3. Fail Fast with Clear Messages

```python
# GOOD: Validate early
files = discover_files(input_dir, "**/*.md")
if len(files) < 2:
    click.echo("Error: Need at least 2 files for comparison", err=True)
    sys.exit(1)

# BAD: Fail late with confusing error
async with AmplifierSession(...) as session:
    # Crashes later with "not enough files"
```

### 4. Use Toolkit Utilities

```python
# GOOD: Use toolkit
from amplifier_app_cli.toolkit import discover_files, ProgressReporter
files = discover_files(input_dir, "**/*.md")  # Recursive, validated

# BAD: Manual implementation
files = list(Path(input_dir).glob("*.md"))  # Only top-level!
```

### 5. Document the Recipe

**HOW_TO_BUILD.md** should include:

- **Metacognitive recipe diagram** (what stages, what order)
- **Config roles** (why each config exists, temperature choice)
- **Flow control** (loops, conditionals, human gates)
- **State management** (checkpoints, resume logic)
- **Testing approach** (how to verify each stage)

---

## Example: Tutorial Analyzer

The `tutorial_analyzer` in the toolkit is the pedagogical exemplar. Study it to understand:

**Six specialized configs**:

1. **Analyzer** (temp=0.3) - Structure analysis
2. **Learner Simulator** (temp=0.5) - User perspective
3. **Diagnostician** (temp=0.1) - Issue identification
4. **Improver** (temp=0.7) - Improvement generation
5. **Critic** (temp=0.2) - Quality scoring
6. **Synthesizer** (temp=0.3) - Results synthesis

**Complex flow control**:

- Multi-stage pipeline
- Quality loops (improve until score > threshold)
- Human approval gates
- Nested evaluations
- State checkpointing

**Complete documentation**:

- README.md - User guide
- HOW_TO_BUILD.md - Builder guide
- Inline comments explaining recipe

See: `amplifier-app-cli/toolkit/examples/tutorial_analyzer/`

---

## Advanced Topics

### Nested Loops and Conditional Jumps

Scenario tools support arbitrary complexity in flow control:

```python
# Outer quality loop
for overall_iteration in range(3):

    # Inner section loop
    for section in tutorial.sections:
        issues = await analyze_section(section)

        if has_critical_issues(issues):
            # Jump to specialized repair
            section = await critical_repair(section)

        score = await score_section(section)
        if score < 0.7:
            # Re-analyze with different config
            async with AmplifierSession(config=DEEP_ANALYZER) as session:
                deep_issues = await session.execute(f"Deep analyze: {section}")
            section = await apply_fixes(section, deep_issues)

    # Outer quality check
    overall_score = await score_tutorial(tutorial)
    if overall_score > 0.9:
        break  # Success, exit early

return tutorial
```

### Dynamic Config Selection

Choose config based on input characteristics:

```python
def select_config(content: str) -> dict:
    """Choose config based on content."""

    word_count = len(content.split())

    if word_count < 100:
        return SIMPLE_CONFIG  # Fast model, low temp
    elif word_count < 1000:
        return STANDARD_CONFIG  # Balanced
    else:
        return DEEP_CONFIG  # Powerful model, high context

async def adaptive_process(content: str):
    """Use different config based on input."""

    config = select_config(content)

    async with AmplifierSession(config=config) as session:
        return await session.execute(f"Process: {content}")
```

### Partial Failure Handling

Continue processing even when some items fail:

```python
async def resilient_batch(items: list):
    """Process batch with partial failure handling."""

    results = []
    errors = []

    for item in items:
        try:
            async with AmplifierSession(config=PROCESSOR_CONFIG) as session:
                result = await session.execute(f"Process: {item}")
            results.append({"item": item, "result": result})
        except Exception as e:
            errors.append({"item": item, "error": str(e)})
            # Continue to next item

    return {
        "completed": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors
    }
```

---

## Testing Scenario Tools

### Unit Testing Configs

```python
# tests/test_configs.py
import pytest
from analyzer.core import ANALYZER_CONFIG

def test_analyzer_config():
    """Verify analyzer config structure."""

    assert "session" in ANALYZER_CONFIG
    assert "providers" in ANALYZER_CONFIG

    provider = ANALYZER_CONFIG["providers"][0]
    assert provider["config"]["temperature"] == 0.3  # Analytical
    assert "claude-sonnet" in provider["config"]["model"]
```

### Integration Testing

```python
# tests/test_pipeline.py
import pytest
from pathlib import Path
from main import run_pipeline

@pytest.mark.asyncio
async def test_complete_pipeline(tmp_path):
    """Test full pipeline on sample input."""

    # Create test input
    test_file = tmp_path / "test.md"
    test_file.write_text("# Test Document\n\nSample content.")

    # Run pipeline
    state = {"stage": "ANALYZE", "results": {}}
    result = await run_pipeline(tmp_path, state)

    # Verify results
    assert "analysis" in result["results"]
    assert "synthesis" in result["results"]
```

### Manual Testing

```bash
# Test with real input
uv run python main.py test_data/

# Test resumability
uv run python main.py test_data/ --resume

# Test error handling
uv run python main.py empty_dir/  # Should fail gracefully
```

---

## Common Patterns

### File Discovery

```python
from amplifier_app_cli.toolkit import discover_files

# GOOD: Recursive discovery
files = discover_files(base_path, "**/*.md")

# BAD: Only top-level
files = list(base_path.glob("*.md"))
```

### Progress Visibility

```python
from amplifier_app_cli.toolkit import ProgressReporter

# GOOD: Clear progress
progress = ProgressReporter(len(items), "Processing")
for item in items:
    process(item)
    progress.update()
progress.finish()

# BAD: Silent processing
for item in items:
    process(item)  # User has no idea what's happening
```

### Input Validation

```python
from amplifier_app_cli.toolkit import validate_input_path, require_minimum_files

# GOOD: Validate early
validate_input_path(input_dir, must_be_dir=True)
files = discover_files(input_dir, "**/*.md")
require_minimum_files(files, minimum=2, context="need multiple files")

# BAD: Fail late
# Process for 10 minutes, then discover not enough files
```

---

## Distribution

### Via Collection

```bash
# Users install your collection
amplifier collection add git+https://github.com/user/my-collection

# Tools automatically available
my-analyzer --help
```

### Standalone

```bash
# Users install tool directly
uv tool install git+https://github.com/user/my-analyzer

# Or use via uvx
uvx --from git+https://github.com/user/my-analyzer my-analyzer [args]
```

### Development Installation

```bash
# Install locally for testing
cd scenario-tools/my_analyzer
uv tool install .

# Test
my-analyzer test_input/

# Uninstall
uv tool uninstall my-analyzer
```

---

## Learning Path

**1. Study the exemplar**:

- Read: `amplifier-app-cli/toolkit/examples/tutorial_analyzer/README.md`
- Understand: Multi-config pattern, state management, flow control
- Run: Test with sample tutorials

**2. Review the template**:

- Read: `amplifier-app-cli/toolkit/templates/standalone_tool.py`
- Understand: Basic structure, config definition, orchestration

**3. Read advanced guides**:

- [TOOLKIT_GUIDE.md](TOOLKIT_GUIDE.md) - Complete toolkit reference
- `toolkit/METACOGNITIVE_RECIPES.md` - Advanced patterns
- `toolkit/HOW_TO_CREATE_YOUR_OWN.md` - Step-by-step creation

**4. Build your own**:

- Start simple (2-3 stages)
- Add complexity as needed
- Test thoroughly
- Document the recipe

---

## Troubleshooting

### Tool not found after collection install

**Problem**: Installed collection but tool not in PATH

**Solution**:

```bash
# Verify tool was discovered
amplifier collection show my-collection  # Check "Scenario Tools" section

# Reinstall collection
amplifier collection remove my-collection
amplifier collection add git+https://github.com/user/my-collection

# Verify uv tool install
uv tool list | grep my-tool
```

### Config errors

**Problem**: `ModuleNotFoundError` or `SourceNotFoundError`

**Solution**: Every module reference needs `source` URL:

```python
"providers": [{
    "module": "provider-anthropic",
    "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",  # Required!
    "config": {...}
}]
```

### State file corruption

**Problem**: Resume fails with JSON decode error

**Solution**:

```bash
# Delete corrupted state
rm .my_analyzer_state.json

# Start fresh
my-analyzer input/ --resume
```

---

## Related Documentation

- **[Collections User Guide](https://github.com/microsoft/amplifier-collections/blob/main/docs/USER_GUIDE.md)** - Using collections
- **[Collection Authoring](https://github.com/microsoft/amplifier-collections/blob/main/docs/AUTHORING.md)** - Creating collections
- [TOOLKIT_GUIDE.md](TOOLKIT_GUIDE.md) - Toolkit utilities and multi-config patterns
- **[Agent Authoring](https://github.com/microsoft/amplifier-profiles/blob/main/docs/AGENT_AUTHORING.md)** - Creating agents for collections
- **[Profile Authoring](https://github.com/microsoft/amplifier-profiles/blob/main/docs/PROFILE_AUTHORING.md)** - Creating profiles for collections

---

**Document Version**: 1.0
**Last Updated**: 2025-10-26
