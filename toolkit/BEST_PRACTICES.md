# Best Practices for Building Amplifier Tools

This guide distills hard-won insights for building effective Amplifier tools using metacognitive recipes and multi-config patterns. These aren't theoretical best practices—they're battle-tested strategies that transform how you build AI tools.

## Understanding Context Over Capability

### When Tools Don't Complete: Two Root Causes

If a tool doesn't work as expected, it likely has one or both of these problems:

1. **Too challenging for current approach** - The design genuinely exceeds what the current pattern can reliably accomplish
2. **Not enough of the _right_ context** - Missing configuration, missing cognitive stages, or missing orchestration logic

### The Context Solution Space is Bigger Than You Think

The "not enough context" problem has a _very_ big space. It could be:
- **Config optimization** - Wrong temperature for the cognitive role
- **Missing stages** - Need additional analysis or evaluation step
- **Poor orchestration** - Flow doesn't match the thinking process needed
- **Insufficient specialization** - One config trying to do too many things

**Example:** If your research tool produces shallow results, it might not be a capability problem. It might be:
- **Config issue**: Using temp=0.7 (creative) when research needs temp=0.3 (analytical)
- **Missing stage**: Needs "extract themes → prioritize → deep dive" instead of "just research"
- **Orchestration issue**: No quality loop to validate and improve results

### Multi-Config as Context Enhancement

**Multi-config pattern** is fundamentally about providing better context:

**Single-config** (limited context):
```python
ONE_CONFIG = {"temperature": 0.5, "system_prompt": "You are helpful."}

# All tasks share one context setup - compromise
async with AmplifierSession(config=ONE_CONFIG) as session:
    analysis = await session.execute("Analyze...")  # Needs temp=0.3
    creation = await session.execute("Create...")   # Needs temp=0.7
```

**Multi-config** (optimized context):
```python
ANALYZER_CONFIG = {"temperature": 0.3, "system_prompt": "You are an expert analyzer."}
CREATOR_CONFIG = {"temperature": 0.7, "system_prompt": "You are a creative generator."}

# Each task gets optimized context
async with AmplifierSession(config=ANALYZER_CONFIG) as session:
    analysis = await session.execute("Analyze...")  # Optimized!

async with AmplifierSession(config=CREATOR_CONFIG) as session:
    creation = await session.execute("Create...")   # Optimized!
```

**More configs = better context for each cognitive task.**

## Decomposition: Breaking Down Big Tools

### Building Tools That Are Too Large

If you're trying to build a tool that doesn't achieve what you hope for, consider that maybe it's trying to do too much in one swing.

**Ask yourself:** What can you decompose and break apart into smaller cognitive stages?

**The Pattern:**

1. **Identify the cognitive subtasks** - What distinct kinds of thinking are needed?
2. **Build specialized configs for each** - Optimize temperature and prompt per role
3. **Orchestrate with code** - Combine the subtasks into a pipeline
4. **Add checkpoints** - Save state between stages for resumability

**Example: Document Synthesizer**

**Too big** (one task):
```
"Read all documents and create a comprehensive synthesis report"
```

**Better** (decomposed):
```
Stage 1 (analytical, temp=0.3): Extract key concepts from each document
Stage 2 (analytical, temp=0.3): Identify themes across concepts
Stage 3 (evaluative, temp=0.2): Rank themes by importance
Stage 4 (creative, temp=0.6): Generate synthesis of top themes
Stage 5 (evaluative, temp=0.2): Critique synthesis quality
Stage 6 (creative, temp=0.7): Revise based on critique
```

Each stage is small, focused, and optimized. Orchestration code combines them.

### The Persistence Principle

**If something feels too complex to build reliably, don't give up.**

Lean into decomposition:
- Break into smaller cognitive stages
- Create specialized config for each
- Add orchestration code to combine them
- Test each stage independently
- Integrate into complete pipeline

**Bonus Value:** Smaller cognitive stages are often reusable in other tools. An "extract concepts" stage can be used in multiple tools.

## Configuration Strategy Patterns

### Pattern: Start with Fixed Configs (Level 1)

Begin with hardcoded CONFIG constants:

```python
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4",
            "temperature": 0.3,
            "system_prompt": "You are an expert analyzer."
        }
    }]
}
```

