# Packaging Guide: Standalone Amplifier Tools

## Overview

This guide shows how to package standalone amplifier tools for distribution via `uvx` and PyPI. Standalone tools are self-contained Python packages that use amplifier-core for AI orchestration.

**What you'll learn**:

- Project structure for standalone tools
- `pyproject.toml` configuration
- Entry points setup
- Module dependency bundling
- Testing locally with `uv`
- Publishing to PyPI
- Using with `uvx`

## Project Structure

A standalone tool follows standard Python package structure:

```
my-tool/
  pyproject.toml           # Package metadata
  README.md                # User documentation
  LICENSE                  # License file

  src/my_tool/             # Source package
    __init__.py
    main.py                # Entry point & CLI
    state.py               # State management

    # Specialized config modules (if multi-config)
    analyzer/
      __init__.py
      core.py              # ANALYZER_CONFIG + analyze()

    creator/
      __init__.py
      core.py              # CREATOR_CONFIG + create()

  tests/                   # Tests
    test_analyzer.py
    test_creator.py
    test_integration.py
```

## pyproject.toml Configuration

### Basic Metadata

```toml
[project]
name = "my-tool"
version = "0.1.0"
description = "Description of what your tool does"
authors = [{name = "Your Name", email = "you@example.com"}]
requires-python = ">=3.11"
readme = "README.md"
license = {text = "MIT"}

keywords = ["ai", "amplifier", "cli"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
```

### Dependencies

```toml
[project]
dependencies = [
    "amplifier-core",
]

# Optional dependency groups
[project.optional-dependencies]
# Amplifier modules needed at runtime
amplifier = [
    "amplifier-module-provider-anthropic @ git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "amplifier-module-loop-streaming @ git+https://github.com/microsoft/amplifier-module-loop-streaming@main",
    "amplifier-module-context-simple @ git+https://github.com/microsoft/amplifier-module-context-simple@main",
]

# Development dependencies
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "ruff>=0.6.0",
]
```

### Module Sources (Critical!)

Specify git URLs for amplifier-core and modules:

```toml
[tool.uv.sources]
amplifier-core = { git = "https://github.com/microsoft/amplifier-core", branch = "main" }
```

**Why git URLs?** They work for both local development and GitHub installation via uvx:

```bash
uvx --from git+https://github.com/you/my-tool@main my-tool input.md
```

### Entry Points

Define CLI command:

```toml
[project.scripts]
my-tool = "my_tool.main:cli"
```

This creates the `my-tool` command that calls `cli()` function in `my_tool/main.py`.

### Build System

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

## Entry Point Setup

### main.py Structure

```python
"""Main entry point for my-tool CLI."""

import asyncio
import sys
from pathlib import Path

# Import specialized configs
from .analyzer.core import ANALYZER_CONFIG, analyze
from .creator.core import CREATOR_CONFIG, create
from .state import load_state, save_state


def cli():
    """CLI entry point called by `my-tool` command."""
    if len(sys.argv) < 2:
        print("Usage: my-tool <input-file> [options]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    asyncio.run(main(input_path))


async def main(input_path: Path):
    """Main tool logic."""
    state = load_state()

    # Stage 1: Analyze
    if "analysis" not in state:
        state["analysis"] = await analyze(input_path.read_text())
        save_state(state)

    # Stage 2: Create
    if "creation" not in state:
        state["creation"] = await create(state["analysis"])
        save_state(state)

    print(f"Complete! Results: {state['creation']}")


if __name__ == "__main__":
    cli()
```

## Module Dependency Bundling

### Strategy

Amplifier modules are loaded at runtime via git URLs specified in configs:

```python
ANALYZER_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",  # Runtime
        "config": {...}
    }]
}
```

**On first run**, amplifier-core will:

1. Clone module from git URL
2. Install module in isolated environment
3. Load via entry points
4. Execute as configured

**Subsequent runs**: Modules cached, fast startup.

### Installation Instructions for Users

In your README.md:

````markdown
## Installation

Install my-tool with amplifier modules:

```bash
# Via uvx (no install needed)
uvx my-tool input.md

# Or install globally
uv tool install my-tool

# Then run
my-tool input.md
```
````

On first run, amplifier will automatically install required modules from git sources specified in the tool's configuration.

````

### Optional: Pre-bundle Modules

For offline use or faster startup, you can bundle modules as package dependencies:

```toml
[project.optional-dependencies]
amplifier = [
    "amplifier-module-provider-anthropic @ git+https://github.com/...",
    # Pre-install modules
]
````

Install with:

```bash
uv tool install my-tool[amplifier]
```

## Testing Locally

### With uv

```bash
# In your tool directory
uv sync --all-extras

# Run directly
uv run python -m my_tool input.md

# Or via entry point
uv run my-tool input.md
```

### With uvx

```bash
# Build distribution
uv build

# Test via uvx
uvx ./dist/my_tool-0.1.0-py3-none-any.whl input.md
```

### Test Module Loading

Verify modules load correctly:

```bash
# Run with verbose logging
MY_TOOL_DEBUG=1 uv run my-tool input.md

# Check modules loaded
# Should see: "Loading module provider-anthropic from git+..."
```

## Publishing to PyPI

### Preparation

1. **Build the package**:

```bash
uv build
```

This creates `dist/my_tool-0.1.0-py3-none-any.whl` and `.tar.gz`.

2. **Test the distribution**:

```bash
# Install locally and test
uv tool install ./dist/my_tool-0.1.0-py3-none-any.whl
my-tool test_input.md
uv tool uninstall my-tool
```

3. **Create PyPI account** (if you don't have one):
   - Go to https://pypi.org
   - Register account
   - Get API token from account settings

### Publishing

```bash
# Publish to PyPI
uv publish

# It will prompt for PyPI token
# Or set in environment: UV_PUBLISH_TOKEN=pypi-...
```

### After Publishing

Users can install via:

```bash
# Via uvx (ephemeral)
uvx my-tool input.md

# Via uv tool (persistent)
uv tool install my-tool
my-tool input.md

# Via pip (traditional)
pip install my-tool
my-tool input.md
```

## Using with uvx

### For Users

**Ephemeral execution** (no install):

```bash
uvx my-tool input.md
```

**From specific version**:

```bash
uvx my-tool==0.2.0 input.md
```

**From git repository**:

```bash
uvx --from git+https://github.com/you/my-tool@main my-tool input.md
```

### For Development

**Test unreleased versions**:

```bash
# From local wheel
uvx ./dist/my_tool-0.1.0-py3-none-any.whl input.md

# From git branch
uvx --from git+https://github.com/you/my-tool@dev my-tool input.md
```

## Complete Example: tutorial_analyzer

### Directory Structure

```
tutorial-analyzer/
  pyproject.toml
  README.md
  LICENSE

  src/tutorial_analyzer/
    __init__.py
    main.py
    state.py

    analyzer/
      __init__.py
      core.py

    learner_simulator/
      __init__.py
      core.py

    # ... other stages

  tests/
    test_analyzer.py
    # ... other tests
```

### pyproject.toml

```toml
[project]
name = "tutorial-analyzer"
version = "0.1.0"
description = "Improve tutorials through multi-stage metacognitive analysis"
requires-python = ">=3.11"
dependencies = [
    "amplifier-core",
]

[project.optional-dependencies]
amplifier = [
    "amplifier-module-provider-anthropic @ git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "amplifier-module-loop-streaming @ git+https://github.com/microsoft/amplifier-module-loop-streaming@main",
    "amplifier-module-context-simple @ git+https://github.com/microsoft/amplifier-module-context-simple@main",
]

[project.scripts]
tutorial-analyzer = "tutorial_analyzer.main:cli"

[tool.uv.sources]
amplifier-core = { git = "https://github.com/microsoft/amplifier-core", branch = "main" }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Building and Testing

```bash
cd tutorial-analyzer

# Sync dependencies
uv sync --all-extras

# Test locally
uv run tutorial-analyzer example.md

# Build
uv build

# Test built distribution
uvx ./dist/tutorial_analyzer-0.1.0-py3-none-any.whl example.md

# Publish
uv publish
```

