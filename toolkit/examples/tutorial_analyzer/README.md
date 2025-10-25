# Tutorial Analyzer

Analyze tutorials and generate improvement recommendations through multi-stage metacognitive analysis.

## What It Does

Analyzes tutorials from a learner's perspective and generates a detailed improvement report:

1. **Analyzes** tutorial structure and content
2. **Simulates** learner experiencing the tutorial
3. **Diagnoses** pedagogical issues from learner perspective
4. **Generates** specific, actionable improvement recommendations
5. **Evaluates** recommendation quality
6. **Synthesizes** prioritized action plan
7. **Creates** markdown analysis report

## Installation

Via uvx (no install needed):
```bash
uvx tutorial-analyzer tutorial.md
```

Via uv tool (persistent):
```bash
uv tool install tutorial-analyzer
tutorial-analyzer tutorial.md
```

From local wheel:
```bash
uvx --from ./tutorial_analyzer-0.1.0-py3-none-any.whl tutorial-analyzer tutorial.md
```

## Usage

Basic:
```bash
tutorial-analyzer tutorial.md
```

With focus areas:
```bash
tutorial-analyzer tutorial.md clarity engagement code-examples
```

**Output:** Creates `tutorial_name_analysis.md` with:
- Recommended improvements (prioritized)
- Implementation guidance
- Quality assessment

## How It Works

### Multi-Config Metacognitive Recipe

Uses 6 specialized configs, each optimized for its cognitive role:

1. **Analyzer** (analytical, temp=0.3) - Extract tutorial structure
2. **Learner Simulator** (empathetic, temp=0.5) - Simulate learner experience
3. **Diagnostician** (precise, temp=0.1) - Identify pedagogical issues
4. **Improver** (creative, temp=0.7) - Generate improvement suggestions
5. **Critic** (evaluative, temp=0.2) - Evaluate improvement quality
6. **Synthesizer** (analytical, temp=0.3) - Create final recommendations

### Pipeline

```
Analyze → Simulate Learner → Diagnose Issues →
→ Generate Improvements → [HUMAN APPROVAL] →
→ Evaluate Improvements → Synthesize Recommendations →
→ [QUALITY CHECK] → Loop or Finalize
```

### Philosophy

- **Code for structure, AI for intelligence**: Code orchestrates, specialized configs think
- **Multiple configs, not one**: Each cognitive task gets optimized setup
- **Human-in-loop**: Strategic approval after improvement generation
- **Quality loops**: Iterate until threshold met
- **Checkpointing**: Resumable if interrupted

## Development

Install for development:
```bash
cd amplifier-app-cli/toolkit/examples/tutorial_analyzer
uv sync --all-extras
```

Run tests:
```bash
uv run pytest
```

Build:
```bash
uv build
```

Test locally:
```bash
uvx ./dist/tutorial_analyzer-*.whl tests/fixtures/sample_tutorial.md
```

## Architecture

See source code for complete implementation:
- `src/tutorial_analyzer/analyzer/` - Stage 1
- `src/tutorial_analyzer/learner_simulator/` - Stage 2
- `src/tutorial_analyzer/diagnostician/` - Stage 3
- `src/tutorial_analyzer/improver/` - Stage 4
- `src/tutorial_analyzer/critic/` - Stage 5
- `src/tutorial_analyzer/synthesizer/` - Stage 6
- `src/tutorial_analyzer/main.py` - Orchestration

Each stage is self-contained with its own specialized config.

## License

MIT