**Why start here**: Predictable, safe, simple. 90% of tools stay here.

### Pattern: Add Code-Modification When Needed (Level 2)

Only if task requirements vary at runtime:

```python
# Base template
BASE_CONFIG = {...}

def create_config(task_type: str) -> dict:
    """Code decides config based on task type."""
    config = copy.deepcopy(BASE_CONFIG)

    if task_type == "deep_research":
        config["providers"][0]["config"]["model"] = "claude-opus-4"
        config["providers"][0]["config"]["temperature"] = 0.3
    else:
        config["providers"][0]["config"]["model"] = "claude-sonnet-4"
        config["providers"][0]["config"]["temperature"] = 0.5

    return config
```

**When to use**: Task varies but within bounded options.

### Pattern: Avoid AI-Generated Configs (Level 3)

Use ONLY for low-stakes exploration:

```python
# Rare use case: exploring unknown domains
config = await generate_exploratory_config(topic)

# CRITICAL: Validate before using
validate_schema(config)
validate_module_whitelist(config)

# Use for exploration only
result = await explore_with_config(config, topic)
```

**Default**: Don't use this. Stick with Level 1 or 2.

## Orchestration Best Practices

### Practice: Checkpoint After Every Stage

```python
async def multi_stage_tool(input_path: Path):
    state = load_state()

    # Stage 1
    if "stage1" not in state:
        async with AmplifierSession(config=CONFIG1) as session:
            state["stage1"] = await session.execute(...)
        save_state(state)  # ALWAYS checkpoint

    # Stage 2
    if "stage2" not in state:
        async with AmplifierSession(config=CONFIG2) as session:
            state["stage2"] = await session.execute(...)
        save_state(state)  # ALWAYS checkpoint

    return state
```

**Why**: Resumability, debuggability, iteration support.

### Practice: Code Decides Flow, Not AI

**Good** (code decides):
```python
score = await evaluate(creation)
if score < threshold:
    # Code decides to iterate
    creation = await recreate_with_feedback(score)
```

**Bad** (AI decides):
```python
# Asking AI to decide
prompt = "If quality is low, regenerate. Otherwise continue."
```

**Why**: Code is deterministic and debuggable. AI decisions are unpredictable.

### Practice: Isolate Failures

```python
results = {}
errors = {}

# Stage 1
try:
    async with AmplifierSession(config=CONFIG1) as session:
        results["stage1"] = await session.execute(...)
except Exception as e:
    errors["stage1"] = str(e)
    # Continue - maybe later stages can work

# Stage 2 (even if stage 1 failed)
try:
    async with AmplifierSession(config=CONFIG2) as session:
        results["stage2"] = await session.execute(...)
except Exception as e:
    errors["stage2"] = str(e)

return {"status": "partial" if errors else "success", "results": results, "errors": errors}
```

**Why**: One stage failing shouldn't kill the whole pipeline. Partial results better than nothing.

### Practice: Human-in-Loop at Strategic Points

Add human input where human judgment is valuable:

**Strategic decision points**:
```python
# AI proposes plan
plan = await plan_approach(task)

# Human validates before expensive execution
print(f"Proposed Plan:\n{plan}")
if input("Approve? (yes/no): ") != "yes":
    return {"status": "rejected", "plan": plan}

# Proceed with approved plan
result = await execute(plan)
```

**Not for routine decisions**:
```python
# Bad: Asking human about routine things
if input("Analyze file1? (yes/no): ") == "yes":  # Don't do this!
    analysis = await analyze(file1)
```

**Why**: Humans good at strategy, AI good at execution. Use each for what they do best.

## Temperature Tuning Strategies

### Strategy: Match Temperature to Cognitive Role

| Role | Temp | When to Use | Example Tasks |
|------|------|-------------|---------------|
| **Analytical** | 0.1-0.3 | Need precision, consistency | Extract structure, classify items, identify patterns |
| **Empathetic** | 0.4-0.6 | Need perspective, nuance | Simulate users, understand motivations |
| **Creative** | 0.6-0.8 | Need diversity, exploration | Generate content, brainstorm ideas |
| **Evaluative** | 0.1-0.3 | Need consistency, objectivity | Score quality, judge correctness |
| **Synthesizing** | 0.3-0.5 | Need clarity, coherence | Combine information, summarize |

