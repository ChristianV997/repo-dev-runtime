"""Tests for repo_dev_runtime.eval.models: ProviderScorecard, FixtureCaseResult,
BenchmarkProviderSpec, EvalRequest/EvalResult."""
from __future__ import annotations

import math

import pytest

from repo_dev_runtime.eval.models import (
    BenchmarkProviderSpec,
    EvalRequest,
    EvalResult,
    FixtureCaseResult,
    ProviderScorecard,
)


def test_scorecard_has_no_single_opaque_score_field():
    scorecard = ProviderScorecard(provider="fake")
    payload = scorecard.to_dict()

    assert "score" not in payload
    assert "quality_score" not in payload
    # every field name is a specific, distinguishable metric
    assert payload["tasks_completed"] == 0
    assert payload["tasks_safely_rejected"] == 0
    assert payload["tasks_failed_provider"] == 0
    assert payload["tasks_blocked_by_policy"] == 0


def test_scorecard_rejects_non_finite_values():
    scorecard = ProviderScorecard(provider="fake", total_duration_ms=math.inf)
    with pytest.raises(ValueError):
        scorecard.validate()


def test_scorecard_rejects_negative_counts():
    scorecard = ProviderScorecard(provider="fake", tasks_attempted=-1)
    with pytest.raises(ValueError):
        scorecard.validate()


def test_fixture_case_result_outcome_vocabulary_enforced():
    result = FixtureCaseResult(
        fixture_id="one_file_bugfix",
        provider="fake",
        outcome="succeeded",
        before_git_head="a" * 40,
        after_git_head="b" * 40,
    )
    result.validate()

    with pytest.raises(ValueError):
        FixtureCaseResult(
            fixture_id="x",
            provider="fake",
            outcome="looks_great",
            before_git_head="a" * 40,
            after_git_head="a" * 40,
        ).validate()


def test_benchmark_provider_spec_requires_reason_when_blocked():
    with pytest.raises(ValueError):
        BenchmarkProviderSpec(provider="openhands", evaluation_status="blocked").validate()

    spec = BenchmarkProviderSpec(
        provider="openhands",
        evaluation_status="blocked",
        blocked_reason="cannot verify without installing untrusted software",
    )
    spec.validate()
    assert spec.to_dict()["evaluation_status"] == "blocked"


def test_eval_request_requires_valid_kind():
    request = EvalRequest.create(kind="reviewer", objective="review a diff", diff="--- a\n+++ b\n")
    assert request.to_dict()["kind"] == "reviewer"

    with pytest.raises(ValueError):
        EvalRequest.create(kind="not_a_kind", objective="x")


def test_eval_result_status_restricted_to_known_vocabulary():
    result = EvalResult(request_id="r1", provider="pr_agent", status="succeeded", normalized={"approved": True})
    result.validate()

    with pytest.raises(ValueError):
        EvalResult(request_id="r1", provider="pr_agent", status="maybe").validate()
