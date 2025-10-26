"""Standalone tool template showing multi-config metacognitive recipe pattern.

This template demonstrates:
- Multiple specialized configs (not one!)
- Code orchestration across stages
- State management with checkpointing
- Toolkit utilities usage
- Direct AmplifierSession use (no wrappers)

Philosophy alignment:
- Mechanism not policy: AmplifierSession = mechanism, CONFIGs = policy
- Policy at edges: Tool decides all configs
- Ruthless simplicity: Each piece simple, composition sophisticated

Customize:
1. Define your specialized configs (adjust temperature, system_prompt for each role)
2. Write stage processing functions (one per config)
3. Update main orchestration logic (which stage when, how to combine)
4. Adjust state structure if needed
5. Update CLI arguments
"""

import asyncio
import json
import sys
from pathlib import Path

from amplifier_core import AmplifierSession

# ==== CONFIGURATION: Multiple specialized configs (not one!) ====

# Config 1: Analytical thinking (precise, structured)
ANALYZER_CONFIG = {
    "session": {
        "orchestrator": "loop-basic",  # Simple analytical task
        "context": "context-simple",
    },
    "providers": [
        {
            "module": "provider-anthropic",
            "config": {
                "model": "claude-sonnet-4",
                "temperature": 0.3,  # Analytical precision
                "system_prompt": "You are an expert content analyzer.",
                # NOTE: Simplified prompt. See data/agents/*.md for production prompts.
            },
        }
    ],
}

# Config 2: Creative thinking (diverse, exploratory)
CREATOR_CONFIG = {
    "session": {
        "orchestrator": "loop-streaming",  # Long-form generation
        "context": "context-simple",
    },
    "providers": [
        {
            "module": "provider-anthropic",
            "config": {
                "model": "claude-opus-4",
                "temperature": 0.7,  # Creative exploration
                "system_prompt": "You are a creative content generator.",
            },
        }
    ],
}

# Config 3: Evaluative thinking (consistent, objective)
EVALUATOR_CONFIG = {
    "session": {
        "orchestrator": "loop-basic",
        "context": "context-simple",
    },
    "providers": [
        {
            "module": "provider-anthropic",
            "config": {
                "model": "claude-sonnet-4",
                "temperature": 0.2,  # Evaluative consistency
                "system_prompt": "You are a quality evaluator.",
            },
        }
    ],
}


# ==== STATE MANAGEMENT: Tool-specific, simple dict to JSON ====

STATE_FILE = ".standalone_tool_state.json"


def save_state(state: dict):
    """Save state after every stage (checkpoint for resumability)."""
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))


def load_state() -> dict:
    """Load state if exists."""
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text())
    return {}


# ==== STAGE PROCESSING: Each stage uses specialized config ====


async def analyze_content(content: str) -> dict:
    """Stage 1: Analyze content (analytical config).

    Note: In production, use defensive JSON parsing (see tutorial_analyzer/utils.py).
    This template uses simple json.loads() for clarity.
    """
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        response = await session.execute(f"Analyze this content and extract key information:\n\n{content}")
    # Parse response to dict (in production, use defensive parsing)
    return json.loads(response)  # type: ignore[arg-type]  # session.execute() returns str


async def create_from_analysis(analysis: dict, requirements: str) -> dict:
    """Stage 2: Create content (creative config).

    Note: In production, use defensive JSON parsing (see tutorial_analyzer/utils.py).
    """
    async with AmplifierSession(config=CREATOR_CONFIG) as session:
        response = await session.execute(f"Create content based on:\nAnalysis: {analysis}\nRequirements: {requirements}")
    return json.loads(response)  # type: ignore[arg-type]


async def evaluate_quality(creation: dict) -> dict:
    """Stage 3: Evaluate quality (evaluative config).

    Note: In production, use defensive JSON parsing (see tutorial_analyzer/utils.py).
    """
    async with AmplifierSession(config=EVALUATOR_CONFIG) as session:
        response = await session.execute(f"Evaluate this creation and score 0-1:\n\n{creation}")
    return json.loads(response)  # type: ignore[arg-type]


# ==== ORCHESTRATION: Code manages flow, state, decisions ====


async def process_file(input_path: Path, requirements: str = "default") -> dict:
    """Main orchestration across stages.

    This is where CODE makes decisions:
    - Which config to use when
    - How to combine results
    - When to loop or iterate
    - When to checkpoint state
    """
    # Load state (resumability)
    state = load_state()

    content = input_path.read_text()

    # Stage 1: Analyze (autonomous)
    if "analysis" not in state:
        print("Stage 1/3: Analyzing content...")
        state["analysis"] = await analyze_content(content)
        save_state(state)  # Checkpoint
        print("✓ Analysis complete")

    # Stage 2: Create (autonomous)
    if "creation" not in state:
        print("Stage 2/3: Creating content...")
        state["creation"] = await create_from_analysis(state["analysis"], requirements)
        save_state(state)  # Checkpoint
        print("✓ Creation complete")

    # Stage 3: Evaluate (autonomous)
    if "evaluation" not in state:
        print("Stage 3/3: Evaluating quality...")
        state["evaluation"] = await evaluate_quality(state["creation"])
        save_state(state)  # Checkpoint
        print("✓ Evaluation complete")

    # CODE makes decision: iterate if quality low
    score = float(state["evaluation"].get("score", 0))
    iterations = state.get("iterations", 0)

    if score < 0.8 and iterations < 3:
        print(f"Quality score {score} below threshold. Iterating...")
        state["iterations"] = iterations + 1
        del state["creation"]  # Regenerate with feedback
        feedback = f"Previous score: {score}. Issues: {state['evaluation'].get('issues', 'N/A')}"
        save_state(state)
        return await process_file(input_path, f"{requirements}\n\nFeedback: {feedback}")

    return state


# ==== CLI ENTRY POINT ====


def cli():
    """CLI entry point.

    Usage: standalone-tool <input-file> [requirements]
    """
    if len(sys.argv) < 2:
        print("Usage: standalone-tool <input-file> [requirements]")
        print("\nExample: standalone-tool tutorial.md 'focus on clarity'")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    requirements = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "default"

    # Validate input
    if not input_path.exists():
        print(f"Error: {input_path} does not exist")
        sys.exit(1)

    # Run
    result = asyncio.run(process_file(input_path, requirements))

    # Report
    print("\n✓ Complete!")
    print(f"Score: {result['evaluation'].get('score', 'N/A')}")
    print(f"Results saved to {STATE_FILE}")


if __name__ == "__main__":
    cli()
