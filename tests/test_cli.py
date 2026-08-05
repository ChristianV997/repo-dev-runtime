import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_run_pr_agent_requires_live_edits_and_explicit_approval(tmp_path, capsys):
    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--enable-pr-agent",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)
    assert code == 1
    assert payload["reason"] == "pr_agent_requires_live_apply_edits"

    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--live", "--apply-edits", "--enable-pr-agent",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)
    assert code == 1
    assert payload["reason"] == "pr_agent_requires_explicit_approval"


def test_live_aider_edits_require_an_independent_reviewer_provider(tmp_path, capsys):
    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--live", "--enable-aider", "--apply-edits",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)

    assert code == 1
    assert payload["reason"] == "aider_requires_routable_core_and_independent_reviewer"

    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--live", "--enable-aider", "--enable-ollama", "--apply-edits",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)
    assert code == 1
    assert payload["reason"] == "aider_requires_routable_core_and_independent_reviewer"


def test_live_aider_edits_accept_sidecars_as_the_independent_reviewer(tmp_path, capsys):
    """RoutingPolicy's reviewer preference list includes hermes/deerflow
    (registry.py), so --enable-sidecars alone is a real independent
    reviewer for Aider's final review, same as --enable-omniroute or
    --enable-pr-agent - the gate must not reject this combination."""
    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--live", "--enable-aider", "--enable-ollama",
        "--enable-sidecars", "--apply-edits",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)
    assert payload.get("reason") != "aider_requires_routable_core_and_independent_reviewer"


def test_run_handoff_requires_explicit_vault(tmp_path, capsys):
    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--write-handoff",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)

    assert code == 1
    assert payload["reason"] == "obsidian_vault_required_for_handoff"


def test_run_handoff_writes_only_to_explicit_obsidian_vault(tmp_path, capsys):
    vault = tmp_path / "vault"
    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--write-handoff",
        "--obsidian-vault", str(vault), "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)

    assert code == 0
    assert payload["handoff"]["status"] == "written"
    handoff = Path(payload["handoff"]["path"])
    assert handoff.is_file()
    assert vault.resolve() in handoff.resolve().parents


def test_scheduled_run_requires_paired_state_and_key(tmp_path, capsys):
    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--scheduler-state-file", str(tmp_path / "state.json"),
        "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)
    assert code == 1
    assert payload["reason"] == "scheduler_state_requires_schedule_key"

    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--schedule-key", "nightly",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)
    assert code == 1
    assert payload["reason"] == "schedule_key_requires_scheduler_state"


def test_scheduled_run_records_success_and_skips_completed_key(tmp_path, capsys):
    state_path = tmp_path / "state.json"
    args = [
        "run", str(tmp_path), "--prompt", "inspect", "--scheduler-state-file", str(state_path),
        "--schedule-key", "nightly", "--artifacts-root", str(tmp_path / "artifacts"),
    ]
    code, payload = _run_cli(args, capsys)
    assert code == 0
    assert payload["status"] == "ready_for_human_review"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["nightly"]["status"] == "succeeded"

    code, payload = _run_cli(args, capsys)
    assert code == 0
    assert payload == {"status": "skipped", "reason": "scheduled_task_already_completed", "schedule_key": "nightly"}


