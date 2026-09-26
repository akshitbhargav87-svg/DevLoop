from typing import Any

from backend.agents.llm_utils import parse_json_response
from backend.llm_client import get_llm


def analyze_log(
    stack_trace: str,
    description: str,
) -> dict[str, Any]:
    """Analyze a bug report stack trace and return structured evidence."""

    prompt = f"""
You are the LogAnalysisAgent for DevLoop.

Analyze the supplied bug description and stack trace.

Your job is ONLY to identify:
- exception: exception or failure type
- module: most relevant module/file mentioned by the failure
- line: most relevant line number
- call_chain: ordered list of relevant functions/files in the call chain

Do NOT inspect or invent repository files.
Do NOT propose a fix.
Do NOT explain your reasoning.

Return ONLY valid JSON with exactly this structure:

{{
  "exception": "string",
  "module": "string",
  "line": 0,
  "call_chain": ["string"]
}}

Bug description:
{description}

Stack trace:
{stack_trace}
"""

    fallback = {
        "exception": "Unknown",
        "module": "",
        "line": 0,
        "call_chain": [],
    }

    response = get_llm().invoke(prompt)

    content = getattr(response, "content", "")
    return parse_json_response(content, fallback)