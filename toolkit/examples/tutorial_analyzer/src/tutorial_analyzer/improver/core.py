"""Stage 4: Improvement generation (creative thinking).

Contract (Stud):
- Input: dict (diagnosis), list[str] (focus areas)
- Output: dict with keys: {suggestions, rationale, examples}
- Config: IMPROVER_CONFIG (creative, temp=0.7)

Philosophy:
- This is a BRICK - self-contained, regeneratable from this spec
- Config is POLICY - tool decided temp=0.7 for creative generation
- AmplifierSession is MECHANISM - kernel unchanged
"""

from amplifier_core import AmplifierSession

from ..utils import extract_dict_from_response

IMPROVER_CONFIG = {
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
                "system_prompt": """You are a creative tutorial improvement expert.

Generate specific, actionable improvements:
- Clear, concrete suggestions
- Examples of how to implement
- Rationale for each improvement
- Consider pedagogical best practices

Return JSON with keys: suggestions, rationale, examples
""",
            },
        }
    ],
}


async def generate_improvements(diagnosis: dict, focus_areas: list[str] | None = None) -> dict:
    """Generate improvement suggestions based on diagnosis.

    Args:
        diagnosis: Diagnosis results from stage 3
        focus_areas: Optional list of areas to focus on (e.g., ["clarity", "examples"])

    Returns:
        Dict with improvements: {suggestions, rationale, examples}
    """
    focus_text = f"\nFocus areas: {', '.join(focus_areas)}" if focus_areas else ""

    prompt = f"""Generate improvements for this tutorial:

DIAGNOSIS:
{diagnosis}
{focus_text}

CRITICAL: Return EXACTLY this JSON structure with AT LEAST 5-8 different improvements:

{{
  "suggestions": [
    {{
      "title": "First Improvement",
      "description": "Detailed description",
      "location": "Section/location"
    }},
    {{
      "title": "Second Improvement",
      "description": "Another improvement",
      "location": "Where to add this"
    }},
    {{
      "title": "Third Improvement",
      "description": "Keep adding more",
      "location": "Location"
    }}
  ],
  "rationale": "Why these improvements help learners",
  "examples": "Implementation examples"
}}

IMPORTANT: The "suggestions" field MUST be an ARRAY of at least 5-8 improvement objects.
Generate specific, actionable, pedagogically-focused improvements.
"""

    async with AmplifierSession(config=IMPROVER_CONFIG) as session:
        response = await session.execute(prompt)

    return extract_dict_from_response(response)