def test_scheduled_run_uses_the_atomic_claim_not_a_separate_load_and_update(tmp_path, capsys, monkeypatch):
    """Regression test: the scheduler wiring used to call
    state_store.load() and state_store.update(..., "running") as two
    separate lock acquisitions - a TOCTOU window where two concurrent
    invocations sharing --schedule-key could both observe the task as
    unclaimed and both proceed. It must go through the single atomic
    claim() call instead."""
    from repo_dev_runtime.scheduler import TaskStateStore

    calls = []
    real_claim = TaskStateStore.claim

    def spying_claim(self, *args, **kwargs):
        calls.append((args, kwargs))
        return real_claim(self, *args, **kwargs)

    monkeypatch.setattr(TaskStateStore, "load", lambda self: (_ for _ in ()).throw(AssertionError("load() must not be called directly; use claim()")))
    monkeypatch.setattr(TaskStateStore, "claim", spying_claim)

    state_path = tmp_path / "state.json"
    code, payload = _run_cli([
        "run", str(tmp_path), "--prompt", "inspect", "--scheduler-state-file", str(state_path),
        "--schedule-key", "nightly", "--artifacts-root", str(tmp_path / "artifacts"),
    ], capsys)

    assert code == 0
    assert payload["status"] == "ready_for_human_review"
    assert len(calls) == 1
    assert calls[0][0][:2] == ("nightly", "running")


def test_cli_enable_openclaw_flag_is_wired_into_default_registry(tmp_path, monkeypatch):
    """Regression test: cli.py had no --enable-openclaw flag at all, so
    default_registry() was always called with openclaw_enabled unset
    (None) regardless of user intent - OpenClaw was reachable nowhere
    from the CLI. This does not change OpenClaw's own fail-closed
    behavior (it still blocks internally); it only proves the flag now
    reaches default_registry()."""
    captured = {}
    real_default_registry = cli.default_registry

    def spying_default_registry(**kwargs):
        captured.update(kwargs)
        return real_default_registry(**kwargs)

    monkeypatch.setattr(cli, "default_registry", spying_default_registry)

    cli.main([
        "run", str(tmp_path), "--prompt", "inspect", "--live", "--enable-openclaw",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ])

    assert captured.get("openclaw_enabled") is True


def test_cli_enable_aider_flag_is_wired_into_default_registry(tmp_path, monkeypatch):
    captured = {}
    real_default_registry = cli.default_registry

    def spying_default_registry(**kwargs):
        captured.update(kwargs)
        return real_default_registry(**kwargs)

    monkeypatch.setattr(cli, "default_registry", spying_default_registry)
    cli.main([
        "run", str(tmp_path), "--prompt", "inspect", "--live", "--enable-aider",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ])
    assert captured.get("aider_enabled") is True


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


@pytest.mark.parametrize("provider", ["hermes", "deerflow"])
def test_cli_benchmark_accepts_hermes_and_deerflow_providers(provider, tmp_path, capsys, fast_benchmark):
    """Regression test: --provider choices lagged the runtime registry -
    hermes/deerflow were already routable via `run --live --enable-sidecars`
    but had no first-class --provider option here, forcing operators to
    fall back to --provider-module for a class they could otherwise select
    directly."""
    code, result = _run_cli(["benchmark", "--provider", provider, "--fixtures-root", str(tmp_path)], capsys)
    assert code == 1
    assert result["reason"] == "real_provider_requires_live"


@pytest.mark.parametrize("provider", ["hermes", "deerflow"])
def test_cli_benchmark_hermes_and_deerflow_select_the_matching_runtime(provider, tmp_path, monkeypatch):
    """--provider hermes/deerflow must route to that runtime's own class,
    not silently fall through to a different adapter."""
    from repo_dev_runtime.runtimes.sidecars import DeerFlowRuntime, HermesRuntime

    expected_class = {"hermes": HermesRuntime, "deerflow": DeerFlowRuntime}[provider]
    captured = {}
    real_default_registry = cli.default_registry

    def spying_default_registry(**kwargs):
        captured.update(kwargs)
        return real_default_registry(**kwargs)

    monkeypatch.setattr(cli, "default_registry", spying_default_registry)

    cli.main([
        "benchmark", "--provider", provider, "--live",
        "--approve-external-provider-benchmark", "--fixtures-root", str(tmp_path),
        "--fixture", "one_file_bugfix",
    ])

    assert captured.get(f"{provider}_enabled") is True
    registry = real_default_registry(**captured)
    assert isinstance(registry.get(provider), expected_class)


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