## Best Practices

### 1. Keep It Self-Contained

**Good**: All code in package

```
src/my_tool/
  main.py
  analyzer/core.py
  creator/core.py
```

**Bad**: External dependencies on local files

```python
# Don't do this
from amplifier_app_cli.some_module import helper  # Not packaged!
```

### 2. Module Sources in Configs

Always specify module sources in configs, not just in pyproject.toml:

```python
# In your config constants
ANALYZER_CONFIG = {
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",  # Required!
        "config": {...}
    }]
}
```

### 3. Use git URLs

For both amplifier-core and modules:

```toml
[tool.uv.sources]
amplifier-core = { git = "https://github.com/microsoft/amplifier-core", branch = "main" }

# NOT path dependencies (breaks GitHub install)
# amplifier-core = { path = "../amplifier-core", editable = true }  # WRONG
```

### 4. Test the Distribution

Before publishing:

```bash
# Build
uv build

# Test built wheel (not source!)
uvx ./dist/my_tool-*.whl test_input

# Verify module loading works
MY_TOOL_DEBUG=1 uvx ./dist/my_tool-*.whl test_input
```

### 5. Semantic Versioning

Follow semver for versions:

- `0.1.0` - Initial development
- `0.2.0` - Added features (backward compatible)
- `1.0.0` - Stable public API
- `1.1.0` - New features (backward compatible)
- `2.0.0` - Breaking changes

## Troubleshooting

### Issue: "Module not found" during execution

**Cause**: Module source missing or incorrect in config.

**Solution**: Ensure every module reference has `source`:

```python
"providers": [{
    "module": "provider-anthropic",
    "source": "git+https://github.com/...",  # Must have this!
    "config": {...}
}]
```

### Issue: "Cannot import from amplifier_app_cli"

**Cause**: Tool depends on amplifier-app-cli code (not packaged).

**Solution**: Move code into your package:

```bash
# Don't do this
from amplifier_app_cli.utils import helper  # Not available!

# Do this
from my_tool.utils import helper  # In your package
```

### Issue: uvx can't find git repository

**Cause**: Wrong git URL or branch doesn't exist.

**Solution**: Test git URLs:

```bash
# Test if URL works
git ls-remote https://github.com/microsoft/amplifier-core

# Verify branch exists
git ls-remote https://github.com/microsoft/amplifier-core | grep main
```

### Issue: Modules not loading in packaged tool

**Cause**: Modules not in `[project.optional-dependencies]` or sources not configured.

**Solution**: Add to pyproject.toml:

```toml
[project.optional-dependencies]
amplifier = [
    "amplifier-module-provider-anthropic @ git+https://...",
]

[tool.uv.sources]
amplifier-core = { git = "https://...", branch = "main" }
```

## Quick Reference

### Development Workflow

```bash
# 1. Create project
mkdir my-tool && cd my-tool

# 2. Initialize pyproject.toml
# (copy template from this guide)

# 3. Create source structure
mkdir -p src/my_tool

# 4. Write code
# (define configs, orchestration, CLI)

# 5. Install locally
uv sync --all-extras

# 6. Test
uv run my-tool test_input

# 7. Build
uv build

# 8. Test distribution
uvx ./dist/my_tool-*.whl test_input

# 9. Publish
uv publish
```

### User Installation

```bash
# Ephemeral (no install)
uvx my-tool input.md

# Persistent install
uv tool install my-tool
my-tool input.md

# From git
uvx --from git+https://github.com/you/my-tool@main my-tool input.md
```

## Multi-Config Tools

For tools with multiple specialized configs, organize by cognitive stage:

```
src/my_tool/
  main.py              # Orchestration

  # Each stage = module with config
  stage1_analyzer/
    __init__.py
    core.py            # ANALYZER_CONFIG = {...}

  stage2_creator/
    __init__.py
    core.py            # CREATOR_CONFIG = {...}

  stage3_evaluator/
    __init__.py
    core.py            # EVALUATOR_CONFIG = {...}
```

