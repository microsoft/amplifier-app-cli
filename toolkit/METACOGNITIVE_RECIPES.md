# Metacognitive Recipes: Multi-Config Thinking Patterns

## What Are Metacognitive Recipes?

A **metacognitive recipe** is a structured thinking process encoded as code that orchestrates multiple LLM sessions, each optimized for a specific cognitive role. Instead of one monolithic AI session trying to do everything, you create specialized "thinkers" and code that decides which thinker to use when.

**Think of it like a kitchen**:

- **Bad approach**: One cook does everything at once (prep, cook, plate, critique, adjust) - rushed, unfocused
- **Good approach**: Multiple specialists (prep chef, line cook, sous chef, head chef) each focused on their role - coordinated, optimized

**In AI tools**:

- **Bad approach**: One LLM session with one config trying to analyze, create, evaluate, and iterate
- **Good approach**: Multiple sessions with specialized configs - analyzer (temp=0.3), creator (temp=0.7), evaluator (temp=0.2) - orchestrated by code

## Why Multi-Config?

**Different cognitive tasks need different cognitive setups.**

### The Temperature Problem

One temperature can't optimize for all thinking modes:

- **Analytical thinking** (temp=0.2-0.3): Breaking down structure, classifying, extracting patterns - needs low temperature for precision
- **Creative thinking** (temp=0.6-0.8): Generating novel content, exploring possibilities - needs high temperature for diversity
- **Evaluative thinking** (temp=0.1-0.2): Judging quality, scoring, critiquing - needs very low temperature for consistency

With one config, you compromise. With multiple configs, you optimize each.

### The Attention Problem

Large prompts with many instructions dilute attention:

- Model tries to do everything at once
- Critical instructions get lost in context
- Failures cascade (one mistake breaks everything)

With multiple configs:

- Each session has one focused job
- Clear instructions optimized for that job
- Failures isolated to one stage

### The Context Problem

Everything must fit in one context window:

- Limited by token limits
- Can't process large multi-stage workflows
- No checkpointing between stages

With multiple configs:

- Each stage starts with fresh context
- Unlimited total context (spread across sessions)
- Checkpoint after every stage (resumability)

## The Multi-Config Pattern

### Basic Structure

```python
from amplifier_core import AmplifierSession

# Define multiple specialized configs
CONFIG_ANALYZER = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,  # Analytical precision
            "system_prompt": "You are an expert content analyzer."
        }
    }],
}

CONFIG_CREATOR = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-opus-4-1",
            "temperature": 0.7,  # Creative exploration
            "system_prompt": "You are a creative content generator."
        }
    }],
}

CONFIG_EVALUATOR = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.2,  # Evaluative consistency
            "system_prompt": "You are a quality evaluator."
        }
    }],
}

# Code orchestrates the recipe
async def metacognitive_pipeline(input_data):
    # Stage 1: Analyze (analytical config)
    async with AmplifierSession(config=CONFIG_ANALYZER) as session:
        analysis = await session.execute(f"Analyze: {input_data}")

    # Stage 2: Create (creative config)
    async with AmplifierSession(config=CONFIG_CREATOR) as session:
        creation = await session.execute(f"Create from analysis: {analysis}")

    # Stage 3: Evaluate (evaluative config)
    async with AmplifierSession(config=CONFIG_EVALUATOR) as session:
        evaluation = await session.execute(
            f"Evaluate this creation: {creation}"
        )

    # Code makes decision based on evaluation
    if evaluation["score"] < threshold:
        # Iterate with feedback...
        pass

    return creation
```

### Key Components

**1. Specialized Configs** - Each optimized for its cognitive role

- Model choice (Haiku for fast analysis, Opus for complex creation)
- Temperature (low for precision, high for creativity)
- System prompt (focused instructions for specific task)
- Orchestrator (basic for atomic tasks, streaming for long-form)

**2. Code Orchestration** - Manages the thinking process

- Decides which config to use when
- Manages state across stages
- Controls flow (loops, conditionals, jumps)
- Determines when human input needed

**3. State Management** - Tracks progress

- Checkpoints after each stage
- Enables resumability if interrupted
- Passes relevant context between stages
- Maintains history for iteration

## Configuration Sophistication Spectrum

There are three levels of configuration sophistication. Start with Level 1 (90% of tools). Only advance when you need adaptability.

### Level 1: Fixed Configs (Foundation - 90% of tools)

