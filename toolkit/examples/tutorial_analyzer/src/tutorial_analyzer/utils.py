"""Utility functions for tutorial_analyzer.

Defensive JSON parsing adapted from proven ccsdk_toolkit patterns.  # cspell:ignore ccsdk
See: DISCOVERIES.md - "LLM Response Handling and Defensive Utilities"
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def extract_json_from_response(response: str | object) -> dict[str, Any] | list[Any]:
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
        text = response.text  # type: ignore[attr-defined]  # Defensive: checked with hasattr
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
                if isinstance(result, dict | list):
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


def extract_dict_from_response(response: str | object) -> dict[str, Any]:
    """Extract dict from LLM response with structure validation.

    Wrapper around extract_json_from_response that validates result is a dict.
    Use this when you expect a JSON object, not an array.

    Args:
        response: Response from AmplifierSession

    Returns:
        Parsed JSON dict

    Raises:
        ValueError: If response doesn't contain valid JSON dict
    """
    result = extract_json_from_response(response)
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object (dict), got {type(result).__name__}: {result}")
    return result


def extract_list_from_response(response: str | object) -> list[Any]:
    """Extract list from LLM response with structure validation.

    Wrapper around extract_json_from_response that validates result is a list.
    Use this when you expect a JSON array, not an object.

    Args:
        response: Response from AmplifierSession

    Returns:
        Parsed JSON list

    Raises:
        ValueError: If response doesn't contain valid JSON list
    """
    result = extract_json_from_response(response)
    if not isinstance(result, list):
        raise ValueError(f"Expected JSON array (list), got {type(result).__name__}: {result}")
    return result
