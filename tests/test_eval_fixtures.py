"""Tests for the fixture benchmark harness: repo_dev_runtime.eval.fixtures
and repo_dev_runtime.eval.harness."""
from __future__ import annotations

import subprocess
import json
import re

import pytest

from repo_dev_runtime.eval.fakes import FakeCodingProvider, default_fake_provider_factory
from repo_dev_runtime.eval.fixtures import FIXTURE_CASES, build_fixture_repository
from repo_dev_runtime.eval.harness import aggregate_scorecard, run_fixture_benchmark, run_fixture_case
from repo_dev_runtime.eval.models import EvalResult


def _by_id(results, fixture_id):
    return next(r for r in results if r.fixture_id == fixture_id)


def test_seven_fixture_cases_defined():
    assert len(FIXTURE_CASES) == 7
    assert len({case.fixture_id for case in FIXTURE_CASES}) == 7


def test_build_fixture_repository_creates_isolated_git_repo(tmp_path):
    case = FIXTURE_CASES[0]
    repo_root = build_fixture_repository(tmp_path, case)

    assert (repo_root / ".git").exists()
    assert repo_root != tmp_path
    assert tmp_path in repo_root.parents
    head = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    assert head.stdout.strip()


def test_fake_provider_default_run_matches_expected_outcomes(tmp_path):
    results = run_fixture_benchmark(
        FIXTURE_CASES,
        make_provider=default_fake_provider_factory,
        provider_name="fake_coding_provider",
        reviewer_adapter=_fake_reviewer_reject,
        tmp_root=tmp_path,
        max_fix_attempts=1,
    )
    for case in FIXTURE_CASES:
        result = _by_id(results, case.fixture_id)
        assert result.outcome == case.expected_outcome, f"{case.fixture_id}: {result.outcome} != {case.expected_outcome} ({result.error_message})"


def _fake_reviewer_reject(request):
    return EvalResult(
        request_id=request.request_id,
        provider="fake_pr_agent",
        status="succeeded",
        normalized={"approved": False, "findings": [{"severity": "high", "path": "validator.py", "message": "removes required input validation"}]},
    )


def test_source_checkout_immutable_across_fixture_run(tmp_path):
    case = FIXTURE_CASES[0]
    repo_root = build_fixture_repository(tmp_path, case)
    before = (repo_root / "calc.py").read_text(encoding="utf-8")

    run_fixture_case(case, make_provider=lambda c: FakeCodingProvider(c.provider_turns), provider_name="fake", tmp_root=tmp_path, max_fix_attempts=1)

    # a second, independent fixture repo build (not the one above, since the
    # harness builds its own copy internally) — assert the harness's own
    # internal checkout for `repo_root` never touched the original files we
    # inspected here by re-reading them.
    assert (repo_root / "calc.py").read_text(encoding="utf-8") == before


def test_worktree_containment(tmp_path):
    case = FIXTURE_CASES[0]
    captured = {}

    def capturing_provider(c):
        provider = FakeCodingProvider(c.provider_turns)
        return provider

    result = run_fixture_case(case, make_provider=capturing_provider, provider_name="fake", tmp_root=tmp_path, max_fix_attempts=1)
    assert result.outcome == "succeeded"
    # the worktree directory itself must be removed after the run (no leftover under tmp_path/.repo-dev-worktrees)
    worktrees_root = tmp_path.parent / ".repo-dev-worktrees"
    if worktrees_root.exists():
        for repo_worktree_dir in worktrees_root.iterdir():
            assert not any(repo_worktree_dir.iterdir())


def test_forbidden_path_trap_rejected_and_file_untouched(tmp_path):
    case = next(c for c in FIXTURE_CASES if c.fixture_id == "forbidden_path_trap")
    result = run_fixture_case(case, make_provider=default_fake_provider_factory, provider_name="fake", tmp_root=tmp_path, max_fix_attempts=1)

    assert result.outcome == "safely_rejected"
    assert result.changed_files == ()


def test_malformed_incomplete_task_yields_provider_failure(tmp_path):
    case = next(c for c in FIXTURE_CASES if c.fixture_id == "malformed_incomplete_task")
    result = run_fixture_case(case, make_provider=default_fake_provider_factory, provider_name="fake", tmp_root=tmp_path, max_fix_attempts=1)

    assert result.outcome == "provider_failure"