### Strategy: Iterate on Temperature

If output not what you expect:

**Too random/creative?** Lower temperature:
```python
"temperature": 0.3  # Was 0.5
```

**Too rigid/repetitive?** Raise temperature:
```python
"temperature": 0.6  # Was 0.3
```

**Temperature tuning is iterative** - test, observe, adjust.

### Strategy: Different Models for Different Roles

**Fast analytical tasks**: Haiku
```python
{"model": "claude-haiku-4", "temperature": 0.3}  # Fast, cheap, precise
```

**Complex creative tasks**: Opus
```python
{"model": "claude-opus-4", "temperature": 0.7}  # Sophisticated, expensive, creative
```

**Balanced tasks**: Sonnet
```python
{"model": "claude-sonnet-4", "temperature": 0.4}  # Good balance
```

## State Management Patterns

### Pattern: Simple Dict to JSON

```python
STATE_FILE = ".tool_state.json"

def save_state(state: dict):
    """Save after EVERY significant operation."""
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))

def load_state() -> dict:
    """Load if exists."""
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text())
    return {}
```

**Key**: Fixed filename (overwrite), simple dict, save frequently.

### Pattern: Incremental Progress Tracking

```python
state = load_state()
processed = state.get("processed", [])
results = state.get("results", [])

for item in items:
    # Resume check
    if str(item) in processed:
        continue

    # Process
    result = await process(item)
    results.append(result)
    processed.append(str(item))

    # Save after EVERY item
    save_state({"processed": processed, "results": results})
```

**Why**: If interrupted, resume from where you left off. No lost work.

### Pattern: Stage Checkpointing

```python
state = load_state()

# Each stage checks if done
if "analysis" not in state:
    state["analysis"] = await analyze(...)
    save_state(state)

if "creation" not in state:
    state["creation"] = await create(state["analysis"])
    save_state(state)

if "evaluation" not in state:
    state["evaluation"] = await evaluate(state["creation"])
    save_state(state)
```

**Why**: Long-running multi-stage tools can be interrupted and resumed.

## Learning from Debugging Sessions

### When Tools Fail: Diagnostic Process

1. **Identify which stage failed**
```bash
cat .tool_state.json  # See last successful stage
```

2. **Check config for that stage**
```python
# Is temperature appropriate for task?
# Is system prompt clear and focused?
# Is model appropriate?
```

3. **Test stage in isolation**
```python
# Run just that stage
result = await stage_that_failed(test_input)
print(result)
```

4. **Adjust and iterate**
- Lower temp if too random
- Raise temp if too rigid
- Clarify system prompt if unfocused
- Add validation if input issues

### Improving Based on Failures

**Document patterns that worked**:
```python
# After finding optimal config
# Add comment explaining why

ANALYZER_CONFIG = {
    "temperature": 0.3,  # Needs precision - 0.5 was too random, 0.3 is optimal
    ...
}
```

**Share improvements**:
- Update toolkit examples
- Contribute patterns
- Help others avoid same issues

## Building Tools with Status Tracking

### Pattern: Clear Progress Visibility

Users should always know what's happening:

```python
from amplifier_app_cli.toolkit import ProgressReporter

progress = ProgressReporter(len(items), "Processing items")

for item in items:
    print(f"Processing: {item.name}")  # Current item
    result = await process(item)
    progress.update()  # Shows overall progress

progress.finish()
```

**Why**: Long-running tools need progress feedback. Users shouldn't wonder if it's stuck.

### Pattern: Stage Status Reporting

For multi-stage tools:

```python
print("Stage 1/6: Analyzing content...")
async with AmplifierSession(config=ANALYZER_CONFIG) as session:
    analysis = await session.execute(...)
print("✓ Analysis complete")

print("Stage 2/6: Simulating learner experience...")
async with AmplifierSession(config=SIMULATOR_CONFIG) as session:
    simulation = await session.execute(...)
print("✓ Simulation complete")
```

**Why**: Users see progress through stages, know what's happening.

## The Configuration Sophistication Spectrum

### Guideline: Start Simple, Add Complexity Only When Needed

