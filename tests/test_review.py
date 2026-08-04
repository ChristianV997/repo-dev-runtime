import pytest

from repo_dev_runtime.review import ReviewValidationError, parse_review_verdict


def test_structured_review_verdict() -> None:
    verdict = parse_review_verdict('{"schema":"RepoDev.ReviewVerdict.v1","approved":true,"summary":"safe","findings":[]}')
    assert verdict.approved
    with pytest.raises(ReviewValidationError):
        parse_review_verdict('{"approved":"yes","summary":"unsafe"}')


def test_bare_code_fence_and_non_mapping_finding_raise_the_declared_contract_error():
    """Regression test: a bare "```" satisfied both startswith and endswith
    (IndexError on the fence strip), and a non-mapping finding made
    set(raw) raise TypeError. Both escaped this parser's declared
    ReviewValidationError contract for malformed provider output, which
    conformance.assert_reviewer_contract treats as a hard violation."""
    with pytest.raises(ReviewValidationError):
        parse_review_verdict("```")
    with pytest.raises(ReviewValidationError):
        parse_review_verdict("``````")
    with pytest.raises(ReviewValidationError):
        parse_review_verdict('{"schema":"RepoDev.ReviewVerdict.v1","approved":true,"summary":"s","findings":[123]}')
