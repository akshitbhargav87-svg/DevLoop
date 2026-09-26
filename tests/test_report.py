import json

from backend.agents.report import generate_report


def test_generate_report_returns_resolution_fields():
    result = generate_report(
        run_id="run-123",
        hypothesis={
            "root_cause": "Coupon rate is used as the final multiplier.",
            "affected_files": ["store/pricing.py"],
            "fix_strategy": "Apply the discount to the order total.",
            "confidence": 0.95,
        },
        patch_result={
            "diff": "--- a/store/pricing.py\n+++ b/store/pricing.py",
            "approved": True,
        },
        test_result={
            "passed": 5,
            "failed": 0,
            "errors": [],
        },
        iteration_count=1,
    )

    assert result["run_id"] == "run-123"
    assert "Coupon rate" in result["root_cause"]
    assert "store/pricing.py" in result["evidence"]
    assert result["iteration_count"] == 1
    assert result["patch_approved"] == 1
    assert result["generated_at"] is not None


def test_generate_report_rejects_failed_patch():
    result = generate_report(
        run_id="run-456",
        hypothesis={
            "root_cause": "Incorrect discount calculation.",
        },
        patch_result={
            "diff": "some diff",
            "approved": False,
        },
        test_result={
            "passed": 2,
            "failed": 1,
            "errors": [],
        },
        iteration_count=2,
    )

    assert result["patch_approved"] == 0


def test_generate_report_handles_missing_diff():
    result = generate_report(
        run_id="run-789",
        hypothesis={
            "root_cause": "Unknown",
        },
        patch_result={
            "changes_made": "Updated pricing logic.",
            "approved": True,
        },
        test_result={
            "passed": 3,
            "failed": 0,
            "errors": [],
        },
        iteration_count=1,
    )

    assert result["changes_made"] == "Updated pricing logic."


def test_generate_report_serializes_structured_results():
    result = generate_report(
        run_id="run-999",
        hypothesis={"root_cause": "Test cause"},
        patch_result={
            "diff": "diff",
            "approved": True,
        },
        test_result={
            "passed": 4,
            "failed": 0,
            "errors": [],
        },
        iteration_count=3,
    )

    assert isinstance(result["evidence"], str)
    assert isinstance(result["test_results"], str)
    assert '"passed": 4' in result["test_results"]
    assert json.loads(result["evidence"])["patch"]["diff"] == "diff"


def test_generate_report_accepts_patchagent_patch_field():
    result = generate_report(
        run_id="run-321",
        hypothesis={"root_cause": "Incorrect coupon arithmetic."},
        patch_result={
            "patch": "--- a/store/pricing.py\n+++ b/store/pricing.py",
            "files_changed": ["store/pricing.py"],
            "approved": True,
        },
        test_result={"passed": 2, "failed": 0, "errors": []},
        iteration_count=1,
    )

    assert result["changes_made"].startswith("--- a/store/pricing.py")
