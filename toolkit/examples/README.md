# Toolkit Examples

## Philosophy: Metacognitive Recipes with Multi-Config Patterns

These examples demonstrate how to build sophisticated AI tools using **metacognitive recipes** - code-orchestrated multi-stage thinking processes where each stage uses a specialized AI config.

**Core Principle**: Code orchestrates, specialized configs think

- **Code handles**: Flow control, state management, decisions, loops, human interaction
- **Multiple configs handle**: Each cognitive subtask (analytical, creative, evaluative)
- **Amplifier-core provides**: The mechanism to execute any config
- **Toolkit utilities provide**: File discovery, progress reporting, validation

## Available Examples

### tutorial_analyzer/

**Purpose**: Improves tutorials through multi-stage metacognitive analysis

**The Pedagogical Exemplar** - This is THE example to study for understanding the multi-config metacognitive recipe pattern.

**What it demonstrates**:
- **6 specialized configs** - Each optimized for its cognitive role:
  - `ANALYZER_CONFIG` (analytical, temp=0.3) - Extract tutorial structure
  - `LEARNER_SIMULATOR_CONFIG` (empathetic, temp=0.5) - Simulate learner experience
  - `DIAGNOSTICIAN_CONFIG` (precise, temp=0.1) - Identify pedagogical issues
  - `IMPROVER_CONFIG` (creative, temp=0.7) - Generate improvement suggestions
  - `CRITIC_CONFIG` (evaluative, temp=0.2) - Evaluate improvement quality
  - `SYNTHESIZER_CONFIG` (analytical, temp=0.3) - Create final recommendations

- **Multi-stage orchestration** - Code manages flow between 7 stages
- **Human-in-loop** - Strategic decision point (approve improvement plan)
- **State management** - Checkpointing after each stage for resumability
- **Evaluative loops** - Re-simulate and score to decide if done
- **Complex flow control** - Nested loops, conditional jumps, context accumulation

**Structure**:
```
tutorial_analyzer/
  main.py                     # Main orchestration & CLI
  state.py                    # State management

  analyzer/core.py            # Stage 1: Content analysis
  learner_simulator/core.py   # Stage 2: Learner simulation
  diagnostician/core.py       # Stage 3: Issue diagnosis
  improver/core.py            # Stage 4: Improvement generation
  critic/core.py              # Stage 5: Quality evaluation
  synthesizer/core.py         # Stage 6: Final synthesis

  pyproject.toml             # Package metadata for uvx
  README.md                  # Complete usage guide
```

**Pipeline**:
```
Analyze → Simulate Learner → Diagnose Issues →
→ Plan Improvements [HUMAN] → Apply Fixes →
→ Re-Simulate → Score Quality → Decide [HYBRID] →
→ Loop or Finalize
```

**Run it**:
```bash
# Via uvx (after packaging)
uvx tutorial-analyzer tutorial.md clarity engagement

# Or directly
python toolkit/examples/tutorial_analyzer/main.py tutorial.md clarity engagement
```

**Expected workflow**:
```
1. Analyzing tutorial structure...
2. Simulating learner experience...
3. Diagnosing pedagogical issues...
4. Generating improvements...

Proposed Improvements:
- Add code examples in section 2
- Clarify prerequisites
- Improve exercise scaffolding

Approve improvements? (yes/no/modify): yes

5. Evaluating improvement quality...
6. Synthesizing recommendations...

✓ Tutorial evolution complete
✓ Quality score: 0.85
✓ Results saved to .tutorial_analyzer_state.json
```

See `tutorial_analyzer/README.md` for complete documentation.

## The Pattern Each Example Follows

### Multi-Config Orchestration

```python
# Define specialized configs (not one!)
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {"model": "claude-sonnet-4", "temperature": 0.3}  # Analytical
    }],
}

CREATOR_CONFIG = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {"model": "claude-opus-4", "temperature": 0.7}  # Creative
    }],
}

# Code orchestrates thinking
async def process():
    # Stage 1: Analytical config
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        analysis = await session.execute(...)

    # Stage 2: Creative config
    async with AmplifierSession(config=CREATOR_CONFIG) as session:
        creation = await session.execute(...)

    # Code makes decision
    if needs_iteration(analysis, creation):
        # Loop back...
        pass

    return creation
```

### State Management with Checkpointing

```python
STATE_FILE = ".tool_state.json"

async def multi_stage_with_resumability():
    state = load_state()

    # Stage 1
    if "stage1" not in state:
        async with AmplifierSession(config=CONFIG1) as session:
            state["stage1"] = await session.execute(...)
        save_state(state)  # Checkpoint

    # Stage 2
    if "stage2" not in state:
        async with AmplifierSession(config=CONFIG2) as session:
            state["stage2"] = await session.execute(...)
        save_state(state)  # Checkpoint

    return state
```

## What Examples Show

### ✅ Correct Patterns

**Multi-config orchestration**:
- Multiple specialized configs (each optimized for its role)
- Code orchestrates between configs
- Different temperatures for different cognitive tasks

**State management**:
- Tool-specific state structure (simple dict)
- Checkpoint after each stage
- Resumability on failure

**Flow control**:
- Code decides routing (which config when)
- Loops and conditionals in code (not AI)
- Human-in-loop at strategic points