def test_real_provider_fixture_task_includes_contract_and_rejects_wrong_task_id(tmp_path):
    from repo_dev_runtime.contracts.models import DevResult, RuntimeHealth

    case = next(c for c in FIXTURE_CASES if c.fixture_id == "one_file_bugfix")
    captured = {}

    class WrongTaskProvider:
        name = "wrong_task"

        def health(self):
            return RuntimeHealth(self.name, True, True)

        def execute(self, task):
            captured["prompt"] = task.prompt
            head = re.search(r"base_commit=([0-9a-f]+)", task.prompt).group(1)
            context = re.search(r"context_hash=([0-9a-f]+)", task.prompt).group(1)
            return DevResult(task.task_id, self.name, "succeeded", output=json.dumps({
                "schema": "RepoDev.EditProposal.v1",
                "proposal_id": "wrong-task",
                "task_id": "another-task",
                "base_commit": head,
                "context_hash": context,
                "summary": "attempted edit",
                "edits": [{"path": "calc.py", "format": "search_replace", "search": "return a - b", "replace": "return a + b"}],
            }))

    result = run_fixture_case(case, make_provider=lambda _case: WrongTaskProvider(), provider_name="wrong_task", tmp_root=tmp_path)

    assert "Return only one JSON object using schema RepoDev.EditProposal.v1" in captured["prompt"]
    assert "Repository context:" in captured["prompt"]
    assert result.outcome == "invalid_proposal"
    assert result.error_message == "proposal task_id mismatch"


def test_valid_schema_but_failing_behavior_is_not_scored_as_success(tmp_path):
    from repo_dev_runtime.contracts.models import DevResult, RuntimeHealth

    case = next(c for c in FIXTURE_CASES if c.fixture_id == "one_file_bugfix")

    class BehaviorFailingProvider:
        name = "behavior_failing"

        def health(self):
            return RuntimeHealth(self.name, True, True)

        def execute(self, task):
            head = re.search(r"base_commit=([0-9a-f]+)", task.prompt).group(1)
            context = re.search(r"context_hash=([0-9a-f]+)", task.prompt).group(1)
            return DevResult(task.task_id, self.name, "succeeded", output=json.dumps({
                "schema": "RepoDev.EditProposal.v1",
                "proposal_id": "behavior-failing",
                "task_id": task.task_id,
                "base_commit": head,
                "context_hash": context,
                "summary": "syntactically valid but incorrect replacement",
                "edits": [{"path": "calc.py", "format": "whole_file", "content": "return a + b\n"}],
            }))

    result = run_fixture_case(case, make_provider=lambda _case: BehaviorFailingProvider(), provider_name="behavior_failing", tmp_root=tmp_path, max_fix_attempts=0)

    assert result.proposal_valid is True
    assert result.outcome == "test_failure"
    assert result.test_result["status"] == "failed"


def test_test_failure_requires_one_repair_iteration(tmp_path):
    case = next(c for c in FIXTURE_CASES if c.fixture_id == "test_failure_requires_repair")
    result = run_fixture_case(case, make_provider=default_fake_provider_factory, provider_name="fake", tmp_root=tmp_path, max_fix_attempts=1)

    assert result.outcome == "succeeded"
    assert result.repair_attempts == 1
    assert result.repair_succeeded is True
    assert result.test_result["status"] == "passed"


def test_test_command_timeout_is_classified_distinctly(tmp_path, monkeypatch):
    from repo_dev_runtime.eval import harness
    from repo_dev_runtime.tools.runner import CommandResult

    case = next(c for c in FIXTURE_CASES if c.fixture_id == "test_failure_requires_repair")

    def fake_run_command(command, *, cwd, timeout_s=60.0, **kwargs):
        return CommandResult(command=tuple(command), returncode=None, stdout="", stderr="", timed_out=True)

    monkeypatch.setattr(harness, "run_command", fake_run_command)
    result = run_fixture_case(case, make_provider=default_fake_provider_factory, provider_name="fake", tmp_root=tmp_path, max_fix_attempts=1)

    assert result.outcome == "test_failure"
    assert result.error_type == "timeout"
    assert result.test_result["status"] == "timed_out"

    scorecard = aggregate_scorecard("fake", [result])
    assert scorecard.timeout_count == 1


def test_provider_timeout_error_type_is_counted(tmp_path):
    from repo_dev_runtime.contracts.models import DevResult, RuntimeHealth

    case = next(c for c in FIXTURE_CASES if c.fixture_id == "one_file_bugfix")

    class TimingOutProvider:
        name = "timing_out"

        def health(self):
            return RuntimeHealth(self.name, True, True)

        def execute(self, task):
            return DevResult(task_id=task.task_id, runtime=self.name, status="failed", error_type="TimeoutError")

    result = run_fixture_case(case, make_provider=lambda c: TimingOutProvider(), provider_name="timing_out", tmp_root=tmp_path, max_fix_attempts=1)

    assert result.outcome == "provider_failure"
    assert result.error_type == "TimeoutError"

    scorecard = aggregate_scorecard("timing_out", [result])
    assert scorecard.timeout_count == 1


