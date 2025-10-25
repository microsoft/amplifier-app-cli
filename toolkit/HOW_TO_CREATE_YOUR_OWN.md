# Creating Your Own Amplifier Tools

Amplifier is designed so **you can create new AI-powered tools** just by describing how they should think. This guide will walk you through the process of turning an idea into a working **standalone tool** using **metacognitive recipes** – structured thought processes that the AI will follow. You'll describe the **problem**, outline the **approach**, and build the solution using multi-config patterns.

**Workflow Overview:**

1. **Identify a Problem or Need** – Pick a task or workflow you want to automate or improve.
2. **Formulate a Metacognitive Recipe** – Describe the step-by-step thinking process an expert would use.
3. **Build the Tool** – Create specialized configs and orchestration code.
4. **Refine and Integrate** – Test the tool, iterate until it works well.
5. **Package and Share** – Package for uvx distribution and share with others.

## 1. Identify a Problem or Need

Every great tool begins with a **clear need**. Start by pinpointing a task that is repetitive, complex, or time-consuming – something you wish an AI assistant could handle reliably. This could be anything from _research synthesis_ (gathering and refining information on a topic) to _tutorial improvement_ (analyzing educational content and suggesting enhancements). The key is that you can describe **what the goal is** and **what a successful outcome looks like**.

If you're unsure what to build, try **brainstorming**. For example, you might consider:
- **Documentation Quality Analyzer** - Identify gaps, inconsistencies, unclear sections
- **Code Review Assistant** - Multi-perspective analysis (security, performance, maintainability)
- **Tutorial Evolver** - Simulate learners, diagnose issues, suggest improvements
- **Research Synthesizer** - Extract themes, deep-dive selected topics, generate report

Use your own experience and needs to choose an idea that would be genuinely useful to you. Remember, Amplifier works best when the problem is something **concrete** that you can break down into parts.

## 2. Formulate a Metacognitive Recipe

Once you have a problem in mind, **outline the approach** an expert (or you, on your best day) would take to solve it. This outline is your **metacognitive recipe** – essentially the game plan for the tool. Focus on **how the AI should think**, not just what it should do. Think in terms of stages, decision points, and loops:

### Breaking the Task Into Stages

Divide the problem into logical phases or sub-tasks. Each step should be something the AI can tackle with a **specialized cognitive approach**. For example, a tutorial improvement tool might have stages for:

1. **Content Analysis** (analytical thinking, temp=0.3) - Extract structure, identify sections, classify content type
2. **Learner Simulation** (empathetic thinking, temp=0.5) - Simulate a learner experiencing the tutorial, identify confusion points
3. **Issue Diagnosis** (precision thinking, temp=0.1) - Analyze learner difficulties, categorize pedagogical issues
4. **Improvement Generation** (creative thinking, temp=0.7) - Generate specific, actionable improvements
5. **Quality Evaluation** (evaluative thinking, temp=0.2) - Assess improvement quality, score effectiveness
6. **Final Synthesis** (analytical thinking, temp=0.3) - Combine evaluations into final recommendations

If a task feels too big or complex, it's a sign to decompose it into smaller steps or tools. Amplifier excels at this incremental approach. As a rule of thumb, **avoid making one tool handle "everything at once"** – smaller focused steps improve reliability. (_For more strategies on breaking down problems, see **BEST_PRACTICES.md** in this toolkit, which covers decomposition patterns._)

### Characterizing Each Stage

For each stage, think about the **cognitive role**:

| Cognitive Role | Temperature | Characteristics |
|----------------|-------------|-----------------|
| **Analytical** | 0.1-0.3 | Precise, structured, extracting patterns, classification |
| **Empathetic** | 0.4-0.6 | Perspective-taking, simulation, understanding users |
| **Creative** | 0.6-0.8 | Generating novel content, exploring possibilities |
| **Evaluative** | 0.1-0.3 | Judging quality, scoring, critique, consistency |
| **Synthesizing** | 0.3-0.5 | Combining information, summarization, clarity |

**Different roles need different configs.** This is the core insight of multi-config metacognitive recipes.

### Planning Checkpoints and Loops

