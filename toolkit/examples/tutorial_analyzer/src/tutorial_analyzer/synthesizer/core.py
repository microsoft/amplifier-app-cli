"""Stage 6: Final synthesis (analytical thinking).

Contract (Stud):
- Input: dict (critique), dict (improvements), dict (diagnosis)
- Output: dict with keys: {recommendations, implementation_order, quality_score}
- Config: SYNTHESIZER_CONFIG (analytical, temp=0.3)

Philosophy:
- This is a BRICK - self-contained, regeneratable from this spec
- Config is POLICY - tool decided temp=0.3 for analytical synthesis
- AmplifierSession is MECHANISM - kernel unchanged
"""

from amplifier_core import AmplifierSession

from ..utils import extract_dict_from_response

SYNTHESIZER_CONFIG = {
    "session": {
        "orchestrator": "loop-basic",
        "context": "context-simple",
    },
    "providers": [
        {
            "module": "provider-anthropic",
            "config": {
                "model": "claude-sonnet-4-5",
                "temperature": 0.3,  # Analytical synthesis
                "system_prompt": """You are a synthesis expert creating final recommendations.

Synthesize all information into actionable plan:
- Prioritize improvements by impact
- Recommend implementation order
- Provide clear, structured guidance
- Include quality assessment

Return JSON with keys: recommendations, implementation_order, quality_score
""",
            },
        }
    ],
}


async def synthesize_recommendations(critique: dict, improvements: dict, diagnosis: dict) -> dict:
    """Synthesize final recommendations from all stages.

    Args:
        critique: Evaluation results from stage 5
        improvements: Improvement suggestions from stage 4
        diagnosis: Diagnosis results from stage 3

    Returns:
        Dict with final synthesis: {recommendations, implementation_order, quality_score}
    """
    prompt = f"""Synthesize final recommendations:

CRITIQUE:
{critique}

IMPROVEMENTS:
{improvements}

ORIGINAL DIAGNOSIS:
{diagnosis}

Return EXACTLY this JSON structure:

{{
  "recommendations": ["First action to take", "Second action", "Third action"],
  "implementation_order": [1, 2, 3],
  "quality_score": 0.85
}}

Provide clear, prioritized recommendations. Quality score from 0.0 to 1.0.
"""

    async with AmplifierSession(config=SYNTHESIZER_CONFIG) as session:
        response = await session.execute(prompt)

    return extract_dict_from_response(response)
