"""FileIdentificationAgent: rank repository paths using bug evidence."""

from __future__ import annotations

from typing import Any

from agents.llm_utils import parse_json_response
from llm_client import get_llm


def _validated_files(
    payload: dict[str, Any],
    available_files: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Keep only well-formed results that refer to paths in the repository."""
    candidates = payload.get("files")
    if not isinstance(candidates, list):
        return {"files": []}

    ranked: list[tuple[int, str, str]] = []
    seen_paths: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue

        path = item.get("path")
        reason = item.get("relevance_reason")
        rank = item.get("rank")
        if not isinstance(path, str) or path not in available_files or path in seen_paths:
            continue
        if isinstance(rank, bool):
            continue
        if isinstance(rank, str) and rank.isdecimal():
            rank = int(rank)
        if not isinstance(rank, int) or rank < 1:
            continue
        if not isinstance(reason, str):
            continue

        reason = " ".join(reason.split())
        if not reason:
            continue
        seen_paths.add(path)
        ranked.append((rank, path, reason))

    ranked.sort(key=lambda result: result[0])
    return {
        "files": [
            {"path": path, "relevance_reason": reason, "rank": rank}
            for rank, (_, path, reason) in enumerate(ranked[:10], start=1)
        ]
    }


def identify_files(stack_trace: str, file_listing: str) -> dict[str, Any]:
    """Rank listed repository files by likely relevance to a failure.

    The agent receives only the raw stack trace and repository listing. It does
    not use LogAnalysisAgent output, and its results are constrained to paths
    present in ``file_listing``.
    """
    available_files = {
        line.strip()
        for line in file_listing.splitlines()
        if line.strip()
    }
    if not available_files:
        return {"files": []}

    prompt = f"""
You are the FileIdentificationAgent for DevLoop.

Rank the listed repository files by how likely each file is involved in the
reported failure. Use the raw stack trace and file listing only. Do not use
LogAnalysisAgent output, inspect file contents, propose a fix, or invent paths.
Return at most 10 files, ordered from most to least relevant. Give each a brief,
single-line relevance_reason and a unique 1-based rank.

Return ONLY valid JSON with exactly this structure:
{{
  "files": [
    {{"path": "exact path from the listing", "relevance_reason": "brief reason", "rank": 1}}
  ]
}}

Repository file listing:
{file_listing}

Stack trace:
{stack_trace}
"""

    response = get_llm().invoke(prompt)
    content = getattr(response, "content", "")
    payload = parse_json_response(content if isinstance(content, str) else str(content), {"files": []})
    return _validated_files(payload, available_files)