Consider what information each step needs and when to pause for review:
- **Checkpoints**: Should the tool save state after each stage? (Yes, for multi-stage tools - enables resumability)
- **Human-in-loop**: Should humans approve key decisions? (Yes, at strategic points - approve plan, validate safety)
- **Quality loops**: Should the tool evaluate its own output and iterate? (Yes, for quality-critical tools - generate → evaluate → improve)

By building in checkpoints or reviews (even if just AI self-reviews), you make the process more robust. A recipe might include a loop where the AI evaluates its own output or seeks human feedback before proceeding to the next stage.

### Planning for Errors or Ambiguity

Metacognitive recipes often include fallback plans. Think about what the AI should do if a step produces incomplete or low-quality results:
- "If the analysis is incomplete, try again with more specific prompts"
- "If quality score is below threshold, regenerate with feedback from evaluation"
- "If no data is found, explain the issue rather than proceeding blindly"

Designing these recovery or iteration steps helps the tool adapt when things don't go perfectly on the first try.

### Writing Down Your Recipe

Write your recipe in plain language. It can be a numbered list of steps or a few short paragraphs describing the flow. The goal is to **describe the thinking process** clearly enough that you understand the intended logic. You're essentially programming the AI with instructions, except you're doing it in natural language. Don't worry about syntax – **clarity and structure** are what count.

> **Tip:** Aim for the level of detail you'd give if delegating the task to a smart colleague. Include important details (criteria for decisions, what outputs to generate, etc.), but don't micromanage every tiny action. Focus on the high-level game plan and cognitive roles needed.

## 3. Build the Tool

With your idea and recipe in hand, it's time to **build the tool**. You'll create specialized configs for each cognitive stage and code to orchestrate them.

### Project Structure

Start with this structure:

```bash
mkdir my-tool
cd my-tool

# Create package structure
mkdir -p src/my_tool
touch src/my_tool/__init__.py
touch src/my_tool/main.py
touch src/my_tool/state.py

# For multi-config tools, create stage modules
mkdir -p src/my_tool/stage1_analyzer
touch src/my_tool/stage1_analyzer/__init__.py
touch src/my_tool/stage1_analyzer/core.py

mkdir -p src/my_tool/stage2_creator
touch src/my_tool/stage2_creator/__init__.py
touch src/my_tool/stage2_creator/core.py

# Add packaging files
touch pyproject.toml
touch README.md
```

### Define Specialized Configs

For each stage in your recipe, create a specialized config:

```python
# src/my_tool/stage1_analyzer/core.py
"""Stage 1: Content analysis (analytical thinking)."""

from amplifier_core import AmplifierSession

ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4",
            "temperature": 0.3,  # Analytical precision
            "system_prompt": "You are an expert content analyzer. Extract structure and patterns with precision."
        }
    }]
}

async def analyze(content: str) -> dict:
    """Analyze content structure."""
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        result = await session.execute(f"Analyze this content:\n\n{content}")
    return result
```

Repeat for each cognitive stage, adjusting temperature and system prompt appropriately.

### Write Orchestration Code

In `main.py`, orchestrate the stages:

```python
"""Main orchestration across stages."""

import asyncio
from pathlib import Path
from .stage1_analyzer.core import analyze
from .stage2_creator.core import create
from .stage3_evaluator.core import evaluate
from .state import load_state, save_state


async def process(input_path: Path):
    """Multi-stage pipeline."""
    state = load_state()
    content = input_path.read_text()

    # Stage 1: Analyze
    if "analysis" not in state:
        state["analysis"] = await analyze(content)
        save_state(state)  # Checkpoint

    # Stage 2: Create
    if "creation" not in state:
        state["creation"] = await create(state["analysis"])
        save_state(state)  # Checkpoint

    # Stage 3: Evaluate
    if "evaluation" not in state:
        state["evaluation"] = await evaluate(state["creation"])
        save_state(state)

    # Code makes decision
    if state["evaluation"]["score"] < 0.8:
        # Iterate with feedback...
        del state["creation"]  # Re-generate
        return await process(input_path)

    return state["creation"]


def cli():
    """CLI entry point."""
    import sys
    if len(sys.argv) < 2:
        print("Usage: my-tool <input-file>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    result = asyncio.run(process(input_path))
    print(f"Complete! Result: {result}")


if __name__ == "__main__":
    cli()
```

### Add State Management

