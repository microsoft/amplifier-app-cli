"""Utility functions for tutorial_analyzer.

Defensive JSON parsing adapted from proven ccsdk_toolkit patterns.
See: DISCOVERIES.md - "LLM Response Handling and Defensive Utilities"
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def extract_json_from_response(response: str | object) -> dict:
    """Extract JSON from LLM response with defensive parsing.

    Handles multiple response formats from AmplifierSession:
    - Plain text strings
    - TextBlock objects (with .text attribute)
    - Lists of TextBlock objects
    - Markdown-wrapped JSON
    - JSON with explanatory preambles

    Args:
        response: Response from AmplifierSession

    Returns:
        Parsed JSON dict

    Raises:
        ValueError: If no valid JSON found after all extraction attempts
    """
    # Step 1: Convert to text string
    if isinstance(response, list):
        # Concatenate text from all blocks
        text = "".join(block.text if hasattr(block, "text") else str(block) for block in response)
    elif hasattr(response, "text"):
        text = response.text
    else:
        text = str(response)

    if not text or not isinstance(text, str):
        raise ValueError(f"Empty or invalid response: {type(response)}")

    # Step 2: Try direct JSON parsing
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Step 3: Extract from markdown code blocks
    # Flexible patterns that handle various formatting
    markdown_patterns = [
        r"```json\s*\n?(.*?)```",  # ```json ... ```
        r"```\s*\n?(.*?)```",  # ``` ... ```
    ]

    for pattern in markdown_patterns:
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        for match in matches:
            try:
                return json.loads(match)
            except (json.JSONDecodeError, TypeError):
                continue

    # Step 4: Find JSON structures in text
    # Look for {...} or [...] patterns
    json_patterns = [
        r"(\{[^{}]*\{[^{}]*\}[^{}]*\})",  # Nested objects
        r"(\[[^\[\]]*\[[^\[\]]*\][^\[\]]*\])",  # Nested arrays
        r"(\{[^{}]+\})",  # Simple objects
        r"(\[[^\[\]]+\])",  # Simple arrays
    ]

    for pattern in json_patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                result = json.loads(match)
                if isinstance(result, (dict, list)):
                    return result
            except (json.JSONDecodeError, TypeError):
                continue

    # Step 5: Try removing preambles
    preamble_patterns = [
        r"^.*?(?:here\'s|here is|below is|following is).*?:\s*",
        r"^.*?(?:i\'ll|i will|let me).*?:\s*",
        r"^[^{\[]*",  # Remove everything before first { or [
    ]

    for pattern in preamble_patterns:
        cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)
        if cleaned != text:
            try:
                return json.loads(cleaned)
            except (json.JSONDecodeError, TypeError):
                continue

    # All attempts failed
    raise ValueError(
        f"Could not extract valid JSON from response.\nResponse preview (first 300 chars):\n{text[:300]}..."
    )
