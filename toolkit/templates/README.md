# Toolkit Templates

## Philosophy: Use Mechanisms, Don't Wrap Them

These templates demonstrate the correct pattern for building amplifier-dev CLI tools that use AI.

**Core Principle**: Always use amplifier-core directly - never wrap `AmplifierSession`.

## The Correct Pattern

```python
from amplifier_core import AmplifierSession
from amplifier_app_cli.profile_system import ProfileManager
from toolkit import discover_files, ProgressReporter

async def my_tool(input_dir: Path):
    # 1. App-layer: Load profile to get mount plan
    manager = ProfileManager()
    mount_plan = manager.get_profile_as_mount_plan("dev")

    # 2. Kernel: Create session (use directly - no wrapper!)
    async with AmplifierSession(config=mount_plan) as session:
        # 3. Toolkit: Use utilities for structure
        files = discover_files(input_dir, "**/*.md")
        progress = ProgressReporter(len(files), "Processing")

        # 4. Amplifier-core: Use for ALL intelligence
        for file in files:
            # Amplifier-core handles everything:
            # - Provider selection (from profile)
            # - Retries and error handling
            # - Response parsing
            # - Streaming and progress
            # - Hooks (logging, approval, redaction)
            response = await session.execute(f"Analyze: {file.read_text()}")

            # Response is clean - no parsing needed
            save_result(file, response)
            progress.update()
```

## Using the Template

### 1. Copy the Template

```bash
cp amplifier-app-cli/toolkit/templates/cli_tool_template.py my_tool.py
```

### 2. Customize for Your Use Case

The template has clear sections:

**A. Update the prompt**:
```python
async def analyze_file(file: Path, session: AmplifierSession) -> dict:
    # Change this prompt for your use case
    prompt = f"YOUR CUSTOM PROMPT: Analyze {file.name}"

    response = await session.execute(prompt)
    return {"file": str(file), "result": response}
```

**B. Modify state structure** (if needed):
```python
STATE_FILE = ".my_tool_state.json"

def save_state(data: dict):
    # Customize for your tool
    Path(STATE_FILE).write_text(json.dumps(data, indent=2))
```

### 3. Run Your Tool

```bash
python my_tool.py ./input_directory
```

## What NOT to Do

### ❌ Don't Wrap AmplifierSession

**WRONG**:
```python
class Helper:
    def __init__(self, profile: str):
        self.session = AmplifierSession(...)  # Violation!
```

**RIGHT**:
```python
mount_plan = ProfileManager().get_profile_as_mount_plan("dev")
async with AmplifierSession(config=mount_plan) as session:
    response = await session.execute(prompt)
```

### ❌ Don't Create State Frameworks

**WRONG**:
```python
class StateManager:
    """Don't generalize!"""
```

**RIGHT**:
```python
# Each tool owns simple state
STATE_FILE = ".tool_state.json"

def save_state(data: dict):
    Path(STATE_FILE).write_text(json.dumps(data))
```

### ❌ Don't Bypass Amplifier-Core

**WRONG**:
```python
import anthropic
response = anthropic.Client().messages.create(...)  # Violation!
```

**RIGHT**:
```python
# ALL LLM via amplifier-core
response = await session.execute(prompt)
```

## Philosophy References

- **Kernel Philosophy** (@docs/context/KERNEL_PHILOSOPHY.md) - Use mechanisms directly
- **Implementation Philosophy** (@docs/context/IMPLEMENTATION_PHILOSOPHY.md) - Ruthless simplicity
- **Modular Design** (@docs/context/MODULAR_DESIGN_PHILOSOPHY.md) - Clear bricks & studs