**In each core.py**:

```python
"""Stage N: Description of cognitive task."""

from amplifier_core import AmplifierSession

STAGE_N_CONFIG = {
    "session": {"orchestrator": "loop-basic"},
    "providers": [{
        "module": "provider-anthropic",
        "source": "git+https://github.com/...",
        "config": {
            "model": "claude-sonnet-4-5",
            "temperature": 0.3,  # Optimized for this stage
            "system_prompt": "Stage-specific instructions..."
        }
    }]
}

async def process_stage_n(input_data: str) -> dict:
    """Execute stage N processing."""
    async with AmplifierSession(config=STAGE_N_CONFIG) as session:
        result = await session.execute(f"Process: {input_data}")
    return result
```

**In main.py**:

```python
"""Main orchestration across stages."""

from .stage1_analyzer.core import process_stage_1
from .stage2_creator.core import process_stage_2
from .stage3_evaluator.core import process_stage_3
from .state import load_state, save_state


async def main(input_path: Path):
    """Orchestrate multi-stage pipeline."""
    state = load_state()

    # Stage 1
    if "stage1" not in state:
        state["stage1"] = await process_stage_1(...)
        save_state(state)

    # Stage 2
    if "stage2" not in state:
        state["stage2"] = await process_stage_2(state["stage1"])
        save_state(state)

    # Stage 3
    if "stage3" not in state:
        state["stage3"] = await process_stage_3(state["stage2"])
        save_state(state)

    return state["stage3"]
```

Clean separation: each stage self-contained, main.py orchestrates.

## Example pyproject.toml (Complete)

```toml
[project]
name = "tutorial-analyzer"
version = "0.1.0"
description = "Improve tutorials through multi-stage metacognitive analysis"
authors = [{name = "Your Name", email = "you@example.com"}]
requires-python = ">=3.11"
readme = "README.md"
license = {text = "MIT"}

keywords = ["ai", "tutorial", "education", "amplifier"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Intended Audience :: Education",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]

dependencies = [
    "amplifier-core",
]

[project.optional-dependencies]
amplifier = [
    "amplifier-module-provider-anthropic @ git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "amplifier-module-loop-streaming @ git+https://github.com/microsoft/amplifier-module-loop-streaming@main",
    "amplifier-module-loop-basic @ git+https://github.com/microsoft/amplifier-module-loop-basic@main",
    "amplifier-module-context-simple @ git+https://github.com/microsoft/amplifier-module-context-simple@main",
]

dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "ruff>=0.6.0",
    "pyright>=1.1.0",
]

[project.scripts]
tutorial-analyzer = "tutorial_analyzer.main:cli"

[tool.uv.sources]
amplifier-core = { git = "https://github.com/microsoft/amplifier-core", branch = "main" }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 120
target-version = "py311"

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "basic"
```

## Summary

**Packaging standalone amplifier tools**:

1. **Structure**: Standard Python package with `src/` layout
2. **Dependencies**: amplifier-core + modules via git URLs
3. **Entry points**: CLI command via `[project.scripts]`
4. **Build**: `uv build` creates distributable wheel
5. **Test**: `uvx ./dist/*.whl` before publishing
6. **Publish**: `uv publish` to PyPI
7. **Use**: `uvx my-tool` (ephemeral) or `uv tool install my-tool` (persistent)

**Key principles**:

- Self-contained (all code in package)
- Git URLs for dependencies (not paths)
- Module sources in configs (runtime loading)
- Entry point for CLI command
- Test distribution before publishing

**For multi-config tools**:

- Organize by cognitive stage
- Each stage = module with specialized config
- Main orchestrates across stages
- State management for resumability

See `toolkit/examples/tutorial_analyzer/` for complete working example ready for packaging.

**Next steps**:

1. Copy `tutorial_analyzer` structure as starting point
2. Customize for your use case
3. Follow this packaging guide
4. Test locally with `uvx`
5. Publish to PyPI

**Remember**: Packaging is just making your tool distributable. The real value is in the metacognitive recipe you've built.
