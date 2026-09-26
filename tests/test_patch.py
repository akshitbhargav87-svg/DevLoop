from unittest.mock import Mock, patch

from backend.agents.patch import generate_patch


def _mock_llm_response(content):
    response = Mock(content=content)
    llm = Mock()
    llm.invoke.return_value = response
    return llm


def test_generate_patch_returns_valid_patch():
    mock_response = Mock()
    mock_response.content = """
    {
        "patch": "--- a/store/pricing.py\\n+++ b/store/pricing.py\\n@@ -1 +1 @@\\n-old\\n+new",
        "files_changed": ["store/pricing.py"],
        "explanation": "Correct the discount calculation."
    }
    """

    mock_llm = Mock()
    mock_llm.invoke.return_value = mock_response

    hypothesis = {
        "root_cause": "The discount calculation uses the coupon rate as the final multiplier.",
        "affected_files": ["store/pricing.py"],
        "fix_strategy": "Apply the discount rate correctly.",
        "confidence": 0.95,
    }

    with patch(
        "backend.agents.patch.get_llm",
        return_value=mock_llm,
    ):
        result = generate_patch(
            hypothesis=hypothesis,
            file_contents={
                "store/pricing.py": "return order_total * coupon_rate"
            },
            bug_report={
                "description": "Expected 75 but got 25.",
                "stack_trace": "assert result == 75",
            },
        )

    assert result["patch"].startswith("--- a/store/pricing.py")
    assert result["files_changed"] == ["store/pricing.py"]
    assert result["explanation"] == "Correct the discount calculation."
    assert "Expected 75 but got 25" in mock_llm.invoke.call_args.args[0]


def test_generate_patch_rejects_unknown_files():
    mock_response = Mock()
    mock_response.content = """
    {
        "patch": "some patch",
        "files_changed": [
            "store/pricing.py",
            "secrets.py"
        ],
        "explanation": "Fix the bug."
    }
    """

    mock_llm = Mock()
    mock_llm.invoke.return_value = mock_response

    hypothesis = {
        "root_cause": "Incorrect calculation.",
        "affected_files": ["store/pricing.py"],
        "fix_strategy": "Correct the calculation.",
        "confidence": 0.8,
    }

    with patch(
        "backend.agents.patch.get_llm",
        return_value=mock_llm,
    ):
        result = generate_patch(
            hypothesis=hypothesis,
            file_contents={},
        )

    assert result["files_changed"] == []


def test_generate_patch_handles_invalid_json():
    mock_response = Mock()
    mock_response.content = "not valid json"

    mock_llm = Mock()
    mock_llm.invoke.return_value = mock_response

    with patch(
        "backend.agents.patch.get_llm",
        return_value=mock_llm,
    ):
        result = generate_patch(
            hypothesis={
                "affected_files": ["store/pricing.py"],
            },
            file_contents={},
        )

    assert result == {
        "patch": "",
        "files_changed": [],
        "explanation": (
            "Unable to generate a valid patch from the supplied evidence."
        ),
    }


def test_generate_patch_deduplicates_files():
    mock_response = Mock()
    mock_response.content = """
    {
            "patch": "--- a/store/pricing.py\\n+++ b/store/pricing.py\\n@@ -1 +1 @@\\n-old\\n+new",
        "files_changed": [
            "store/pricing.py",
            "store/pricing.py"
        ],
        "explanation": "Fix calculation."
    }
    """

    mock_llm = Mock()
    mock_llm.invoke.return_value = mock_response

    with patch(
        "backend.agents.patch.get_llm",
        return_value=mock_llm,
    ):
        result = generate_patch(
            hypothesis={
                "affected_files": ["store/pricing.py"],
            },
            file_contents={"store/pricing.py": "old"},
        )

    assert result["files_changed"] == ["store/pricing.py"]


def test_generate_patch_rejects_empty_changed_files():
    llm = _mock_llm_response(
        '{"patch":"--- a/pricing.py\\n+++ b/pricing.py\\n@@ -1 +1 @@\\n-old\\n+new",'
        '"files_changed":[],"explanation":"Fix it."}'
    )
    with patch("backend.agents.patch.get_llm", return_value=llm):
        result = generate_patch(
            {"affected_files": ["pricing.py"]},
            {"pricing.py": "old"},
        )
    assert result["files_changed"] == []


def test_generate_patch_materializes_exact_explanation_replacement():
    llm = _mock_llm_response(
        '{"patch":"invalid hunk",'
        '"files_changed":["pricing.py"],'
        '"explanation":"Change the line `return total * coupon_rate` to `return total * (1 - coupon_rate)`."}'
    )
    source = "def calculate_discount(total, coupon_rate=None):\n    if coupon_rate is None:\n        return total\n    return total * coupon_rate\n"
    with patch("backend.agents.patch.get_llm", return_value=llm):
        result = generate_patch(
            {"affected_files": ["pricing.py"]},
            {"pricing.py": source},
        )

    assert result["files_changed"] == ["pricing.py"]
    assert "-    return total * coupon_rate" in result["patch"]
    assert "+    return total * (1 - coupon_rate)" in result["patch"]
