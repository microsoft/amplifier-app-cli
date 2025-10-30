"""Stage 5: Improvement evaluation (evaluative thinking).

Contract (Stud):
- Input: dict (improvements), dict (diagnosis)
- Output: dict with keys: {scores, strengths, weaknesses, overall_quality}
- Config: CRITIC_CONFIG (evaluative, temp=0.2)

Philosophy:
- This is a BRICK - self-contained, regeneratable from this spec
- Config is POLICY - tool decided temp=0.2 for consistent evaluation
- AmplifierSession is MECHANISM - kernel unchanged
"""

from amplifier_core import AmplifierSession

from ..utils import extract_dict_from_response

CRITIC_CONFIG = {
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
                "system_prompt": """You are a quality evaluator for tutorial improvements.

Evaluate improvements objectively:
- Are suggestions specific and actionable?
- Do they address root causes?
- Are examples clear and helpful?
- Is the rationale sound?

Return JSON with keys: scores, strengths, weaknesses, overall_quality
""",
            },
        }
    ],
}


async def evaluate_improvements(improvements: dict, diagnosis: dict) -> dict:
    """Evaluate quality of improvement suggestions.

    Args:
        improvements: Improvement suggestions from stage 4
        diagnosis: Diagnosis results from stage 3

    Returns:
        Dict with evaluation: {scores, strengths, weaknesses, overall_quality}
    """
    prompt = f"""Evaluate these improvement suggestions:

IMPROVEMENTS:
{improvements}

ORIGINAL DIAGNOSIS:
{diagnosis}

Return EXACTLY this JSON structure:

{{
  "scores": {{"specificity": 0.8, "actionability": 0.9, "impact": 0.7}},
  "strengths": "What makes these improvements strong",
  "weaknesses": "What could be improved",
  "overall_quality": 0.8
}}

Provide honest, specific evaluation. Score from 0.0 to 1.0.
"""

    async with AmplifierSession(config=CRITIC_CONFIG) as session:
        response = await session.execute(prompt)

    return extract_dict_from_response(response)
