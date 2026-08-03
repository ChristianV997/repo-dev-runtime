"""Tests for repo_dev_runtime.eval.report."""
from __future__ import annotations

import json

from repo_dev_runtime.eval.fakes import default_fake_provider_factory
from repo_dev_runtime.eval.fixtures import FIXTURE_CASES
from repo_dev_runtime.eval.harness import aggregate_scorecard, run_fixture_benchmark
from repo_dev_runtime.eval.provider_specs import default_provider_specs
from repo_dev_runtime.eval.report import render_json_report, render_json_report_text, render_markdown_report


def _fake_reviewer_reject(request):
    from repo_dev_runtime.eval.models import EvalResult

    return EvalResult(request_id=request.request_id, provider="fake_pr_agent", status="succeeded", normalized={"approved": False, "findings": []})


def _build_results(tmp_path):
    results = run_fixture_benchmark(
        FIXTURE_CASES, make_provider=default_fake_provider_factory, provider_name="fake",
        reviewer_adapter=_fake_reviewer_reject, tmp_root=tmp_path, max_fix_attempts=1,
    )
    scorecard = aggregate_scorecard("fake", results)
    return results, scorecard


def test_json_report_is_valid_json_and_contains_expected_sections(tmp_path):
    results, scorecard = _build_results(tmp_path)
    report = render_json_report(scorecards=[scorecard], fixture_results=results, provider_specs=default_provider_specs())

    assert report["schema"] == "RepoDev.BenchmarkReport.v1"
    assert len(report["scorecards"]) == 1
    assert len(report["fixture_results"]) == 7
    assert len(report["provider_specs"]) == 2
    # round-trips through json.dumps cleanly
    json.dumps(report)


def test_json_report_text_is_canonical_and_parseable(tmp_path):
    results, scorecard = _build_results(tmp_path)
    text = render_json_report_text(scorecards=[scorecard], fixture_results=results)

    parsed = json.loads(text)
    assert parsed["schema"] == "RepoDev.BenchmarkReport.v1"


def test_markdown_report_restates_governance_guarantees(tmp_path):
    results, scorecard = _build_results(tmp_path)
    markdown = render_markdown_report(scorecards=[scorecard], fixture_results=results, provider_specs=default_provider_specs())

    assert "never pushes, merges, or creates a pull request" in markdown
    assert "credential-free by default" in markdown
    assert "never part of default routing" in markdown
    assert "fake" in markdown
    assert "one_file_bugfix" in markdown


def test_report_generation_is_deterministic_given_same_inputs(tmp_path_factory):
    # Timestamps, git hashes, and timing naturally vary run to run; the
    # outcome classification (what the benchmark is actually for) must not.
    results1, scorecard1 = _build_results(tmp_path_factory.mktemp("a"))
    results2, scorecard2 = _build_results(tmp_path_factory.mktemp("b"))

    def _normalize_fixture_result(item):
        stable = {k: v for k, v in item.items() if k not in {"created_at", "before_git_head", "after_git_head", "elapsed_ms", "output_bytes", "raw_output_redacted", "test_result"}}
        # test_result's stdout embeds pytest's own non-deterministic timing
        # text ("1 passed in 0.01s"); only its outcome classification matters here.
        if item.get("test_result"):
            stable["test_result_status"] = item["test_result"].get("status")
        return stable

    def _stable_shape(report):
        return (
            [
                {k: v for k, v in item.items() if k not in {"created_at", "total_duration_ms", "total_output_bytes"}}
                for item in report["scorecards"]
            ],
            [_normalize_fixture_result(item) for item in report["fixture_results"]],
        )

    report1 = render_json_report(scorecards=[scorecard1], fixture_results=results1)
    report2 = render_json_report(scorecards=[scorecard2], fixture_results=results2)
    assert _stable_shape(report1) == _stable_shape(report2)
