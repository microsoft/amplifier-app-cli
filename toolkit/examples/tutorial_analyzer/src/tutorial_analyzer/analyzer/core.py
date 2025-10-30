"""Stage 1: Tutorial content analysis (analytical thinking).

Contract (Stud):
- Input: str (tutorial content)
- Output: dict with keys: {structure, sections, concepts, complexity}
- Config: ANALYZER_CONFIG (analytical, temp=0.3)

Philosophy:
- This is a BRICK - self-contained, regeneratable from this spec
- Config is POLICY - tool decided temp=0.3 for analytical precision
- AmplifierSession is MECHANISM - kernel unchanged
"""

from amplifier_core import AmplifierSession

from ..utils import extract_dict_from_response

ANALYZER_CONFIG = {
    "session": {
        "orchestrator": "loop-basic",
        "context": "context-simple",
    },
    "providers": [
        {
            "module": "provider-anthropic",
            "config": {
                "model": "claude-sonnet-4-5",
                "temperature": 0.3,  # Analytical precision
                "system_prompt": """You are an expert tutorial content analyzer.

Extract:
- Overall structure (sections, flow)
- Learning concepts introduced
- Prerequisites assumed
- Complexity level
- Code examples present

Return JSON with keys: structure, sections, concepts, complexity, examples
""",
            },
        }
    ],
}


async def analyze(content: str) -> dict:
    """Analyze tutorial content structure.

    Args:
        content: Tutorial markdown content

    Returns:
        Dict with analysis results: {structure, sections, concepts, complexity, examples}
    """
    prompt = f"""Analyze this tutorial:

{content}

Return JSON with:
- structure: Overall organization
- sections: List of sections with titles
- concepts: Key concepts introduced
- complexity: Level (beginner/intermediate/advanced)
- examples: Code examples present (boolean)
"""

    async with AmplifierSession(config=ANALYZER_CONFIG) as session:
        response = await session.execute(prompt)

    return extract_dict_from_response(response)