**What**: Hardcoded CONFIG constants defined at tool creation time.

**Example**:

```python
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,
            "system_prompt": "You are an expert tutorial analyzer."
        }
    }]
}

# Use everywhere the same way
async with AmplifierSession(config=ANALYZER_CONFIG) as session:
    result = await session.execute(prompt)
```

**When to use**:

- Tool has well-defined, unchanging needs ✅
- Predictability > adaptability ✅
- Security is critical ✅
- Most production tools ✅

**Benefits**:

- **Predictable**: Same config every time, no surprises
- **Reviewable**: Can audit exactly what will happen
- **Safe**: No dynamic behavior that could go wrong
- **Simple**: Define once, use everywhere

**Risk**: Low - Configuration is static and auditable

**Power**: Moderate - Multiple specialized configs can still handle complex workflows

**Philosophy alignment**: ✅ Perfect - Tool makes policy decision once (config values), kernel executes

---

### Level 2: Code-Modified Configs (Intermediate - 8% of tools)

**What**: Base template configs that code modifies based on runtime data or conditions.

**Pattern A: Code-Driven Modification** (Deterministic):

```python
# Base template
BASE_RESEARCHER_CONFIG = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{"module": "provider-anthropic", "config": {}}],
    "tools": []  # Code adds what's needed
}

def create_researcher_config(task_type: str, needs_web: bool, needs_code: bool) -> dict:
    """Code decides configuration based on task requirements."""
    config = copy.deepcopy(BASE_RESEARCHER_CONFIG)

    # Deterministic decisions based on task analysis
    if needs_web:
        config["tools"].append({
            "module": "tool-web-search",
            "source": "git+https://github.com/..."
        })

    if needs_code:
        config["tools"].append({
            "module": "tool-code-exec",
            "source": "git+https://github.com/..."
        })

    # Adjust model based on task type
    if task_type == "deep_research":
        config["providers"][0]["config"]["model"] = "claude-opus-4-1"
        config["providers"][0]["config"]["temperature"] = 0.3
    else:
        config["providers"][0]["config"]["model"] = "claude-sonnet-4-5"
        config["providers"][0]["config"]["temperature"] = 0.5

    return config

# Use in tool
async def research(topic: str, task_type: str):
    # Analyze what's needed
    needs_web = "web" in topic.lower() or "current" in topic.lower()
    needs_code = "code" in topic.lower() or "programming" in topic.lower()

    # Code builds config deterministically
    config = create_researcher_config(task_type, needs_web, needs_code)

    async with AmplifierSession(config=config) as session:
        result = await session.execute(f"Research: {topic}")
    return result
```

**Pattern B: AI-Suggested, Code-Applied** (Hybrid):

```python
# Meta-planner analyzes task and suggests needs
META_PLANNER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "config": {"model": "claude-sonnet-4-5", "temperature": 0.2}  # Analytical
    }]
}

async def create_adaptive_config(task_description: str) -> dict:
    """AI analyzes task, code builds config from analysis."""

    # Step 1: AI analyzes task needs
    async with AmplifierSession(config=META_PLANNER_CONFIG) as session:
        prompt = f"""Analyze this task and determine requirements:

        {task_description}

        Return JSON:
        - needs_web_search: boolean
        - needs_code_execution: boolean
        - complexity_level: "simple" | "moderate" | "complex"
        - optimal_model: "haiku" | "sonnet" | "opus"
        """
        analysis = await session.execute(prompt)

    # Step 2: Code interprets AI's analysis (not blind trust!)
    needs = parse_json(analysis)

    # Step 3: Code builds config based on AI's suggestions
    # CODE is in control - AI only suggests, code decides
    config = BASE_RESEARCHER_CONFIG.copy()

    if needs.get("needs_web_search"):
        config["tools"].append(WEB_SEARCH_TOOL)

    if needs.get("needs_code_execution"):
        # Add validation: only if task is code-related
        if "code" in task_description.lower():
            config["tools"].append(CODE_EXEC_TOOL)

    # AI suggests model, code validates and applies
    model_map = {
        "haiku": "claude-haiku-4",
        "sonnet": "claude-sonnet-4-5",
        "opus": "claude-opus-4-1"
    }
    suggested_model = needs.get("optimal_model", "sonnet")
    config["providers"][0]["config"]["model"] = model_map.get(suggested_model, "claude-sonnet-4-5")

    return config
```

**When to use**:

- Task requirements vary at runtime ✅
- Need adaptability but maintain safety ✅
- Hybrid AI-human decision making ✅
- Advanced tools with bounded flexibility ✅

**When NOT to use**:

- Requirements are static ❌ (use Level 1)
- Need maximum predictability ❌ (use Level 1)
- Security-critical operations ❌ (use Level 1)

**Benefits**:

- **Adaptive**: Responds to varying task requirements
- **Safe**: Code controls final decisions with validation
- **Bounded**: Limited to predefined options
- **Hybrid**: AI suggests, code decides

**Risk**: Moderate - Code validates AI suggestions and maintains boundaries

**Power**: High - Adaptable to varying needs while staying safe

**Philosophy alignment**: ✅ Good - Policy at edges (code makes final decisions), mechanism in kernel

---

### Level 3: AI-Generated Configs (Advanced - 2% of tools, EXPLORATORY ONLY)

**What**: AI creates entire config from scratch for low-stakes exploration.

**⚠️ WARNING**: Use ONLY for exploration. NOT for production, security-sensitive tasks, or well-understood domains.

**Example**:

```python
# Meta-config for AI that understands mount plans
META_CONFIG_GENERATOR = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "config": {
            "model": "claude-opus-4-1",  # Needs sophisticated understanding
            "temperature": 0.2
        }
    }],
    "context": "context-advanced",  # Needs mount plan schema knowledge
}

async def explore_topic(topic: str, available_modules: dict) -> dict:
    """Generate config for exploratory analysis - LOW STAKES ONLY."""

    prompt = f"""Generate a mount plan configuration for exploring this topic:

    TOPIC: {topic}

    AVAILABLE MODULES:
    {format_available_modules(available_modules)}

    MOUNT PLAN SCHEMA:
    {{
        "session": {{
            "orchestrator": "loop-basic" | "loop-streaming",
            "context": "context-simple" | "context-advanced"
        }},
        "providers": [{{
            "module": "provider-anthropic",
            "source": "git+...",
            "config": {{
                "model": "claude-haiku-4" | "claude-sonnet-4-5" | "claude-opus-4-1",
                "temperature": 0.0-1.0
            }}
        }}],
        "tools": [{{ "module": "...", "source": "..." }}]
    }}

    Choose appropriate modules and settings for exploring this topic.
    Return ONLY valid JSON matching schema above.
    """

    async with AmplifierSession(config=META_CONFIG_GENERATOR) as session:
        config_json = await session.execute(prompt)

    # CRITICAL: Validate before using
    generated_config = parse_json(config_json)
    validate_mount_plan_schema(generated_config)
    validate_module_sources(generated_config)
    validate_no_dangerous_tools(generated_config)

    # Use for LOW-STAKES exploration
    async with AmplifierSession(config=generated_config) as explore_session:
        result = await explore_session.execute(f"Explore: {topic}")

    # Validate RESULTS before using in production
    if is_valuable(result):
        return result
    else:
        return {"status": "not_valuable", "exploration": result}
```

**When to use** (RARE):

- Exploratory analysis (low stakes) ✅
- Research/prototyping tools ✅
- Learning what's possible ✅
- Expert users who understand risks ✅

**When NOT to use** (DEFAULT):

- Production tools ❌
- Security-sensitive tasks ❌
- Well-understood domains ❌
- Most real-world applications ❌

**Key Safeguards** (REQUIRED):

1. **Use for exploration only** - Low stakes, validate results not config
2. **Schema validation** - Config is well-formed
3. **Module whitelist** - Only approved modules
4. **Tool restrictions** - Dangerous tools disabled
5. **Result validation** - Outputs matter more than inputs
6. **Extensive logging** - Record all generated configs
7. **Fallback to safe defaults** - On error, use known-good config

**Risk**: HIGH

- AI could select inappropriate tools
- AI could misconfigure security boundaries
- AI could make poor model choices
- Hard to debug when things fail
- Unpredictable behavior

**Power**: MAXIMUM

- Fully adaptive to any task
- Can optimize for specific scenarios
- Can discover novel config combinations
- Enables exploration of unknown domains

**Philosophy alignment**: ⚠️ CAUTION

- ✅ Still mechanism not policy (kernel unchanged)
- ⚠️ Policy at edges but edges are AI-controlled
- ⚠️ Violates "boring" if overused
- ✅ Can be safe if properly validated and used for exploration only

---

## Configuration Maturity Decision Framework

Use this framework to choose the right level:

```
┌─────────────────────────────────────────────────────────────┐
│ Are task requirements known and unchanging?                  │
│   YES → Level 1 (Fixed configs)                             │
│   NO  → Continue...                                          │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│ Does task vary at runtime within bounded options?           │
│   YES → Level 2 (Code-modified configs)                     │
│   NO  → Continue...                                          │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│ Is this low-stakes exploration in unknown domain?           │
│   YES → Level 3 (AI-generated, with safeguards)             │
│   NO  → Back to Level 1 (prefer predictability)             │
└─────────────────────────────────────────────────────────────┘
```

**Default**: Start with Level 1. 90% of tools never need more.

## Complete Example: tutorial_analyzer

The toolkit includes `tutorial_analyzer` as the pedagogical exemplar. It demonstrates:

- 6 specialized configs (analyzer, learner_simulator, diagnostician, improver, critic, synthesizer)
- Multi-stage orchestration
- Human-in-loop at strategic points
- Evaluative loops with quality thresholds
- Complex flow control

### The Six Configs

```python
# 1. Analyzer - Extract structure (analytical, temp=0.3)
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,
            "system_prompt": "You are an expert tutorial content analyzer."
        }
    }]
}

# 2. Learner Simulator - Simulate learner (empathetic, temp=0.5)
LEARNER_SIMULATOR_CONFIG = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-opus-4-1",
            "temperature": 0.5,
            "system_prompt": "You are a learner encountering this tutorial."
        }
    }]
}

# 3. Diagnostician - Identify issues (precision, temp=0.1)
DIAGNOSTICIAN_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.1,
            "system_prompt": "You are a pedagogy expert identifying issues."
        }
    }]
}

# 4. Improver - Generate fixes (creative, temp=0.7)
IMPROVER_CONFIG = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-opus-4-1",
            "temperature": 0.7,
            "system_prompt": "You are a tutorial improvement specialist."
        }
    }]
}

# 5. Critic - Evaluate quality (evaluative, temp=0.2)
CRITIC_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.2,
            "system_prompt": "You are a pedagogy critic evaluating improvements."
        }
    }]
}

# 6. Synthesizer - Final recommendations (analytical, temp=0.3)
SYNTHESIZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,
            "system_prompt": "You synthesize evaluation results into recommendations."
        }
    }]
}
```

### The Orchestration

```python
async def evolve_tutorial(tutorial_path: Path):
    """Multi-stage metacognitive recipe."""

    state = load_state()
    content = tutorial_path.read_text()

    # Stage 1: Analyze (autonomous)
    if "analysis" not in state:
        async with AmplifierSession(config=ANALYZER_CONFIG) as session:
            state["analysis"] = await session.execute(f"Analyze: {content}")
        save_state(state)  # Checkpoint

    # Stage 2: Simulate learner (autonomous)
    if "learner_experience" not in state:
        async with AmplifierSession(config=LEARNER_SIMULATOR_CONFIG) as session:
            state["learner_experience"] = await session.execute(
                f"Simulate learner:\nAnalysis: {state['analysis']}\nContent: {content}"
            )
        save_state(state)

    # Stage 3: Diagnose issues (autonomous)
    if "diagnosis" not in state:
        async with AmplifierSession(config=DIAGNOSTICIAN_CONFIG) as session:
            state["diagnosis"] = await session.execute(
                f"Diagnose:\nLearner: {state['learner_experience']}\nAnalysis: {state['analysis']}"
            )
        save_state(state)

    # Stage 4: Generate improvements (autonomous)
    if "improvements" not in state:
        async with AmplifierSession(config=IMPROVER_CONFIG) as session:
            state["improvements"] = await session.execute(
                f"Generate improvements:\nDiagnosis: {state['diagnosis']}"
            )
        save_state(state)

    # Stage 5: HUMAN REVIEW (strategic decision point)
    if "human_feedback" not in state:
        print(f"Proposed Improvements:\n{state['improvements']}\n")
        approval = input("Approve improvements? (yes/no/modify): ")

        if approval == "modify":
            modifications = input("What modifications? ")
            state["improvements"] = f"{state['improvements']}\n\nModifications: {modifications}"

        state["human_feedback"] = approval
        save_state(state)

    # Stage 6: Evaluate (autonomous)
    if "critique" not in state:
        async with AmplifierSession(config=CRITIC_CONFIG) as session:
            state["critique"] = await session.execute(
                f"Evaluate:\nImprovements: {state['improvements']}\nOriginal diagnosis: {state['diagnosis']}"
            )
        save_state(state)

    # Stage 7: Synthesize (autonomous)
    if "synthesis" not in state:
        async with AmplifierSession(config=SYNTHESIZER_CONFIG) as session:
            state["synthesis"] = await session.execute(
                f"Synthesize:\nCritique: {state['critique']}\nImprovements: {state['improvements']}"
            )
        save_state(state)

    # Code makes decision: iterate or finalize
    score = float(state["synthesis"]["quality_score"])
    if score < 0.8 and state.get("iterations", 0) < 3:
        # Loop back with feedback
        state["iterations"] = state.get("iterations", 0) + 1
        del state["improvements"]  # Re-generate with new context
        save_state(state)
        return await evolve_tutorial(tutorial_path)  # Recursive

    return state["synthesis"]
```