In `state.py`:

```python
"""State management for resumability."""

import json
from pathlib import Path

STATE_FILE = ".my_tool_state.json"


def save_state(state: dict):
    """Save state to file."""
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))


def load_state() -> dict:
    """Load state if exists."""
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text())
    return {}
```

### Configure for Packaging

Create `pyproject.toml` (see `PACKAGING_GUIDE.md` for complete template):

```toml
[project]
name = "my-tool"
version = "0.1.0"
description = "Your tool description"
requires-python = ">=3.11"
dependencies = ["amplifier-core"]

[project.optional-dependencies]
amplifier = [
    "amplifier-module-provider-anthropic @ git+https://...",
]

[project.scripts]
my-tool = "my_tool.main:cli"

[tool.uv.sources]
amplifier-core = { git = "https://github.com/microsoft/amplifier-core", branch = "main" }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

## 4. Refine the Tool (Iterate and Improve)

Newly created tools might work on the first try, but often you'll need a round of tweaking to get them just right. Treat this as an iterative process:

### Test the Tool

```bash
# Install locally
cd my-tool
uv sync --all-extras

# Run on sample input
uv run my-tool test_input.md

# Check state
cat .my_tool_state.json

# Test resumability (kill and restart)
^C  # Ctrl-C during execution
uv run my-tool test_input.md  # Should resume from checkpoint
```

### Observe and Adjust

As it runs, watch for issues:
- **Does it skip a stage?** Check state management logic
- **Is output not what you expected?** Adjust temperature or system prompt for that stage
- **Does it fail?** Add error handling, improve validation
- **Is flow too rigid?** Add conditional routing based on results

### Common Adjustments

**Adjust temperature**:
```python
# Too random? Lower temperature
"temperature": 0.3  # Was 0.5

# Too rigid? Raise temperature
"temperature": 0.6  # Was 0.3
```

**Adjust system prompt**:
```python
# Not focused enough? Be more specific
"system_prompt": "You are an expert tutorial analyzer. Focus on pedagogical structure and clarity."  # More specific
```

**Add quality loop**:
```python
# Not achieving quality? Add evaluation loop
if evaluation["score"] < threshold:
    # Regenerate with feedback
    del state["creation"]
    state["iteration"] = state.get("iteration", 0) + 1
    return await process(input_path)  # Iterate
```

**Add human-in-loop**:
```python
# Critical decision? Add human approval
print(f"Proposed Plan:\n{state['plan']}\n")
approval = input("Approve? (yes/no): ")
if approval != "yes":
    return {"status": "rejected"}
```

### Iterate Until Satisfied

Repeat testing and adjusting. Since metacognitive recipes are about **describing thinking**, focus on:
- **Is each stage doing the right kind of thinking?** (analytical vs creative vs evaluative)
- **Is flow logical?** (does one stage properly feed the next)
- **Are decision points clear?** (when to loop, when to get human input)
- **Is state properly managed?** (checkpointing, resumability)

Throughout this refinement, keep the **metacognitive principles** in mind: if a particular step is failing, maybe it needs to be broken into two steps, or given a different cognitive approach (different config). Don't hesitate to iterate; this is normal.

## 5. Use Your Tool and Share

Congratulations – you've built a new tool! Now it's time to put it to work:

### Package for Distribution

```bash
# Build the package
uv build

# Test distribution
uvx ./dist/my_tool-0.1.0-py3-none-any.whl test_input.md

# Publish to PyPI (optional)
uv publish
```

See `PACKAGING_GUIDE.md` for complete packaging instructions.

### Direct Usage

You can use your tool in multiple ways:

```bash
# Via uvx (ephemeral, no install)
uvx my-tool input.md

# Via uv tool (persistent install)
uv tool install my-tool
my-tool input.md

