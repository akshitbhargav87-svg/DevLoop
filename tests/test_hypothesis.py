from unittest.mock import Mock, patch

from backend.agents.hypothesis import generate_hypothesis


LOG_ANALYSIS = {
    "exception": "AssertionError",
    "module": "store/pricing.py",
    "line": 3,
    "call_chain": ["process_payment", "calculate_discount"],
}
RELEVANT_FILES = {
    "files": [
        {"path": "store/pricing.py", "relevance_reason": "Contains the price calculation.", "rank": 1},
        {"path": "store/checkout.py", "relevance_reason": "Uses the calculated amount.", "rank": 2},
    ]
}
RECENT_CHANGES = {
    "commits": [
        {
            "sha": "abc123",
            "message": "refactor: rename apply_coupon to calculate_discount",
            "files_changed": ["store/pricing.py"],
        }
    ]
}


def _mock_llm_response(content: str) -> Mock:
    response = Mock()
    response.content = content
    llm = Mock()
    llm.invoke.return_value = response
    return llm


def test_generate_hypothesis_synthesizes_evidence():
    llm = _mock_llm_response(
        '{"root_cause":"The rename refactor changed the formula to return the coupon amount.",'
        '"affected_files":["store/pricing.py"],'
        '"fix_strategy":"Restore order_total * (1 - coupon_rate) in pricing.py.",'
        '"confidence":0.94}'
    )

    with patch("backend.agents.hypothesis.get_llm", return_value=llm):
        result = generate_hypothesis(
            LOG_ANALYSIS,
            RELEVANT_FILES,
            RECENT_CHANGES,
            previous_failures=["Expected 75.0, got 25.0"],
            bug_report={
                "description": "calculate_discount(100, 0.25) returns 25 but should return 75.",
                "stack_trace": "assert result == 75\\nE assert 25 == 75",
                "test_command": "pytest -q",
            },
            file_contents={
                "pricing.py": "return total * coupon_rate",
                "test_pricing.py": "assert calculate_discount(100, 0.25) == 75",
            },
        )

    assert result == {
        "root_cause": "The rename refactor changed the formula to return the coupon amount.",
        "affected_files": ["store/pricing.py"],
        "fix_strategy": "Restore order_total * (1 - coupon_rate) in pricing.py.",
        "confidence": 0.94,
    }
    prompt = llm.invoke.call_args.args[0]
    assert "Expected 75.0, got 25.0" in prompt
    assert "refactor: rename apply_coupon to calculate_discount" in prompt
    assert "store/pricing.py" in prompt
    assert "returns 25 but should return 75" in prompt
    assert "E assert 25 == 75" in prompt
    assert "assert calculate_discount(100, 0.25) == 75" in prompt


def test_generate_hypothesis_filters_unsupported_affected_files():
    llm = _mock_llm_response(
        '{"root_cause":"The pricing formula is incorrect.",'
        '"affected_files":["missing.py","store/pricing.py","store/pricing.py"],'
        '"fix_strategy":"Restore the final-price calculation.","confidence":0.8}'
    )

    with patch("backend.agents.hypothesis.get_llm", return_value=llm):
        result = generate_hypothesis(LOG_ANALYSIS, RELEVANT_FILES, RECENT_CHANGES)

    assert result["affected_files"] == ["store/pricing.py"]


def test_generate_hypothesis_handles_invalid_json():
    llm = _mock_llm_response("not valid json")

    with patch("backend.agents.hypothesis.get_llm", return_value=llm):
        result = generate_hypothesis(LOG_ANALYSIS, RELEVANT_FILES, RECENT_CHANGES)

    assert result == {
        "root_cause": "Unable to determine the root cause from the available evidence.",
        "affected_files": [],
        "fix_strategy": "Gather more repository and test evidence before proposing a fix.",
        "confidence": 0.0,
    }


def test_generate_hypothesis_requires_valid_confidence_and_fields():
    llm = _mock_llm_response(
        '{"root_cause":"A plausible cause.","affected_files":[],"fix_strategy":"Change it.","confidence":1.5}'
    )

    with patch("backend.agents.hypothesis.get_llm", return_value=llm):
        result = generate_hypothesis(LOG_ANALYSIS, RELEVANT_FILES, RECENT_CHANGES)

    assert result["confidence"] == 0.0
    assert result["root_cause"].startswith("Unable to determine")
