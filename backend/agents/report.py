"""ReportAgent: assemble the final structured DevLoop resolution report."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def generate_report(
    run_id: str,
    hypothesis: dict[str, Any],
    patch_result: dict[str, Any],
    test_result: dict[str, Any],
    iteration_count: int,
) -> dict[str, Any]:
    """Assemble report values matching the ResolutionReport model."""

    root_cause = hypothesis.get(
        "root_cause",
        "Unable to determine the root cause.",
    )

    evidence = {
        "hypothesis": hypothesis,
        "patch": patch_result,
        "tests": test_result,
    }

    changes_made = patch_result.get(
        "diff",
        patch_result.get(
            "patch",
            patch_result.get("changes_made", ""),
        ),
    )

    patch_approved = int(
        bool(patch_result.get("approved", patch_result.get("success", False)))
        and test_result.get("failed", 0) == 0
        and test_result.get("errors", []) == []
    )

    return {
        "run_id": run_id,
        "root_cause": str(root_cause),
        "evidence": json.dumps(evidence, ensure_ascii=False, default=str),
        "changes_made": (
            changes_made
            if isinstance(changes_made, str)
            else json.dumps(changes_made, ensure_ascii=False, default=str)
        ),
        "test_results": json.dumps(
            test_result,
            ensure_ascii=False,
            default=str,
        ),
        "iteration_count": int(iteration_count),
        "patch_approved": patch_approved,
        # Match ResolutionReport's UTC timestamp default while making the
        # generated report complete before it is persisted.
        "generated_at": datetime.utcnow(),
    }
