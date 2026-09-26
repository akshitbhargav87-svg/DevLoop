import json
from typing import Any


def parse_json_response(
    content: str,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Parse JSON from an LLM response, with a safe fallback."""

    text = content.strip()

    # Handle ```json ... ``` responses.
    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return fallback

    if not isinstance(parsed, dict):
        return fallback

    return parsed