# Toolkit Examples

These examples demonstrate the correct pattern for building amplifier-dev CLI tools that use AI.

## Philosophy

**Core Principle**: Code for structure, amplifier-core for intelligence

- **Code handles**: loops, file I/O, state, coordination
- **Amplifier-core handles**: ALL LLM operations
- **Toolkit provides**: Structural utilities

**Critical**: Always use `AmplifierSession` directly. Never wrap it.

## Available Examples

### document_analyzer.py

**Purpose**: Analyzes markdown documents using AI

**What it demonstrates**:
- AmplifierSession direct use (no wrapper)
- Profile loading via ProfileManager
- Toolkit utilities (discover_files, ProgressReporter)
- Tool-specific state management (resumability)
- Incremental saves after each operation
- Graceful error handling

**Run it**:
```bash
python amplifier-app-cli/toolkit/examples/document_analyzer.py ./docs

# With specific profile
python amplifier-app-cli/toolkit/examples/document_analyzer.py ./docs --profile production

# Verbose logging
python amplifier-app-cli/toolkit/examples/document_analyzer.py ./docs --verbose
```

**Expected output**:
```
Found 15 files to analyze
Processing [1/15]: README.md
Processing [2/15]: CONTRIBUTING.md
...
✓ Processed 15 files
✓ Results saved to .document_analyzer_state.json
```

## The Pattern Each Example Follows

```python
# 1. Load profile (app-layer)
manager = ProfileManager()
mount_plan = manager.get_profile_as_mount_plan(profile_name)

# 2. Create session (kernel - use directly!)
async with AmplifierSession(config=mount_plan) as session:

    # 3. Use toolkit for structure
    files = discover_files(input_dir, pattern)
    progress = ProgressReporter(len(files), description)

    # 4. Use amplifier-core for intelligence
    for file in files:
        response = await session.execute(prompt)
        save_result(file, response)
        progress.update()
```

## What Examples Show

### Correct Patterns

- ✅ AmplifierSession used directly (no wrapper)
- ✅ ProfileManager loads profiles
- ✅ Toolkit utilities for file/progress/validation
- ✅ Tool-specific state management (simple dict to JSON)
- ✅ Incremental saves (after each operation)
- ✅ Graceful error handling (partial results)

### Anti-Patterns Avoided

- ❌ No session wrappers
- ❌ No state management frameworks
- ❌ No LLM response parsing (amplifier-core handles it)
- ❌ No direct LLM API calls (always via amplifier-core)

## Creating Your Own Tool

1. **Start with template**:
   ```bash
   cp toolkit/templates/cli_tool_template.py my_tool.py
   ```

2. **Or copy an example**:
   ```bash
   cp toolkit/examples/document_analyzer.py my_custom_analyzer.py
   ```

3. **Customize**:
   - Update prompts for your use case
   - Modify state structure if needed
   - Add tool-specific logic

4. **Test**:
   ```bash
   python my_tool.py ./test_input
   ```

## State Management Pattern

Each example demonstrates tool-specific state:

```python
STATE_FILE = ".tool_name_state.json"

def save_state(processed: list[str], results: list[dict]):
    """Save after EVERY operation."""
    state = {
        "processed": processed,
        "results": results,
        "updated": datetime.now().isoformat()
    }
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))

def load_state() -> tuple[list[str], list[dict]]:
    """Load if exists."""
    if Path(STATE_FILE).exists():
        data = json.loads(Path(STATE_FILE).read_text())
        return data.get("processed", []), data.get("results", [])
    return [], []

# In loop:
processed, results = load_state()
for item in items:
    if str(item) in processed:
        continue  # Resume: skip already done

    result = await process(item)
    processed.append(str(item))
    results.append(result)
    save_state(processed, results)  # Incremental
```

**Pattern**: Fixed filename, overwrite on save, simple dict, resume check.

## Multi-Stage Pipeline Pattern

For tools with multiple AI stages:

```python
async def multi_stage_tool():
    state = load_state()

    async with AmplifierSession(config=mount_plan) as session:
        # Stage 1
        if "stage1" not in state:
            result1 = await session.execute(prompt1)
            state["stage1"] = result1
            save_state(state)  # Checkpoint

        # Stage 2
        if "stage2" not in state:
            result2 = await session.execute(f"{prompt2}: {state['stage1']}")
            state["stage2"] = result2
            save_state(state)  # Checkpoint

        return state["stage2"]
```

**Key**: Save checkpoints between stages for resumability.

## Getting Help

- **Example not working?** Check profile exists: `amplifier profile list`
- **Module errors?** Check profile's module sources are correct
- **Want to add example?** Follow the pattern above, submit PR

## Philosophy References

These examples embody:
- **Kernel Philosophy** - Use AmplifierSession directly, don't wrap
- **Implementation Philosophy** - Ruthless simplicity, no abstractions
- **Modular Design** - Clear contracts, regeneratable from spec

Study these examples to understand how to build tools correctly.
