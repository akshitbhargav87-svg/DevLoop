from unittest.mock import Mock, patch

from backend.agents.file_identification import identify_files


FILE_LISTING = "store/pricing.py\nstore/checkout.py\ntests/test_checkout.py"
STACK_TRACE = "AssertionError: Expected 75.0, got 25.0\n  File \"tests/test_checkout.py\", line 8"


def _mock_llm_response(content: str) -> Mock:
    response = Mock()
    response.content = content
    llm = Mock()
    llm.invoke.return_value = response
    return llm


def test_identify_files_returns_ranked_repository_paths():
    llm = _mock_llm_response(
        '{"files": ['
        '{"path":"store/pricing.py","relevance_reason":"Contains the calculation implicated by the failure.","rank":1},'
        '{"path":"store/checkout.py","relevance_reason":"Passes the calculated amount to payment.","rank":2}'
        ']} '
    )

    with patch("backend.agents.file_identification.get_llm", return_value=llm):
        result = identify_files(STACK_TRACE, FILE_LISTING)

    assert result == {
        "files": [
            {
                "path": "store/pricing.py",
                "relevance_reason": "Contains the calculation implicated by the failure.",
                "rank": 1,
            },
            {
                "path": "store/checkout.py",
                "relevance_reason": "Passes the calculated amount to payment.",
                "rank": 2,
            },
        ]
    }
    prompt = llm.invoke.call_args.args[0]
    assert STACK_TRACE in prompt
    assert FILE_LISTING in prompt
    assert "LogAnalysisAgent output" in prompt


def test_identify_files_discards_hallucinated_paths_and_normalizes_ranks():
    llm = _mock_llm_response(
        '{"files": ['
        '{"path":"missing.py","relevance_reason":"invented file","rank":1},'
        '{"path":"store/checkout.py","relevance_reason":" Checkout path\\n is in the stack.","rank":4},'
        '{"path":"store/pricing.py","relevance_reason":"Formula is likely wrong.","rank":2},'
        '{"path":"store/pricing.py","relevance_reason":"Duplicate.","rank":3}'
        ']}'
    )

    with patch("backend.agents.file_identification.get_llm", return_value=llm):
        result = identify_files(STACK_TRACE, FILE_LISTING)

    assert result == {
        "files": [
            {"path": "store/pricing.py", "relevance_reason": "Formula is likely wrong.", "rank": 1},
            {"path": "store/checkout.py", "relevance_reason": "Checkout path is in the stack.", "rank": 2},
        ]
    }


def test_identify_files_handles_invalid_json():
    llm = _mock_llm_response("not valid json")

    with patch("backend.agents.file_identification.get_llm", return_value=llm):
        result = identify_files(STACK_TRACE, FILE_LISTING)

    assert result == {"files": []}


def test_identify_files_skips_llm_for_empty_listing():
    with patch("backend.agents.file_identification.get_llm") as get_llm:
        result = identify_files(STACK_TRACE, "\n  \n")

    assert result == {"files": []}
    get_llm.assert_not_called()
