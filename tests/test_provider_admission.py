from repo_dev_runtime.eval.fixtures import FIXTURE_CASES
from repo_dev_runtime.eval.harness import aggregate_scorecard, run_fixture_benchmark
from repo_dev_runtime.eval.fakes import default_fake_provider_factory
from repo_dev_runtime.eval.models import EvalResult
from repo_dev_runtime.governance.provider_admission import evaluate_limited_pilot_admission


def _real_reviewer(request):
    return EvalResult(
        request_id=request.request_id,
        provider="independent_reviewer",
        status="succeeded",
        normalized={"schema": "RepoDev.ReviewVerdict.v1", "approved": False, "summary": "unsafe", "findings": []},
    )


def test_admission_blocks_partial_synthetic_evidence(tmp_path):
    case = next(item for item in FIXTURE_CASES if item.fixture_id == "one_file_bugfix")
    results = run_fixture_benchmark((case,), make_provider=default_fake_provider_factory, provider_name="fake", tmp_root=tmp_path)
    scorecard = aggregate_scorecard("fake", results, provider_metadata={"benchmark_kind": "synthetic", "reviewer_kind": "none"})

    decision = evaluate_limited_pilot_admission(scorecard, results)

    assert decision.status == "blocked"
    assert "requires_live_provider_benchmark" in decision.reasons
    assert "missing_fixture:multi_file_change" in decision.reasons


def test_admission_allows_only_complete_live_reviewed_fixture_evidence(tmp_path):
    results = run_fixture_benchmark(
        FIXTURE_CASES,
        make_provider=default_fake_provider_factory,
        provider_name="fake",
        reviewer_adapter=_real_reviewer,
        tmp_root=tmp_path,
        max_fix_attempts=1,
    )
    scorecard = aggregate_scorecard(
        "fake",
        results,
        provider_metadata={"benchmark_kind": "live_provider", "reviewer_kind": "real"},
        expected_outcomes={case.fixture_id: case.expected_outcome for case in FIXTURE_CASES},
    )

    decision = evaluate_limited_pilot_admission(scorecard, results)

    assert decision.status == "limited_pilot_allowed"
    assert decision.reasons == ()
    assert len(decision.evidence_hash) == 64