# From git (development versions)
uvx --from git+https://github.com/you/my-tool@main my-tool input.md
```

### Combination and Composition

One of the most powerful aspects of building tools this way is that **tools can be combined**. Your new tool can be used alongside others to handle bigger tasks:

- Use a _markdown extractor_ to process documents, then feed results to your _research synthesizer_
- Use your _tutorial evolver_ to improve tutorials, then a _documentation publisher_ to deploy them
- Chain tools together - output of one becomes input to another

Over time, you'll build up a suite of specialized tools, and you'll find you can compose them to accomplish complex, higher-order workflows. This composability is by design: **small tools can work in concert to solve large problems**.

### Reusable Recipes

The recipe you encoded in your tool is now reusable. The pattern you built (e.g. _"analyze → simulate → diagnose → improve"_) can be repurposed for different domains:

- Tutorial improvement → Documentation improvement
- Code review → Writing review
- Research synthesis → Knowledge synthesis

The metacognitive recipe pattern transfers across domains. Each tool you build teaches you more about structuring AI thinking.

### Daily Improvements

As you use your tool, you'll discover refinements:
- Better prompts for specific stages
- More effective temperature tuning
- Improved flow control logic
- New patterns for orchestration

These insights feed back into your next tool. You're not just building tools; you're building **expertise in designing AI thinking processes**.

## 6. Sharing and Learning

### Learn from Examples

To deepen your understanding and improve your recipe-writing skills, study the examples in this toolkit:

- **tutorial_analyzer** (`toolkit/examples/tutorial_analyzer/`) - Complete pedagogical exemplar
  - 6 specialized configs (analyzer, learner_simulator, diagnostician, improver, critic, synthesizer)
  - Multi-stage orchestration with human-in-loop
  - Evaluative loops with quality thresholds
  - State management and resumability

- **Metacognitive Recipes Guide** (`toolkit/METACOGNITIVE_RECIPES.md`) - Deep patterns and configuration spectrum

- **Best Practices** (`toolkit/BEST_PRACTICES.md`) - Strategic guidance on decomposition, iteration, learning

- **Philosophy** (`toolkit/PHILOSOPHY.md`) - Why multi-config, mechanism vs policy alignment

### Share Your Tools

If your tool is broadly useful, consider sharing it:
- Publish to PyPI (public package registry)
- Share on GitHub (open source)
- Write about it (blog posts, documentation)
- Contribute patterns back to toolkit (if generally applicable)

Your tools extend the Amplifier ecosystem. Others can learn from your recipes and build on your patterns.

## Deep Dive: The Metacognitive Recipe Process

### What Is Metacognitive Thinking?

**Metacognition** = "thinking about thinking"

When experts solve complex problems, they don't just _do_ the task - they think about:
- **What kind of thinking does this task need?** (analytical, creative, evaluative)
- **What's the right approach?** (systematic analysis vs exploratory brainstorming)
- **How do I know if I'm on the right track?** (evaluation criteria, quality thresholds)
- **When should I iterate?** (evaluate, get feedback, improve)

**Metacognitive recipes** encode this expert thinking process into code and configs:
- **Code** decides which kind of thinking when (orchestration)
- **Configs** provide the right cognitive setup for each kind of thinking (temperature, prompt, model)

### Designing Cognitive Subtasks

When designing your recipe, identify the cognitive subtasks:

**Example: Research Synthesis Tool**

**Bad** (one monolithic task):
```
"Research this topic and create a comprehensive report"
```

**Good** (decomposed into cognitive subtasks):
```
1. Web search (retrieval, temp=0.3) - Find relevant sources
2. Extraction (analytical, temp=0.3) - Extract key themes from sources
3. Theme ranking (evaluative, temp=0.2) - Rank themes by importance and interest
4. Deep research (analytical, temp=0.4) - Deep dive into top themes
5. Synthesis (creative, temp=0.6) - Weave deep research into coherent narrative
6. Critique (evaluative, temp=0.2) - Evaluate report quality
7. Revision (creative, temp=0.7) - Address critique issues
```

Each subtask has a clear cognitive role and optimized config.

### Orchestration Patterns

Code orchestrates the subtasks. Common patterns:

**Linear pipeline**:
```python
analysis = await analyze(input)
creation = await create(analysis)
evaluation = await evaluate(creation)
return creation
```

**Quality loop**:
```python
for iteration in range(max_iterations):
    creation = await create(input)
    evaluation = await evaluate(creation)
    if evaluation["score"] > threshold:
        break  # Success
    input = add_feedback(input, evaluation)  # Iterate
return creation
```

**Conditional routing**:
```python
analysis = await analyze(input)
if analysis["needs_research"]:
    research = await research(analysis["topics"])
    creation = await create_with_research(analysis, research)
