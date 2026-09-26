from unittest.mock import Mock, patch

from backend.agents.log_analysis import analyze_log


def test_analyze_log_returns_structured_result():
    mock_response = Mock()
    mock_response.content = """
    {
        "exception": "AssertionError",
        "module": "store/checkout.py",
        "line": 42,
        "call_chain": [
            "checkout",
            "calculate_discount"
        ]
    }
    """

    mock_llm = Mock()
    mock_llm.invoke.return_value = mock_response

    with patch(
        "backend.agents.log_analysis.get_llm",
        return_value=mock_llm,
    ):
        result = analyze_log(
            stack_trace="AssertionError at store/checkout.py:42",
            description="Checkout fails when applying a coupon.",
        )

    assert result["exception"] == "AssertionError"
    assert result["module"] == "store/checkout.py"
    assert result["line"] == 42
    assert result["call_chain"] == [
        "checkout",
        "calculate_discount",
    ]


def test_analyze_log_handles_invalid_json():
    mock_response = Mock()
    mock_response.content = "not valid json"

    mock_llm = Mock()
    mock_llm.invoke.return_value = mock_response

    with patch(
        "backend.agents.log_analysis.get_llm",
        return_value=mock_llm,
    ):
        result = analyze_log(
            stack_trace="broken stack trace",
            description="Something failed.",
        )

    assert result == {
        "exception": "Unknown",
        "module": "",
        "line": 0,
        "call_chain": [],
    }