**Key teachings**:

- **6 specialized configs** - Each optimized for its cognitive role
- **Code orchestrates** - Decides flow, manages state, controls loops
- **Checkpointing** - Save after every stage for resumability
- **Human-in-loop** - Strategic decision point (approve improvements)
- **Evaluative loops** - Re-generate if quality threshold not met
- **State management** - Track progress, enable iteration

See `toolkit/examples/tutorial_analyzer/README.md` for complete documentation.

## Advanced Flow Control Patterns

Metacognitive recipes support arbitrarily complex flow control. Code can implement sophisticated thinking patterns.

### Pattern: Nested Evaluation Loops

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
            async with AmplifierSession(config=DEEP_ANALYZER) as session:
                deep_issues = await session.execute(f"Deep analyze: {section}")
            section = await apply_fixes(section, deep_issues)

    # Outer quality check
    overall_score = await evaluate_tutorial(tutorial)
    if overall_score > target:
        break  # Success - exit early
```

### Pattern: Conditional Jump with Context (Goto-Style)

```python
state = {
    "stage": "ANALYZE",
    "context": {},
    "return_to": None,  # For goto-style returns
}

while True:
    if state["stage"] == "ANALYZE":
        analysis = await analyze(tutorial)
        state["context"]["analysis"] = analysis

        # Conditional jump
        if analysis["has_code_examples"]:
            state["stage"] = "VERIFY_CODE"
            state["return_to"] = "DIAGNOSE"  # Remember where to return
        else:
            state["stage"] = "DIAGNOSE"

    elif state["stage"] == "VERIFY_CODE":
        code_issues = await verify_code_examples(
            tutorial,
            state["context"]["analysis"]  # Context from earlier
        )
        state["context"]["code_issues"] = code_issues
        state["stage"] = state["return_to"]  # Goto DIAGNOSE

    elif state["stage"] == "DIAGNOSE":
        diagnosis = await diagnose(
            tutorial,
            analysis=state["context"]["analysis"],
            code_issues=state["context"].get("code_issues")  # May not exist
        )

        if diagnosis["severity"] == "critical":
            state["stage"] = "EMERGENCY_REWRITE"
        else:
            state["stage"] = "IMPROVE"
```

### Pattern: Multi-Path Convergence

```python
import asyncio

# Fork into parallel analyses
async with asyncio.TaskGroup() as tg:
    clarity_task = tg.create_task(
        evaluate_with_config(tutorial, CLARITY_EVALUATOR)
    )
    technical_task = tg.create_task(
        evaluate_with_config(tutorial, TECHNICAL_EVALUATOR)
    )
    pedagogical_task = tg.create_task(
        evaluate_with_config(tutorial, PEDAGOGICAL_EVALUATOR)
    )

# Converge results
clarity_score = await clarity_task
technical_score = await technical_task
pedagogical_score = await pedagogical_task

# Complex decision based on which paths found issues
if clarity_score.has_issues and pedagogical_score.has_issues:
    # Both failed - fundamental restructure
    state["stage"] = "FUNDAMENTAL_RESTRUCTURE"
    state["context"]["combined_issues"] = merge_issues([clarity_score, pedagogical_score])
elif technical_score.has_issues:
    # Only technical - targeted fix
    state["stage"] = "FIX_TECHNICAL"
    state["return_to"] = "FINAL_CHECK"
else:
    # All passed
    state["stage"] = "FINALIZE"
