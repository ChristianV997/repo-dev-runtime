import json
import os
import subprocess
import sys

import pytest

from repo_dev_runtime import cli
from repo_dev_runtime.eval.models import EvalRequest, FixtureCaseResult


def _run_cli(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


@pytest.fixture
def fast_benchmark(monkeypatch):
    """Keep CLI option tests fast; fixture isolation is covered separately.

    The dedicated benchmark-regression test and the CI benchmark step run the
    real worktree harness. These tests only need to validate CLI policy,
    provider wiring, and report construction.
    """
    def run_fixture_benchmark(cases, *, provider_name, reviewer_adapter=None, **_kwargs):
        results = []
        for case in cases:
            outcome = case.expected_outcome
            reviewer_approved = None
            reviewer_findings = ()
            if case.needs_reviewer and reviewer_adapter is not None:
                verdict = reviewer_adapter(EvalRequest.create(kind="reviewer", objective=case.prompt))
                if verdict.status == "succeeded":
                    reviewer_approved = bool(verdict.normalized["approved"])
                    reviewer_findings = tuple(verdict.normalized.get("findings", ()))
                    outcome = "succeeded" if reviewer_approved else "reviewer_rejected"
            results.append(FixtureCaseResult(
                fixture_id=case.fixture_id,
                provider=provider_name,
                outcome=outcome,
                before_git_head="fixture-head",
                after_git_head="fixture-head",
                proposal_valid=case.expected_outcome not in {"provider_failure", "safely_rejected"},
                reviewer_approved=reviewer_approved,
                reviewer_findings=reviewer_findings,
            ))
        return tuple(results)

    monkeypatch.setattr(cli, "run_fixture_benchmark", run_fixture_benchmark)


def test_cli_dry_run_is_provider_independent(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "run", str(tmp_path), "--prompt", "inspect", "--artifacts-root", str(tmp_path / "artifacts")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "ready_for_human_review"


def test_cli_reports_invalid_live_edit_resume_as_structured_block(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "run", str(tmp_path),
            "--prompt", "resume edit", "--live", "--apply-edits", "--resume",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["reason"] == "workflow_request_invalid"
    assert "run does not exist" in payload["detail"]


def test_cli_benchmark_defaults_to_fake_provider(tmp_path, capsys, fast_benchmark):
    code, report = _run_cli(["benchmark", "--fixtures-root", str(tmp_path)], capsys)
    assert code == 0
    assert report["schema"] == "RepoDev.BenchmarkReport.v1"
    assert report["scorecards"][0]["provider"] == "fake_coding_provider"
    assert report["scorecards"][0]["provider_metadata"]["benchmark_kind"] == "synthetic"
    assert report["scorecards"][0]["provider_metadata"]["reviewer_kind"] == "fake"
    assert len(report["fixture_results"]) == 7


def test_cli_benchmark_fixture_selection_is_explicit_and_bounded(tmp_path, capsys, fast_benchmark):
    code, report = _run_cli([
        "benchmark", "--fixture", "one_file_bugfix", "--fixtures-root", str(tmp_path),
    ], capsys)

    assert code == 0
    assert [item["fixture_id"] for item in report["fixture_results"]] == ["one_file_bugfix"]
    assert report["scorecards"][0]["tasks_attempted"] == 1


def test_cli_benchmark_real_provider_requires_live(tmp_path, capsys, fast_benchmark):
    code, result = _run_cli(["benchmark", "--provider", "ollama", "--fixtures-root", str(tmp_path)], capsys)
    assert code == 1
    assert result["reason"] == "real_provider_requires_live"


def test_cli_enable_openhands_and_mini_swe_agent_attach_blocked_specs(tmp_path, capsys, fast_benchmark):
    code, report = _run_cli([
        "benchmark", "--enable-openhands", "--enable-mini-swe-agent", "--fixtures-root", str(tmp_path),
    ], capsys)
    assert code == 0
    specs = {spec["provider"]: spec for spec in report["provider_specs"]}
    assert set(specs) == {"openhands", "mini_swe_agent"}
    assert all(spec["evaluation_status"] == "blocked" for spec in specs.values())


def test_cli_provider_metadata_json_invalid_json_is_rejected(tmp_path):
    fixtures_root = tmp_path / "fixtures"
    result = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--provider-metadata-json", "not json", "--fixtures-root", str(fixtures_root)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "provider_metadata_json_invalid"
    assert not fixtures_root.exists()


def test_cli_provider_metadata_json_non_object_is_rejected(tmp_path):
    fixtures_root = tmp_path / "fixtures"
    result = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--provider-metadata-json", "[1, 2, 3]", "--fixtures-root", str(fixtures_root)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "provider_metadata_json_must_be_an_object"
    assert not fixtures_root.exists()


def test_cli_provider_metadata_json_valid_object_is_recorded(tmp_path, capsys, fast_benchmark):
    code, report = _run_cli([
        "benchmark", "--provider-metadata-json", '{"version": "1.2.3"}', "--fixtures-root", str(tmp_path),
    ], capsys)
    assert code == 0
    assert report["scorecards"][0]["provider_metadata"]["version"] == "1.2.3"


def test_cli_markdown_out_writes_a_file(tmp_path, capsys, fast_benchmark):
    markdown_path = tmp_path / "report.md"
    code, _ = _run_cli([
        "benchmark", "--markdown-out", str(markdown_path), "--fixtures-root", str(tmp_path / "fixtures"),
    ], capsys)
    assert code == 0
    assert markdown_path.exists()
    assert "Provider Benchmark Report" in markdown_path.read_text(encoding="utf-8")


def test_cli_history_out_appends_across_runs(tmp_path, capsys, fast_benchmark):
    history_path = tmp_path / "history.jsonl"
    for _ in range(2):
        code, _ = _run_cli([
            "benchmark", "--history-out", str(history_path), "--fixtures-root", str(tmp_path / "fixtures"),
        ], capsys)
        assert code == 0

    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        assert json.loads(line)["schema"] == "RepoDev.BenchmarkReport.v1"


def test_cli_max_fix_attempts_out_of_bounds_is_rejected(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--max-fix-attempts", "4", "--fixtures-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "max_fix_attempts_out_of_bounds"

    negative = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--max-fix-attempts", "-1", "--fixtures-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert negative.returncode == 1
    assert json.loads(negative.stdout)["reason"] == "max_fix_attempts_out_of_bounds"


def test_cli_task_timeout_out_of_bounds_is_rejected(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--task-timeout-s", "901", "--fixtures-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "task_timeout_out_of_bounds"


def test_cli_provider_module_conflicts_with_explicit_provider(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "benchmark",
            "--provider-module", "tests.sample_external_provider:SampleExternalProvider",
            "--provider", "ollama", "--fixtures-root", str(tmp_path),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "provider_module_conflicts_with_provider"


def test_cli_provider_module_with_default_provider_is_not_a_conflict(tmp_path, capsys, fast_benchmark):
    # --provider defaults to "fake"; only an explicitly-set, differing
    # --provider alongside --provider-module should be treated as a conflict.
    code, _ = _run_cli([
        "benchmark", "--provider-module", "tests.sample_external_provider:SampleExternalProvider",
        "--live", "--approve-external-provider-benchmark", "--fixtures-root", str(tmp_path),
    ], capsys)
    assert code == 0


def test_cli_enable_pr_agent_conflicts_with_fake_reviewer(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "benchmark",
            "--enable-pr-agent", "--fake-reviewer", "--fixtures-root", str(tmp_path),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "enable_pr_agent_conflicts_with_fake_reviewer"


def test_cli_provider_module_live_requires_explicit_approval(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "benchmark",
            "--provider-module", "tests.sample_external_provider:SampleExternalProvider",
            "--live", "--fixtures-root", str(tmp_path),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "explicit approval" in json.loads(result.stdout)["reason"]


def test_cli_provider_module_live_with_approval_runs(tmp_path, capsys, fast_benchmark):
    code, report = _run_cli([
        "benchmark", "--provider-module", "tests.sample_external_provider:SampleExternalProvider",
        "--live", "--approve-external-provider-benchmark", "--fixtures-root", str(tmp_path),
    ], capsys)
    assert code == 0
    assert report["scorecards"][0]["provider"] == "sample_external_provider"


def test_cli_reviewer_disagreement_is_reachable(tmp_path, capsys, fast_benchmark):
    # A reviewer that approves the "reviewer_should_reject" fixture disagrees
    # with that fixture's declared expected_outcome (reviewer_rejected) —
    # this is the case that was structurally impossible to reach before
    # expected_outcomes was wired into the CLI's aggregate_scorecard call.
    reviewer_script = tmp_path / "approving_reviewer.py"
    reviewer_script.write_text(
        "import sys, json\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'schema': 'RepoDev.ReviewVerdict.v1', 'approved': True, 'summary': 'looks fine', 'findings': []}))\n"
    )
    code, report = _run_cli([
        "benchmark", "--enable-pr-agent", "--pr-agent-command", f"{sys.executable} {reviewer_script}",
        "--live", "--approve-external-provider-benchmark", "--fixtures-root", str(tmp_path / "fixtures"),
    ], capsys)
    assert code == 0
    assert report["scorecards"][0]["reviewer_disagreement_count"] >= 1


def test_cli_enable_pr_agent_requires_live_and_approval(tmp_path):
    without_live = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--enable-pr-agent", "--fixtures-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert without_live.returncode == 1
    assert json.loads(without_live.stdout)["reason"] == "real_provider_requires_live"

    without_approval = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--enable-pr-agent", "--live", "--fixtures-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert without_approval.returncode == 1
    assert "explicit approval" in json.loads(without_approval.stdout)["reason"]


def test_cli_enable_pr_agent_requires_a_configured_command_before_fixture_creation(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "benchmark",
            "--enable-pr-agent", "--live", "--approve-external-provider-benchmark",
            "--fixtures-root", str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PR_AGENT_COMMAND": ""},
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "pr_agent_command_not_configured"
    assert not list(tmp_path.iterdir())

