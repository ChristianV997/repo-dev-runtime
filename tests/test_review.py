import pytest

from repo_dev_runtime.review import ReviewValidationError, parse_review_verdict


def test_structured_review_verdict() -> None:
    verdict = parse_review_verdict('{"schema":"RepoDev.ReviewVerdict.v1","approved":true,"summary":"safe","findings":[]}')
    assert verdict.approved
    with pytest.raises(ReviewValidationError):
        parse_review_verdict('{"approved":"yes","summary":"unsafe"}')