else:
    creation = await create_from_analysis(analysis)
return creation
```

**Human-in-loop**:
```python
plan = await plan_approach(input)
approval = get_human_approval(plan)  # Pause for human
if approval:
    implementation = await implement(plan)
else:
    return {"status": "rejected"}
```

See `METACOGNITIVE_RECIPES.md` for advanced flow control patterns (nested loops, goto-style jumps, multi-path convergence).

## Complete Example Walkthrough: tutorial_analyzer

Let's walk through how `tutorial_analyzer` was designed:

### 1. Problem Identified

**Need**: Tutorials are hard to write well. Need a tool that analyzes tutorials from learner perspective and suggests improvements.

### 2. Recipe Formulated

**Cognitive stages identified**:
1. **Analyze** (analytical) - Extract tutorial structure, identify sections
2. **Simulate** (empathetic) - Experience tutorial as learner, note confusion points
3. **Diagnose** (precision) - Identify pedagogical issues from learner experience
4. **Improve** (creative) - Generate specific, actionable improvements
5. **Critique** (evaluative) - Evaluate improvement quality
6. **Synthesize** (analytical) - Create final recommendations

**Human-in-loop**: After improvement generation, human approves plan before applying

**Quality loop**: If critique score < threshold, iterate

### 3. Tool Built

**6 specialized configs created** (one per stage)
**Orchestration code written** (main.py manages flow)
**State management added** (checkpoint after each stage)
**Human-in-loop integrated** (approve improvements)
**Quality loop implemented** (score → iterate if needed)

### 4. Tool Refined

**Tested on real tutorials** → Found issues → Adjusted:
- Learner simulation too generic → Made system prompt more specific
- Improvements too abstract → Added "actionable and specific" to prompt
- No iteration happening → Added quality threshold and loop logic

### 5. Tool Packaged

**Created pyproject.toml** → **Built with `uv build`** → **Tested with `uvx`** → **Published to PyPI**

**Now usable**:
```bash
uvx tutorial-analyzer tutorial.md clarity engagement
```

This walkthrough shows the complete process from idea to packaged tool.

## Tips for Success

### Start Simple

Begin with 2-3 configs:
- Analyzer (temp=0.3)
- Creator (temp=0.7)

Add more only if you have distinct cognitive roles.

### Test Early and Often

```bash
# Test after each stage implementation
uv run python -m my_tool test_input
```

Don't wait until all stages done. Test incrementally.

### Use Toolkit Utilities

Leverage structural utilities:
```python
from amplifier_app_cli.toolkit import (
    discover_files,
    ProgressReporter,
    validate_input_path,
    require_minimum_files
)
```

They handle common structural tasks so you focus on cognitive orchestration.

### Reference Production Prompts

For sophisticated system prompts, see:
- `amplifier-app-cli/data/agents/*.md` - Agent instruction templates
- `amplifier-app-cli/data/context/*.md` - Context management prompts

These evolve over time. Use as inspiration for your own prompts.

### Study the Exemplar

`tutorial_analyzer` is the complete pedagogical example. Study:
- How configs are structured
- How stages are organized
- How orchestration works
- How state is managed
- How human-in-loop is integrated

Learn from a complete working implementation.

## Summary

By following this guide, you can turn ideas into reliable, reusable Amplifier tools:

1. **Identify need** - Pick a concrete problem
2. **Formulate recipe** - Break into cognitive stages
3. **Build tool** - Create specialized configs and orchestration
4. **Refine** - Test and iterate
5. **Package** - Distribute via uvx/PyPI
6. **Share** - Extend the ecosystem

**Key principles**:
- **Multiple configs** - Each optimized for its cognitive role
- **Code orchestrates** - Flow, state, decisions
- **Checkpoint progress** - Save after each stage
- **Start simple** - 2-3 configs, iterate from there
- **Study examples** - Learn from tutorial_analyzer

**Next steps**:
1. Pick a problem you want to solve
2. Write down the cognitive stages
3. Define configs for each stage
4. Write orchestration code
5. Test and iterate
6. Package and share

**Remember**: You're not writing code for AI to execute - you're describing thinking for AI to perform, and code to orchestrate. Focus on the cognitive process, not the implementation details.

Happy building!