**90% of tools**: Level 1 (Fixed configs)
```python
# Hardcoded, predictable
ANALYZER_CONFIG = {"temperature": 0.3}
```

**8% of tools**: Level 2 (Code-modified)
```python
# Adaptive within bounds
config = create_config_for_task_type(task_type)
```

**2% of tools**: Level 3 (AI-generated)
```python
# Exploratory only
config = await generate_config(topic)  # Validate heavily!
```

### Guideline: Justify Each Level Jump

**Before moving from Level 1 to Level 2**, ask:
- Do requirements genuinely vary at runtime?
- Can't this be handled with multiple fixed configs?
- Is the added complexity worth it?

**Before moving from Level 2 to Level 3**, ask:
- Is this truly exploratory (low-stakes)?
- Can't this be handled with code-driven modification?
- Are safeguards comprehensive?

**Default**: Stay at Level 1. It handles almost everything.

## Decomposition Strategies

### Strategy: Identify Cognitive Stages

Break problems into cognitive subtasks:

**Example: Code Review Tool**

**Monolithic** (too big):
```
"Review this code and tell me what's wrong"
```

**Decomposed** (cognitive stages):
```
1. Structure Analysis (analytical, temp=0.3) - Extract code structure, identify patterns
2. Security Review (precision, temp=0.1) - Identify security issues with high confidence
3. Performance Analysis (analytical, temp=0.3) - Identify performance bottlenecks
4. Maintainability Review (evaluative, temp=0.2) - Assess readability, complexity
5. Synthesis (analytical, temp=0.3) - Combine findings into prioritized recommendations
```

Each stage is small, focused, optimized.

### Strategy: Extract Reusable Stages

Some stages are useful across multiple tools:

**Reusable stages**:
- "Extract concepts from documents" (analytical, temp=0.3)
- "Rank items by importance" (evaluative, temp=0.2)
- "Generate creative variations" (creative, temp=0.7)
- "Evaluate quality score" (evaluative, temp=0.2)

**Build once, reuse everywhere**:
```python
# In my_tool/common/concept_extractor.py
CONCEPT_EXTRACTOR_CONFIG = {...}

async def extract_concepts(document: str) -> list:
    async with AmplifierSession(config=CONCEPT_EXTRACTOR_CONFIG) as session:
        concepts = await session.execute(f"Extract concepts: {document}")
    return concepts

# Use in multiple tools
from my_tool.common.concept_extractor import extract_concepts

# Tool 1: Document synthesizer
concepts = await extract_concepts(doc)

# Tool 2: Knowledge graph builder
concepts = await extract_concepts(doc)
```

### Strategy: Compose Tools

**Build small tools, compose for complex workflows**:

```bash
# Small focused tools
extract-concepts docs/ > concepts.json
rank-concepts concepts.json > ranked.json
synthesize-report ranked.json > report.md
```

**Composition > monoliths**. Small tools that compose are more flexible than large tools that try to do everything.

## Shift Your Mindset

### From Single-Config to Multi-Config

**Old thinking**: "I need one config for my tool"

**New thinking**: "What cognitive stages does my tool need? Each gets its own optimized config."

### From Prompts to Orchestration

**Old thinking**: "I need a better prompt"

**New thinking**: "I need better orchestration - which config when, how to combine results"

Sophisticated tools aren't built with one amazing prompt. They're built with simple prompts and smart orchestration.

### From Linear to Iterative

**Old thinking**: "Tool runs once, produces output"

**New thinking**: "Tool runs stages, evaluates, iterates until quality threshold met"

**Quality loops** make tools more reliable:
```python
for iteration in range(max_iterations):
    output = await generate(input)
    score = await evaluate(output)
    if score > threshold:
        break
    input = add_feedback(input, score)
```

### From Everything to Specialized

**Old thinking**: "One tool should handle all cases"

**New thinking**: "Build specialized tools for specific use cases"

**Better**: Multiple focused tools that compose
```bash
research-papers topic.txt > papers.json
extract-insights papers.json > insights.json
generate-report insights.json > report.md
```

**Worse**: One monolithic tool trying to handle everything
```bash
mega-research-tool topic.txt --extract-papers --get-insights --make-report
```

## Key Takeaways

### Mindset Shifts