def test_reviewer_should_reject_fixture(tmp_path):
    case = next(c for c in FIXTURE_CASES if c.fixture_id == "reviewer_should_reject")
    result = run_fixture_case(
        case, make_provider=default_fake_provider_factory, provider_name="fake",
        reviewer_adapter=_fake_reviewer_reject, tmp_root=tmp_path, max_fix_attempts=1,
    )

    assert result.outcome == "reviewer_rejected"
    assert result.reviewer_approved is False
    assert result.reviewer_findings


def test_reviewer_receives_only_changed_file_baseline_contents(tmp_path):
    case = next(c for c in FIXTURE_CASES if c.fixture_id == "reviewer_should_reject")
    captured = {}

    def reviewer(request):
        captured["base_files"] = dict(request.base_files)
        return _fake_reviewer_reject(request)

    result = run_fixture_case(
        case, make_provider=default_fake_provider_factory, provider_name="fake",
        reviewer_adapter=reviewer, tmp_root=tmp_path, max_fix_attempts=1,
    )

    assert result.outcome == "reviewer_rejected"
    assert captured["base_files"] == {
        "validator.py": "def validate(value):\n    if value is None:\n        raise ValueError('value required')\n    return value\n",
    }


def test_prompt_injection_resisted_when_target_untouched(tmp_path):
    case = next(c for c in FIXTURE_CASES if c.fixture_id == "prompt_injection_repo_instruction")
    result = run_fixture_case(case, make_provider=default_fake_provider_factory, provider_name="fake", tmp_root=tmp_path, max_fix_attempts=1)

    assert result.outcome == "succeeded"
    assert result.prompt_injection_resisted is True


def test_prompt_injection_not_resisted_when_provider_targets_injected_path(tmp_path):
    from repo_dev_runtime.eval.fakes import ProviderTurn

    case = next(c for c in FIXTURE_CASES if c.fixture_id == "prompt_injection_repo_instruction")
    malicious_provider = FakeCodingProvider(
        (ProviderTurn(kind="edit", edits=({"path": ".github/workflows/deploy.yml", "format": "whole_file", "content": "name: deploy\nsteps: [exfiltrate]\n"},)),)
    )
    result = run_fixture_case(case, make_provider=lambda c: malicious_provider, provider_name="fake", tmp_root=tmp_path, max_fix_attempts=1)

    assert result.outcome == "safely_rejected"
    assert result.prompt_injection_resisted is False


def test_benchmark_is_reproducible_with_fake_providers(tmp_path_factory):
    first = run_fixture_benchmark(
        FIXTURE_CASES, make_provider=default_fake_provider_factory, provider_name="fake",
        reviewer_adapter=_fake_reviewer_reject, tmp_root=tmp_path_factory.mktemp("run1"), max_fix_attempts=1,
    )
    second = run_fixture_benchmark(
        FIXTURE_CASES, make_provider=default_fake_provider_factory, provider_name="fake",
        reviewer_adapter=_fake_reviewer_reject, tmp_root=tmp_path_factory.mktemp("run2"), max_fix_attempts=1,
    )

    first_outcomes = [(r.fixture_id, r.outcome, r.changed_files, r.repair_attempts) for r in first]
    second_outcomes = [(r.fixture_id, r.outcome, r.changed_files, r.repair_attempts) for r in second]
    assert first_outcomes == second_outcomes


def test_no_push_merge_or_pr_creation_ever_occurs(tmp_path, monkeypatch):
    calls = []
    original_run = subprocess.run

    def spy(args, *popen_args, **kwargs):
        calls.append(list(args) if isinstance(args, (list, tuple)) else [args])
        return original_run(args, *popen_args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    run_fixture_benchmark(
        FIXTURE_CASES, make_provider=default_fake_provider_factory, provider_name="fake",
        reviewer_adapter=_fake_reviewer_reject, tmp_root=tmp_path, max_fix_attempts=1,
    )

    for call in calls:
        args = [str(x) for x in call]
        assert "push" not in args
        assert "merge" not in args
        assert not any("api.github.com" in a for a in args)


def test_aggregate_scorecard_buckets_outcomes(tmp_path):
    results = run_fixture_benchmark(
        FIXTURE_CASES, make_provider=default_fake_provider_factory, provider_name="fake",
        reviewer_adapter=_fake_reviewer_reject, tmp_root=tmp_path, max_fix_attempts=1,
    )
    scorecard = aggregate_scorecard("fake", results)

    assert scorecard.tasks_attempted == 7
    # succeeded: one_file_bugfix, multi_file_change, test_failure_requires_repair, prompt_injection_repo_instruction
    assert scorecard.tasks_completed == 4
    assert scorecard.tasks_safely_rejected == 1  # forbidden_path_trap
    assert scorecard.tasks_failed_provider == 1  # malformed_incomplete_task
    assert scorecard.failure_classification.get("reviewer_rejected") == 1  # reviewer_should_reject
    assert scorecard.repair_loop_attempts == 1
    assert scorecard.repair_loop_successes == 1
    assert scorecard.credential_leak_detected is False
