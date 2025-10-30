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
import re
import sys
from pathlib import Path
from typing import Any

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
                "model": "claude-sonnet-4-5",
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
                "model": "claude-opus-4-1",
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
                "model": "claude-sonnet-4-5",
                "temperature": 0.2,  # Evaluative consistency
                "system_prompt": "You are a quality evaluator.",
            },
        }
    ],
}


# ==== DEFENSIVE JSON PARSING ====


def extract_dict_from_response(response: str) -> dict[str, Any]:
    """Extract dict from LLM response with defensive parsing.

    Handles markdown-wrapped JSON and explanatory text that LLMs often add.

    For production tools with comprehensive extraction, see:
    tutorial_analyzer/utils.py (5-step defensive parsing with all edge cases)

    Args:
        response: String response from session.execute()

    Returns:
        Parsed JSON dict

    Raises:
        ValueError: If response doesn't contain valid JSON dict
    """
    # Try direct JSON parsing
    try:
        result = json.loads(response)
        if isinstance(result, dict):
            return result
        raise ValueError(f"Expected JSON object, got {type(result).__name__}")
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting from markdown code blocks
    for pattern in [r"```json\s*\n?(.*?)```", r"```\s*\n?(.*?)```"]:
        matches = re.findall(pattern, response, re.DOTALL | re.IGNORECASE)
        for match in matches:
            try:
                result = json.loads(match)
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, TypeError):
                continue

    # Failed all extraction attempts
    raise ValueError(f"Could not extract valid JSON dict from response.\nPreview: {response[:300]}...")


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


async def analyze_content(content: str) -> dict[str, Any]:
    """Stage 1: Analyze content (analytical config).

    Uses defensive JSON parsing to handle LLM response variability.
    """
    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        response = await session.execute(f"Analyze this content and extract key information:\n\n{content}")
    return extract_dict_from_response(response)


async def create_from_analysis(analysis: dict, requirements: str) -> dict[str, Any]:
    """Stage 2: Create content (creative config).

    Uses defensive JSON parsing to handle LLM response variability.
    """
    async with AmplifierSession(config=CREATOR_CONFIG) as session:
        response = await session.execute(
            f"Create content based on:\nAnalysis: {analysis}\nRequirements: {requirements}"
        )
    return extract_dict_from_response(response)


async def evaluate_quality(creation: dict) -> dict[str, Any]:
    """Stage 3: Evaluate quality (evaluative config).

    Uses defensive JSON parsing to handle LLM response variability.
    """
    async with AmplifierSession(config=EVALUATOR_CONFIG) as session:
        response = await session.execute(f"Evaluate this creation and score 0-1:\n\n{creation}")
    return extract_dict_from_response(response)


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