- **Context over capability** - Most issues are context/config optimization, not model limitations
- **Decomposition over monoliths** - Break into cognitive stages, optimize each
- **Multi-config over single-config** - Different tasks need different cognitive setups
- **Iteration over one-shot** - Quality loops make tools more reliable
- **Composition over features** - Small focused tools that compose > large monolithic tools

### Practical Strategies

1. **For multi-stage tools** - Checkpoint after every stage, enable resumability
2. **For quality-critical tools** - Add evaluation stage and quality loop
3. **For complex workflows** - Decompose into cognitive stages, optimize each config
4. **For varying requirements** - Use code-modified configs (Level 2), not AI-generated (Level 3)
5. **For reliability** - Isolate failures per stage, return partial results
6. **For maintainability** - Keep each config simple, complexity in orchestration

### The Amplifier Philosophy

**Build sophisticated tools from simple pieces:**
- Each config is simple (optimized for one cognitive role)
- Each stage is simple (one focused task)
- Orchestration code is simple (clear flow logic)
- Sophistication emerges from composition

**Don't give up. Lean in. Decompose.**

The challenges you overcome today (finding optimal configs, designing effective orchestration) become patterns you use tomorrow.

## Practical Examples

### Example: Research Tool Evolution

**Iteration 1** (too simple):
```python
# Single config, no stages
async with AmplifierSession(config=ONE_CONFIG) as session:
    result = await session.execute("Research topic and write report")
```

**Problems**: Shallow research, unfocused, no quality control

**Iteration 2** (decomposed):
```python
# Stage 1: Find sources (analytical, temp=0.3)
sources = await find_sources(topic)

# Stage 2: Extract insights (analytical, temp=0.3)
insights = await extract_insights(sources)

# Stage 3: Synthesize report (creative, temp=0.6)
report = await synthesize(insights)
```

**Better**: Deeper research, focused stages, but no quality control

**Iteration 3** (with quality loop):
```python
# ... stages 1-3 same ...

# Stage 4: Evaluate quality (evaluative, temp=0.2)
score = await evaluate_quality(report)

# Stage 5: Iterate if needed
if score < 0.8:
    feedback = f"Score: {score}. Issues: ..."
    report = await regenerate_with_feedback(insights, feedback)
```

**Best**: Deep research, focused stages, quality control, iteration

### Example: Tutorial Improvement Tool

See `toolkit/examples/tutorial_analyzer/` for complete implementation showing:
- 6 specialized configs (each optimized)
- Multi-stage orchestration (code manages flow)
- Human-in-loop (approve improvements)
- Quality loops (iterate if score low)
- State management (checkpoint every stage)

## Summary

**Best practices for building Amplifier tools:**

1. **Start with multi-config** - Different cognitive tasks need different configs
2. **Decompose into stages** - Small focused stages > large monolithic tasks
3. **Optimize per role** - Analytical (temp=0.3), Creative (temp=0.7), Evaluative (temp=0.2)
4. **Orchestrate with code** - Flow, state, decisions in code, not AI
5. **Checkpoint progress** - Save after every significant stage
6. **Add quality loops** - Evaluate → iterate until threshold met
7. **Isolate failures** - One stage failing shouldn't kill pipeline
8. **Start simple** - Level 1 (fixed configs) for 90% of tools
9. **Study examples** - Learn from tutorial_analyzer
10. **Iterate and improve** - Test, observe, adjust, repeat

**Philosophy alignment:**
- **Mechanism not policy** - Kernel provides capabilities, tools make decisions
- **Policy at edges** - Tools decide all configs (multiple policies!)
- **Ruthless simplicity** - Each piece simple, sophistication from composition
- **Context over capability** - Optimize context (configs) before assuming capability limits

**Remember**: Building great AI tools is about designing effective thinking processes and optimizing cognitive setups. Master the art of decomposition, configuration, and orchestration.

**Next steps**:
1. Study `toolkit/examples/tutorial_analyzer/` - See all practices in action
2. Read `toolkit/METACOGNITIVE_RECIPES.md` - Understand patterns deeply
3. Read `toolkit/HOW_TO_CREATE_YOUR_OWN.md` - Build your own tool
4. Start simple, iterate, learn from each tool you build

**The tools you build today teach you the patterns you use tomorrow.**