**Error handling**:
- Graceful failures per stage
- Continue processing when possible
- Return partial results

**Toolkit utilities**:
- File discovery with `discover_files`
- Progress reporting with `ProgressReporter`
- Validation with `validate_input_path`, `require_minimum_files`

### ❌ Anti-Patterns Avoided

**Single-config pattern**:
- ❌ One config with compromise temperature
- ✅ Multiple configs, each optimized

**Session wrapping**:
- ❌ Classes that wrap AmplifierSession
- ✅ Use AmplifierSession directly

**State frameworks**:
- ❌ Generic state management classes
- ✅ Tool-specific state (simple dict to JSON)

**LLM bypass**:
- ❌ Direct API calls to Anthropic/OpenAI
- ✅ ALL LLM via amplifier-core

## Learning Path

### 1. Study tutorial_analyzer

The complete exemplar showing:
- How to structure multi-config tools
- How code orchestrates thinking
- How to checkpoint state
- How to integrate human-in-loop
- How to implement evaluative loops

Read `tutorial_analyzer/README.md` for complete walkthrough.

### 2. Understand the Pattern

Key questions to ask when building your own:
- **What are the cognitive stages?** (analyze, create, evaluate, etc.)
- **What config does each stage need?** (analytical, creative, evaluative)
- **How does code decide flow?** (loops, conditionals, human input)
- **Where to checkpoint?** (after every significant stage)

### 3. Review Supporting Docs

- `toolkit/METACOGNITIVE_RECIPES.md` - Deep dive on multi-config patterns
- `toolkit/HOW_TO_CREATE_YOUR_OWN.md` - Step-by-step creation guide
- `toolkit/BEST_PRACTICES.md` - Strategic guidance and decomposition
- `toolkit/PHILOSOPHY.md` - Why multi-config, mechanism vs policy

## Creating Your Own Tool

### Start Simple

Begin with 2-3 configs:
```python
ANALYZER_CONFIG = {...}   # Analytical (temp=0.3)
CREATOR_CONFIG = {...}    # Creative (temp=0.7)

# Orchestrate
async def my_tool(input_data):
    # Analyze
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        analysis = await session.execute(...)

    # Create
    async with AmplifierSession(config=CREATOR_CONFIG) as session:
        creation = await session.execute(...)

    return creation
```

### Add Sophistication As Needed

Only add complexity when you need it:
- Add evaluator config if you need quality loops
- Add human-in-loop if strategic decisions needed
- Add state checkpointing if tool is long-running
- Add complex flow control if simple linear flow insufficient

### Test Early

```bash
# Test with real inputs
python my_tool.py ./test_data

# Check state management
cat .my_tool_state.json

# Test resumability (kill and restart)
^C  # Ctrl-C to interrupt
python my_tool.py ./test_data  # Should resume from checkpoint
```

## Configuration Temperature Guide

| Cognitive Role | Temperature | Use For |
|----------------|-------------|---------|
| **Analytical** | 0.1-0.3 | Structure extraction, classification, diagnosis |
| **Empathetic** | 0.4-0.6 | User simulation, perspective-taking |
| **Creative** | 0.6-0.8 | Content generation, brainstorming |
| **Evaluative** | 0.1-0.3 | Quality assessment, scoring, critique |
| **Synthesizing** | 0.3-0.5 | Combining information, summarization |

## Packaging for Distribution

All examples are designed to be packageable via uvx:

```bash
# In example directory (e.g., tutorial_analyzer/)
uv build

# Install locally
uvx ./dist/tutorial_analyzer-*.whl

# Or publish to PyPI
uv publish
```

See `toolkit/PACKAGING_GUIDE.md` for complete packaging instructions.

## Philosophy Alignment

These examples embody:
- **Mechanism not policy** - AmplifierSession unchanged, configs = policy decisions
- **Policy at edges** - Tools decide all configs
- **Ruthless simplicity** - Start simple, add complexity only when needed
- **Modular design** - Clear stages, each regeneratable from spec
- **Code for structure, AI for intelligence** - Each does what it does best

## Common Questions

**Q: Why multiple configs instead of one?**
A: Different cognitive tasks need different setups. Analytical tasks need low temperature (precision), creative tasks need high temperature (diversity). One config can't optimize for both.

**Q: How many configs should I have?**
A: Start with 2-3. Add more only if you have distinct cognitive roles that need optimization. tutorial_analyzer has 6 because it has 6 distinct cognitive subtasks.

**Q: Should I always checkpoint after every stage?**
A: For long-running multi-stage tools, yes. For fast tools with 2-3 quick stages, optional.

**Q: When should I add human-in-loop?**
A: At strategic decision points where human judgment is valuable (approve plan, choose direction, validate safety).

## Summary

The toolkit examples teach you to build sophisticated AI tools by:

1. **Identifying cognitive stages** - What thinking tasks does the tool need?
2. **Defining specialized configs** - Optimize each for its cognitive role
3. **Orchestrating with code** - Flow control, state, decisions
4. **Checkpointing progress** - Save after each stage
5. **Adding human-in-loop** - Strategic decision points

**Start with tutorial_analyzer** - study it, run it, understand it. Then build your own using the same patterns.

**Remember**: Sophisticated tools emerge from simple, well-composed pieces. Each config is simple, each stage is simple - sophistication comes from orchestration.
