# Toolkit Philosophy: Mechanism vs Policy, Multi-Config at Edges

## Core Principle: Standalone Tools ARE the Edges

Standalone tools built with this toolkit are **policy-making edges** in the Amplifier ecosystem. They make all decisions about configuration, flow, and behavior. The kernel (amplifier-core) provides only mechanisms.

**The division of responsibility**:

- **Kernel (amplifier-core)**: Provides `AmplifierSession` - the mechanism to execute with any config
- **Tools (this toolkit)**: Make ALL policy decisions - which configs, when to use them, how to orchestrate

## Why Multi-Config?

### The Single-Config Limitation

**Attempt**: One config tries to handle all cognitive tasks

```python
GENERIC_CONFIG = {
    "providers": [{
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.5,  # Compromise
            "system_prompt": "You are a helpful assistant."  # Generic
        }
    }]
}

# Use for everything
async with AmplifierSession(config=GENERIC_CONFIG) as session:
    analysis = await session.execute("Analyze this...")
    creation = await session.execute("Create from this...")
    evaluation = await session.execute("Evaluate this...")
```

**Problems**:

- **Temperature compromise**: Can't optimize for precision (analysis) AND creativity (generation)
- **Attention dilution**: Generic prompt not optimized for any specific task
- **Context pollution**: All tasks share one session, context accumulates
- **Failure cascade**: One mistake affects all subsequent tasks

### The Multi-Config Solution

**Approach**: Specialized config per cognitive subtask

```python
ANALYZER_CONFIG = {
    "providers": [{
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,  # Optimized for precision
            "system_prompt": "You are an expert content analyzer..."  # Focused
        }
    }]
}

CREATOR_CONFIG = {
    "providers": [{
        "config": {
            "model": "claude-opus-4-1",
            "temperature": 0.7,  # Optimized for creativity
            "system_prompt": "You are a creative content generator..."  # Focused
        }
    }]
}

EVALUATOR_CONFIG = {
    "providers": [{
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.2,  # Optimized for consistency
            "system_prompt": "You are a quality evaluator..."  # Focused
        }
    }]
}

# Code orchestrates
async def sophisticated_tool():
    # Each session optimized for its task
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        analysis = await session.execute(...)  # Precise analysis

    async with AmplifierSession(config=CREATOR_CONFIG) as session:
        creation = await session.execute(...)  # Creative generation

    async with AmplifierSession(config=EVALUATOR_CONFIG) as session:
        evaluation = await session.execute(...)  # Consistent evaluation

    return creation
```

**Benefits**:

- **Optimized temperatures**: Each task gets its ideal temperature
- **Focused attention**: Each session has one clear job
- **Fresh context**: Each stage starts clean
- **Isolated failures**: One stage failing doesn't kill the rest

## Alignment with Kernel Philosophy

### Mechanism Not Policy

**Kernel provides mechanism**:

```python
# AmplifierSession: mechanism to execute with ANY config
async with AmplifierSession(config=ANY_CONFIG) as session:
    response = await session.execute(prompt)
```

**Tool makes policy decisions**:

```python
# Tool decides:
# - Which model (haiku, sonnet, opus)
# - What temperature (0.1, 0.5, 0.8)
# - Which orchestrator (basic, streaming)
# - What system prompt
# - When to use which config
# - How to combine results
# - When to loop or iterate

ANALYZER_CONFIG = {"temperature": 0.3}   # Policy decision
CREATOR_CONFIG = {"temperature": 0.7}     # Policy decision
```

The kernel doesn't care. It just executes. **This is correct.**

### Policy at Edges

Standalone tools **ARE** the edges. They make all policy decisions:

```python
# Tool decides everything about its behavior
CONFIGS = {
    "analyzer": {"temperature": 0.3, "model": "claude-sonnet-4-5"},
    "creator": {"temperature": 0.7, "model": "claude-opus-4-1"},
    "evaluator": {"temperature": 0.2, "model": "claude-sonnet-4-5"},
}

# Code decides flow
if analysis_result.needs_research:
    # Jump to research flow with different config
    config = CONFIGS["researcher"]
else:
    # Skip to creation
    config = CONFIGS["creator"]

async with AmplifierSession(config=config) as session:
    # Kernel executes policy decided by tool
    result = await session.execute(...)
```

No one else decides for you. **This is correct.**

### Ruthless Simplicity

**Each piece stays simple**:

- Each config: Simple dict with a few optimized values
- Each stage: One focused task with one config
- Code orchestration: Clear flow logic
- State management: Simple dict to JSON

**Sophistication emerges from composition**:

- Multiple simple configs
- Clear orchestration logic
- Well-defined stages

**No unnecessary abstraction**:

- No session wrappers (use AmplifierSession directly)
- No state frameworks (simple dict per tool)
- No config builders (just dict literals)

### Small, Stable, Boring (Kernel)

**Kernel stays unchanged**:

- `AmplifierSession` interface same
- Load modules, execute prompts
- No new APIs needed

**Tools innovate at edges**:

- New metacognitive recipes
- New config combinations
- New orchestration patterns
- New flow control strategies

The center stays still so the edges can move fast. **This is correct.**

## Build-Time vs Runtime Configuration

### Standalone Tools Make Build-Time Decisions

**CLI applications** (like `amplifier`) allow users to choose behavior at runtime:

```bash
# User chooses profile at runtime
amplifier run --profile dev "research AI safety"
amplifier run --profile production "research AI safety"
```

**Standalone tools** make policy decisions at **build time**:

```python
# Tool author decides configs when building tool
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "config": {"model": "claude-sonnet-4-5", "temperature": 0.3}
    }]
}  # Build-time decision - no runtime user choice
```

### Standalone Tools Are Opinionated

Each standalone tool is a **specific solution** to a **specific problem** with **specific choices**:

```python
# Tutorial evolver's build-time choices:
# - Use claude-sonnet-4-5 for analysis (not opus, not haiku)
# - Use temperature 0.3 for analysis (not 0.5, not 0.1)
# - Use 6 configs (not 3, not 10)
# - Simulate learner perspective (not skip it)
# - Require human approval for improvements (not autonomous)

# These are POLICY DECISIONS made by tool author
# Not runtime choices for users
```

Users who want different behavior create **different tools**. No dynamic configuration needed.

### Module Loading Works Directly

Standalone tools install amplifier modules as dependencies:

```toml
# pyproject.toml
[project]
dependencies = [
    "amplifier-core",
]

[project.optional-dependencies]
amplifier = [
    "amplifier-module-provider-anthropic @ git+https://github.com/...",
    "amplifier-module-loop-streaming @ git+https://github.com/...",
]
```

Modules discovered automatically via entry points. No profile loading or dynamic resolution needed.

## Configuration Sophistication Spectrum

Standalone tools can use three levels of configuration sophistication:

### Level 1: Fixed Configs (90% of tools)

**Policy decision at build time**:

```python
# Tool author decides these values when building tool
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/...",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,
            "system_prompt": "You are an expert analyzer."
        }
    }]
}

# Same config every time tool runs - predictable, safe
```

**Philosophy alignment**: ✅ Perfect

- Mechanism not policy: Kernel unchanged, tool makes policy once
- Policy at edges: Tool decides, kernel executes
- Ruthless simplicity: Define once, use everywhere
- Small, stable, boring: No dynamic behavior

### Level 2: Code-Modified Configs (8% of tools)

**Policy decision at runtime, bounded by code**:

```python
# Base template with policy boundaries
BASE_CONFIG = {
    "session": {"orchestrator": "loop-streaming"},
    "providers": [{"module": "provider-anthropic", "config": {}}],
    "tools": []  # Code decides which tools
}

def create_config(task_type: str) -> dict:
    """Code makes bounded policy decisions."""
    config = copy.deepcopy(BASE_CONFIG)

    # Deterministic decisions within predefined options
    if task_type == "deep_research":
        config["providers"][0]["config"] = {"model": "claude-opus-4-1", "temperature": 0.3}
        config["tools"].append(WEB_SEARCH_TOOL)
    else:
        config["providers"][0]["config"] = {"model": "claude-sonnet-4-5", "temperature": 0.5}

    return config
```

**Philosophy alignment**: ✅ Good

- Mechanism not policy: Kernel still unchanged
- Policy at edges: Tool's code makes runtime decisions
- Ruthless simplicity: Bounded options, deterministic logic
- Some complexity: Justified by need for adaptability

### Level 3: AI-Generated Configs (2% of tools, EXPLORATORY)

**Policy decision delegated to AI** (low-stakes exploration only):

```python
async def generate_config_for_exploration(topic: str) -> dict:
    """AI creates config for exploration."""

    # AI generates entire config
    async with AmplifierSession(config=META_PLANNER_CONFIG) as session:
        config_json = await session.execute(f"Generate config for exploring: {topic}")

    # CRITICAL: Code validates before using
    generated_config = parse_json(config_json)
    validate_schema(generated_config)
    validate_module_whitelist(generated_config)
    validate_no_dangerous_tools(generated_config)

    # Use for LOW-STAKES exploration only
    async with AmplifierSession(config=generated_config) as session:
        result = await session.execute(f"Explore: {topic}")

    return result
```

**Philosophy alignment**: ⚠️ CAUTION

- Mechanism not policy: ✅ Kernel still unchanged
- Policy at edges: ⚠️ AI controls policy (with code validation)
- Ruthless simplicity: ⚠️ Complex if overused
- Small, stable, boring: ⚠️ Unpredictable behavior