```

## Best Practices

### 1. Start Simple (Level 1)

Begin with fixed configs. Only add complexity when you need it.

**Good progression**:

1. Start: 2-3 fixed configs (analyzer, creator)
2. If working: Done! Ship it.
3. If needs vary: Add Level 2 code-modification
4. If exploring: Consider Level 3 with safeguards

### 2. Optimize Temperature for Role

| Role             | Temperature | Why                           |
| ---------------- | ----------- | ----------------------------- |
| **Analytical**   | 0.1-0.3     | Need precision, consistency   |
| **Empathetic**   | 0.4-0.6     | Need perspective, nuance      |
| **Creative**     | 0.6-0.8     | Need diversity, exploration   |
| **Evaluative**   | 0.1-0.3     | Need consistency, objectivity |
| **Synthesizing** | 0.3-0.5     | Need clarity, coherence       |

### 3. Checkpoint After Every Stage

```python
# After EVERY stage
save_state(state)

# Why:
# - Resumability (can restart if interrupted)
# - Debugging (see state at each point)
# - Iteration (can retry stages)
```

### 4. Decide Flow in Code, Not AI

**Good** (code decides):

```python
if score < threshold:
    # Re-generate with feedback
    await regenerate_with_feedback(score)
```

**Bad** (AI decides):

```python
# Asking AI to decide flow
prompt = "If quality is low, regenerate. Otherwise continue."
```

Code is better at: flow control, thresholds, routing
AI is better at: understanding content, generating text

### 5. Isolate Failures

```python
# Each stage isolated
try:
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        analysis = await session.execute(...)
    state["analysis"] = analysis
except Exception as e:
    # Log but continue - maybe next stages can work
    state["errors"]["analysis"] = str(e)
save_state(state)
```

One stage failing shouldn't kill the whole pipeline.

## Common Mistakes

### Mistake 1: One Config to Rule Them All

**Bad**:

```python
# Trying to do everything with one config
ONE_CONFIG = {"temperature": 0.5}  # Compromise

async with AmplifierSession(config=ONE_CONFIG) as session:
    # Analyze (needs low temp)
    analysis = await session.execute("Analyze...")

    # Create (needs high temp)
    creation = await session.execute("Create...")

    # Evaluate (needs low temp)
    evaluation = await session.execute("Evaluate...")
```

**Why bad**: Compromise temperature doesn't optimize for any task

**Good**: Multiple configs, each optimized

### Mistake 2: Overusing Level 3

**Bad**:

```python
# Generating configs for well-understood task
config = await generate_config_for_blog_writing()  # Why?!
```

**Why bad**: Blog writing is well-understood, doesn't need exploration

**Good**: Fixed config for well-understood tasks

### Mistake 3: Not Checkpointing

**Bad**:

```python
# Run all stages, save at end
stage1 = await do_stage1()
stage2 = await do_stage2(stage1)
stage3 = await do_stage3(stage2)
save_state({"results": stage3})  # Too late!
```

**Why bad**: If stage2 fails, lost stage1 work. Not resumable.

**Good**: Save after EVERY stage

### Mistake 4: AI Decides Flow

**Bad**:

```python
prompt = """Analyze this content.
If quality is low, improve it.
If quality is high, move to next stage.
"""
```

**Why bad**: AI makes flow decisions, not code. Unpredictable.

**Good**: Code decides flow based on AI analysis

## Summary

**Metacognitive recipes** = Code-orchestrated multi-config thinking

**Key insights**:

1. **Multiple configs** - Each optimized for its cognitive role
2. **Code orchestrates** - Flow, state, decisions
3. **Configuration spectrum** - Level 1 (fixed), 2 (code-modified), 3 (AI-generated)
4. **Start simple** - Level 1 for 90% of tools
5. **Advanced when needed** - Complex flow control available

**Philosophy**:

- **Mechanism not policy**: Kernel unchanged, configs = policy decisions
- **Policy at edges**: Tools decide all configs
- **Ruthless simplicity**: Start Level 1, add complexity only when needed
- **Code for structure, AI for intelligence**: Each does what it does best

**Next steps**:

1. Study `toolkit/examples/tutorial_analyzer/` - Complete exemplar
2. Read `toolkit/HOW_TO_CREATE_YOUR_OWN.md` - Step-by-step guide
3. Read `toolkit/BEST_PRACTICES.md` - Strategic guidance
4. Start with Level 1 fixed configs - Don't over-engineer!

**Remember**: Sophisticated tools emerge from simple, well-composed pieces. Start simple, add complexity only when you need it.