**Use ONLY for**:

- Exploratory research (low stakes)
- Unknown domains (learning)
- Prototyping (not production)

**NEVER for**:

- Production tools
- Security-sensitive tasks
- Well-understood domains

## When to Use Toolkit Utilities

### Use Toolkit For

**Structural operations**:

- File discovery: `discover_files(path, "**/*.md")`
- Progress reporting: `ProgressReporter(count, description)`
- Input validation: `validate_input_path(path, must_be_dir=True)`

**Why**: These are mechanism utilities. They don't make policy decisions. They just provide capabilities your code uses.

### Don't Use Toolkit For

**LLM operations** - Use amplifier-core directly:

- ❌ Don't wrap `AmplifierSession`
- ❌ Don't create LLM helper classes
- ❌ Don't parse LLM responses (amplifier-core handles it)
- ❌ Don't retry LLM calls (orchestrator handles it)

**State management** - Each tool owns its state:

- ❌ Don't create state manager classes
- ❌ Don't generalize state structure
- ✅ Each tool has its own simple state dict

**Why**: These are policy decisions. Each tool decides its own policies. Toolkit doesn't impose them.

## What NOT to Wrap from Amplifier-Core

### Never Wrap These

**AmplifierSession**:

```python
# WRONG: Wrapping the mechanism
class Helper:
    def __init__(self):
        self.session = AmplifierSession(...)

# RIGHT: Use directly
async with AmplifierSession(config=MY_CONFIG) as session:
    result = await session.execute(...)
```

**Providers**:

```python
# WRONG: Direct provider access
from amplifier_module_provider_anthropic import AnthropicProvider
provider = AnthropicProvider()

# RIGHT: Via AmplifierSession
async with AmplifierSession(config=config_with_provider) as session:
    result = await session.execute(...)
```

**Orchestrators**:

```python
# WRONG: Direct orchestrator use
from amplifier_module_loop_streaming import StreamingOrchestrator
orchestrator = StreamingOrchestrator()

# RIGHT: Specify in config, let kernel load
config = {"session": {"orchestrator": "loop-streaming"}}
async with AmplifierSession(config=config) as session:
    # Kernel loaded and wired orchestrator
    result = await session.execute(...)
```

### Why Not Wrap?

**Wrapping creates indirection**:

- Adds complexity without value
- Hides the kernel interface
- Makes debugging harder
- Violates "use mechanisms directly"

**Kernel interfaces are designed for direct use**:

- `AmplifierSession` is the public API
- It's already simple and usable
- Wrapping it makes it more complex, not simpler

## Multi-Config and Kernel Philosophy

### How Multi-Config Aligns

**Mechanism not policy** ✅:

- Kernel: `AmplifierSession.execute(config, prompt)` - pure mechanism
- Tools: Multiple CONFIG dicts - pure policy decisions
- Clean separation

**Policy at edges** ✅:

- Tools ARE the edges
- Tools decide all configs (which model, what temperature, which orchestrator)
- Multiple configs = multiple policy decisions at edges
- Kernel just executes

**Ruthless simplicity** ✅:

- Each config is simple (just a dict with a few values)
- Each stage is simple (one config, one task)
- Code orchestration is simple (clear flow logic)
- Sophistication emerges from composition

**Small, stable, boring (kernel)** ✅:

- Kernel unchanged
- No new APIs needed
- Just executing configs with prompts
- Boring is good

### How Multi-Config Enables Innovation

**Tools can innovate**:

- Try new config combinations
- Experiment with temperatures
- Test different models
- Explore new flow patterns

**Kernel stays stable**:

- Same `AmplifierSession` interface
- Same module loading
- Same event system
- No changes needed

**The center stays still so the edges can move fast.** Multi-config pattern exemplifies this.

## When to Use Multiple Configs

### Indicators You Need Multi-Config

Ask these questions:

**1. Do you have distinct cognitive tasks?**

- Analysis (precise) AND creation (creative)? → Multiple configs
- Evaluation (consistent) AND generation (diverse)? → Multiple configs

**2. Do tasks need different temperatures?**

- Some need precision (temp=0.1-0.3)? → Multiple configs
- Some need creativity (temp=0.6-0.8)? → Multiple configs

**3. Do you have multiple stages?**

- Extract → Synthesize → Evaluate? → Multiple configs
- Analyze → Plan → Implement → Review? → Multiple configs

**4. Do you need different models?**

- Fast analysis (Haiku) and deep creation (Opus)? → Multiple configs

### When Single Config Is Fine

**Indicators you're overthinking**:

- Tool does ONE thing → Single config probably fine
- All tasks similar (all analytical or all creative) → Single config probably fine
- No multi-stage pipeline → Single config probably fine
- No quality loops → Single config probably fine

**Example of appropriate single-config tool**:

```python
# Simple code formatter - one analytical task
FORMATTER_CONFIG = {"temperature": 0.2}

async def format_code(code: str):
    async with AmplifierSession(config=FORMATTER_CONFIG) as session:
        formatted = await session.execute(f"Format this code:\n\n{code}")
    return formatted
```

**No need for multi-config** - it's one focused analytical task.

## The Spectrum: When to Use Which Level

### Decision Framework

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

**Default**: Level 1 (Fixed configs) - 90% of tools stay here forever

### Philosophy Alignment by Level

**Level 1: Fixed Configs**

- Mechanism not policy: ✅ Perfect
- Policy at edges: ✅ Tool decides once
- Ruthless simplicity: ✅ Maximum
- Boring: ✅ Predictable

**Level 2: Code-Modified**

- Mechanism not policy: ✅ Good
- Policy at edges: ✅ Code at edge decides
- Ruthless simplicity: ⚠️ Some complexity
- Boring: ⚠️ Some dynamism

**Level 3: AI-Generated**

- Mechanism not policy: ✅ Kernel still unchanged
- Policy at edges: ⚠️ AI controls policy
- Ruthless simplicity: ⚠️ Complex if overused
- Boring: ❌ Unpredictable

**Recommendation**: Start Level 1. Stay Level 1 unless you have a specific need for adaptability.

## Common Misconceptions

### Misconception 1: "More configs = more complex"

**Reality**: More configs can mean LESS complexity

**Single config attempting everything**:

```python
# Complex: One config trying to handle all cases
prompt = """You will receive different types of tasks.
For analytical tasks, be precise.
For creative tasks, be diverse.
For evaluative tasks, be consistent.

Task: {task}
Type: {task_type}
"""

# Complexity in prompt engineering to handle all cases
```

**Multiple configs, each simple**:

```python
# Simple: Each config focused on one thing
ANALYZER_CONFIG = {"temperature": 0.3}
CREATOR_CONFIG = {"temperature": 0.7}
EVALUATOR_CONFIG = {"temperature": 0.2}

# Complexity in orchestration (which is code, which is easier)
```

### Misconception 2: "I need complex loading for multiple configs"

**Reality**: Multiple configs are just multiple dicts

```python
# Define all configs directly
CONFIGS = {
    "analyzer": {
        "session": {"orchestrator": "loop-basic"},
        "providers": [{"module": "provider-anthropic", "source": "git+...", "config": {"temperature": 0.3}}]
    },
    "creator": {
        "session": {"orchestrator": "loop-basic"},
        "providers": [{"module": "provider-anthropic", "source": "git+...", "config": {"temperature": 0.7}}]
    },
}

# Code decides which config when
config = CONFIGS["analyzer"]  # or CONFIGS["creator"], etc.
async with AmplifierSession(config=config) as session:
    result = await session.execute(...)
```

CLI apps use profiles for dynamic user choice. Standalone tools make build-time choices.

### Misconception 3: "Multi-config violates simplicity"

**Reality**: Multi-config ENABLES simplicity

**Each config stays simple** (focused, optimized)
**Each stage stays simple** (one task, one config)
**Code orchestration stays simple** (clear flow logic)

**Sophistication emerges from composition of simple pieces**

This is the essence of ruthless simplicity: simple pieces, clear composition.

## Summary

**Toolkit philosophy**:

1. **Standalone tools are policy-making edges** - They decide all configs
2. **Multi-config pattern** - Each cognitive subtask gets optimized config
3. **Code orchestrates thinking** - Flow, state, decisions in code
4. **Use kernel directly** - AmplifierSession without wrappers
5. **Start simple** - Level 1 (fixed configs) for 90% of tools
6. **Add complexity only when needed** - Level 2/3 when justified

**Alignment with kernel philosophy**:

- ✅ Mechanism not policy (kernel provides capability, tools decide configs)
- ✅ Policy at edges (tools ARE edges, make ALL decisions)
- ✅ Ruthless simplicity (each piece simple, composition sophisticated)
- ✅ Small, stable, boring (kernel unchanged, tools innovate)

**Key insight**: The most sophisticated AI tools are built from the simplest pieces - specialized configs and clear orchestration logic. Start simple, compose thoughtfully.

**Next steps**:

1. Study `toolkit/examples/tutorial_analyzer/` - See multi-config in action
2. Read `toolkit/METACOGNITIVE_RECIPES.md` - Understand pattern deeply
3. Read `toolkit/HOW_TO_CREATE_YOUR_OWN.md` - Build your own tool
4. Start with Level 1 - Don't over-engineer!
