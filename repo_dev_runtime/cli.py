from __future__ import annotations

import argparse
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Sequence

from .manifest import load_manifest
from .discovery import probe_repository, validate_consumer
from .runtimes.factory import default_registry
from .runtimes.registry import RuntimeRouter
from .runtimes.dry_run import DryRunRuntime
from .runtimes.pr_agent_reviewer import PRAgentReviewerRuntime
from .governance.policy import RuntimePolicy
from .workflow import DevelopmentWorkflow
from .scheduler import TaskStateStore
from .integrations.github import GitHubPublisher
from .integrations.obsidian import ObsidianHandoff
from .handoff import render_handoff
from .eval.fakes import FakePRAgentAdapter, default_fake_provider_factory
from .eval.fixtures import FIXTURE_CASES
from .eval.harness import aggregate_scorecard, run_fixture_benchmark
from .eval.loader import ProviderLoadError, load_provider
from .eval.provider_specs import default_provider_specs
from .eval.report import append_history, render_json_report, render_markdown_report
from .governance.provider_admission import evaluate_limited_pilot_admission


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI.

    ``argv`` keeps the console entry point unchanged while letting tests
    exercise parsing and policy wiring without spawning a full fixture
    benchmark process for every flag combination.
    """
    parser = argparse.ArgumentParser(prog="repo-dev-runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("path", nargs="?", default=".")
    init = sub.add_parser("init-manifest")
    init.add_argument("path", nargs="?", default=".")
    health = sub.add_parser("health")
    health.add_argument("--json", action="store_true")
    consumers = sub.add_parser("validate-consumers")
    consumers.add_argument("paths", nargs="+", help="repository paths to inspect without changing")
    run = sub.add_parser("run")
    run.add_argument("path", nargs="?", default=".")
    run.add_argument("--prompt", required=True)
    run.add_argument("--base-ref", default="main")
    run.add_argument("--run-id")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--live", action="store_true")
    run.add_argument("--enable-ollama", action="store_true")
    run.add_argument("--enable-aider", action="store_true", help="enable the sandboxed Aider implementer; requires a separate planner/reviewer provider")
    run.add_argument("--enable-omniroute", action="store_true")
    run.add_argument("--enable-sidecars", action="store_true")
    run.add_argument("--enable-openclaw", action="store_true", help="reach the OpenClaw sidecar adapter; still fails closed (blocked) in v1 since its WebSocket client is unimplemented")
    run.add_argument("--enable-pr-agent", action="store_true", help="use the configured PR-Agent bridge as the final independent reviewer; requires --live, --apply-edits, and --approve-external-review")
    run.add_argument("--approve-external-review", action="store_true", help="explicit per-run approval for the opt-in external PR-Agent reviewer")
    run.add_argument("--pr-agent-command", help="reviewer executable/command; overrides PR_AGENT_COMMAND")
    run.add_argument("--pr-agent-required-credential", help="environment variable name required by the PR-Agent bridge")
    run.add_argument("--approve-paid", action="store_true")
    run.add_argument("--create-pr", action="store_true")
    run.add_argument("--apply-edits", action="store_true", help="accept only validated implementer proposals in a disposable worktree")
    run.add_argument("--max-fix-attempts", type=int, default=0, help="bounded repair proposals after failed quality checks (0-3)")
    run.add_argument("--artifacts-root")
    run.add_argument("--write-handoff", action="store_true", help="write a redacted, one-way Markdown run handoff to an explicitly selected Obsidian vault")
    run.add_argument("--obsidian-vault", help="Obsidian vault root required by --write-handoff")
    run.add_argument("--scheduler-state-file", help="atomic state file for an externally scheduled one-shot invocation; requires --schedule-key")
    run.add_argument("--schedule-key", help="stable task identifier for --scheduler-state-file; completed keys are not rerun unless explicitly requested")
    run.add_argument("--rerun-completed", action="store_true", help="allow an explicitly scheduled completed key to execute again")
    benchmark = sub.add_parser("benchmark", help="run the deterministic fixture benchmark against a coding-agent provider")
    benchmark.add_argument("--fixtures-root", help="temp directory root for synthetic fixture repos (defaults to the OS temp dir)")
    benchmark.add_argument("--fixture", action="append", choices=[case.fixture_id for case in FIXTURE_CASES], help="run only a named fixture; repeat to select several (default: all fixtures)")
    benchmark.add_argument("--provider", choices=["fake", "ollama", "openai_compatible", "hermes", "deerflow"], default="fake")
    benchmark.add_argument("--provider-module", help="benchmark an externally-defined provider, given as 'package.module:ClassName'; must implement the DevelopmentRuntime protocol. Requires --live.")
    benchmark.add_argument("--provider-name", help="scorecard name for --provider-module (defaults to the provider's own .name)")
    benchmark.add_argument("--live", action="store_true", help="required to run a real (non-fake) provider")
    benchmark.add_argument("--approve-external-provider-benchmark", action="store_true", help="explicit per-run approval required (alongside --live) to execute a real coding provider or the PR-Agent reviewer bridge")
    benchmark.add_argument("--enable-pr-agent", action="store_true", help="opt in to the disabled-by-default PR-Agent reviewer bridge (still requires its own command/credential to be configured)")
    benchmark.add_argument("--fake-reviewer", action="store_true", help="use a deterministic rejecting reviewer so the reviewer fixture is exercised without an external tool; contract testing only, never evidence about a real reviewer")
    benchmark.add_argument("--pr-agent-command", help="reviewer executable/command; overrides the PR_AGENT_COMMAND environment variable")
    benchmark.add_argument("--pr-agent-required-credential", help="environment variable name the reviewer bridge requires; a missing value yields a blocked result")
    benchmark.add_argument("--enable-openhands", action="store_true", help="prep-only: emits a blocked BenchmarkProviderSpec, never installs or executes OpenHands")
    benchmark.add_argument("--enable-mini-swe-agent", action="store_true", help="prep-only: emits a blocked BenchmarkProviderSpec, never installs or executes mini-SWE-agent")
    benchmark.add_argument("--provider-metadata-json", help='JSON object of provider provenance recorded on the scorecard, e.g. \'{"version": "1.2.3", "lock_hash": "...", "python": "3.12", "model": "..."}\'')
    benchmark.add_argument("--max-fix-attempts", type=int, default=1, help="bounded repair proposals per fixture (0-3)")
    benchmark.add_argument("--task-timeout-s", type=float, default=120.0, help="per-provider task timeout for this benchmark (0.001-900 seconds)")
    benchmark.add_argument("--json-out")
    benchmark.add_argument("--markdown-out")
    benchmark.add_argument("--history-out", nargs="?", const="", help="append this run's JSON report as one JSONL line; defaults to ~/.repo-dev-runtime/eval-history/<date>.jsonl when given without a value")
    args = parser.parse_args(argv)
    if args.command == "probe":
        root = Path(args.path).resolve()
        manifest = load_manifest(root)
        print(json.dumps({"manifest": manifest.to_dict(), "capabilities": probe_repository(root)}, indent=2))
        return 0
    if args.command == "init-manifest":
        print(json.dumps(load_manifest(Path(args.path), create_default=True).to_dict(), indent=2))
        return 0
    if args.command == "validate-consumers":
        results = [validate_consumer(path) for path in args.paths]
        print(json.dumps(results, indent=2))
        return 0 if all(item["valid"] for item in results) else 1
    if args.command == "run":
        root = Path(args.path).resolve()
        manifest = load_manifest(root)
        allow_paid = args.approve_paid
        allow_omniroute = args.enable_omniroute or args.enable_sidecars
        policy = RuntimePolicy(
            allow_ollama=args.enable_ollama,
            allow_aider=args.enable_aider,
            allow_omniroute=allow_omniroute,
            allow_openclaw=args.enable_openclaw,
            allow_paid_routing=allow_paid,
            allow_pr_creation=args.create_pr and manifest.pull_request_creation,
            allow_branch_publish=args.create_pr and manifest.pull_request_creation,
            allow_external_provider_benchmark=args.enable_pr_agent,
            network_access=args.live,
        )
        if args.create_pr and not manifest.pull_request_creation:
            print(json.dumps({"status": "blocked", "reason": "manifest_disables_pull_request_creation"}, indent=2))
            return 1
        if args.apply_edits and not args.live:
            print(json.dumps({"status": "blocked", "reason": "apply_edits_requires_live"}, indent=2))
            return 1
        if args.enable_pr_agent and (not args.live or not args.apply_edits):
            print(json.dumps({"status": "blocked", "reason": "pr_agent_requires_live_apply_edits"}, indent=2))
            return 1
        if args.enable_pr_agent and not args.approve_external_review:
            print(json.dumps({"status": "blocked", "reason": "pr_agent_requires_explicit_approval"}, indent=2))
            return 1
        if args.enable_aider and args.apply_edits:
            # Aider supplies only the implementer role. The workflow still
            # needs a general runtime for planner/tester/integrator and a
            # distinct final reviewer. Sidecars cannot fill tester/integrator,
            # and Ollama alone cannot independently review itself.
            has_core_roles = args.enable_ollama or args.enable_omniroute
            has_independent_reviewer = args.enable_omniroute or args.enable_pr_agent
            if not has_core_roles or not has_independent_reviewer:
                print(json.dumps({"status": "blocked", "reason": "aider_requires_routable_core_and_independent_reviewer"}, indent=2))
                return 1
        if args.enable_pr_agent:
            try:
                policy.authorize("pr_agent_review", approved=True)
            except PermissionError as exc:
                print(json.dumps({"status": "blocked", "reason": str(exc)}, indent=2))
                return 1
        if args.write_handoff and not args.obsidian_vault:
            print(json.dumps({"status": "blocked", "reason": "obsidian_vault_required_for_handoff"}, indent=2))
            return 1
        if args.scheduler_state_file and not args.schedule_key:
            print(json.dumps({"status": "blocked", "reason": "scheduler_state_requires_schedule_key"}, indent=2))
            return 1
        if args.schedule_key and not args.scheduler_state_file:
            print(json.dumps({"status": "blocked", "reason": "schedule_key_requires_scheduler_state"}, indent=2))
            return 1
        state_store = None
        if args.scheduler_state_file:
            state_store = TaskStateStore(args.scheduler_state_file)
            prior = state_store.load().get(args.schedule_key)
            if prior and prior.get("status") == "succeeded" and not args.rerun_completed:
                print(json.dumps({"status": "skipped", "reason": "scheduled_task_already_completed", "schedule_key": args.schedule_key}, indent=2))
                return 0
            state_store.update(args.schedule_key, "running", repository=manifest.name, prompt=args.prompt, run_id=args.run_id or "")
        artifacts_root = Path(args.artifacts_root).expanduser() if args.artifacts_root else Path.home() / ".repo-dev-runtime" / "runs" / manifest.name
        runtime = RuntimeRouter(default_registry(ollama_enabled=args.enable_ollama if args.live else None, aider_enabled=args.enable_aider if args.live else None, omniroute_enabled=args.enable_omniroute if args.live else None, hermes_enabled=args.enable_sidecars if args.live else None, deerflow_enabled=args.enable_sidecars if args.live else None, openclaw_enabled=args.enable_openclaw if args.live else None), policy=policy) if args.live else DryRunRuntime()
        reviewer_runtime = None
        if args.enable_pr_agent:
            from .eval.pr_agent import PRAgentReviewAdapter

            adapter = PRAgentReviewAdapter(
                command=shlex.split(args.pr_agent_command, posix=os.name != "nt") if args.pr_agent_command else None,
                enabled=True,
                required_credential=args.pr_agent_required_credential,
                policy=policy,
            )
            health = adapter.health()
            if not health.configured or not health.reachable:
                print(json.dumps({"status": "blocked", "reason": "pr_agent_not_healthy", "detail": health.detail}, indent=2))
                return 1
            reviewer_runtime = PRAgentReviewerRuntime(adapter)
        publisher = GitHubPublisher(policy=policy) if args.create_pr else None
        try:
            result = DevelopmentWorkflow(manifest=manifest, policy=policy, runtime=runtime, reviewer_runtime=reviewer_runtime, artifacts_root=artifacts_root).run(prompt=args.prompt, base_ref=args.base_ref, dry_run=not args.live, run_id=args.run_id, resume=args.resume, approved=args.approve_paid, publisher=publisher, create_pr=args.create_pr, apply_edits=args.apply_edits, max_fix_attempts=args.max_fix_attempts)
        except (FileExistsError, FileNotFoundError, ValueError) as exc:
            if state_store is not None:
                state_store.update(args.schedule_key, "blocked", error_type=type(exc).__name__, detail=str(exc)[:500])
            print(json.dumps({"status": "blocked", "reason": "workflow_request_invalid", "detail": str(exc)}, indent=2))
            return 1
        if state_store is not None:
            state_store.update(
                args.schedule_key,
                "succeeded" if result.status in {"ready_for_human_review", "pr_created"} else "blocked",
                repository=manifest.name,
                run_id=result.run_id,
                workflow_status=result.status,
                artifact_dir=result.artifact_dir,
            )
        handoff = {}
        if args.write_handoff:
            quality_path = Path(result.artifact_dir) / "quality.json"
            try:
                quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
                next_action = "review the generated patch and run envelope" if result.status == "ready_for_human_review" else "inspect the blocked run envelope before retrying"
                content = render_handoff(
                    repository=manifest.name,
                    run_id=result.run_id,
                    status=result.status,
                    next_action=next_action,
                    tests=quality,
                )
                destination = ObsidianHandoff(args.obsidian_vault).write(
                    f"repo-dev-runtime-{manifest.name}-{result.run_id}.md",
                    content,
                    dry_run=False,
                )
                handoff = {"path": str(destination), "status": "written"}
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                # The handoff is a non-authoritative convenience mirror. A
                # failure must be visible but cannot change a completed run's
                # promotion state or mutate its checksum-validated envelope.
                handoff = {"status": "failed", "error_type": type(exc).__name__, "detail": str(exc)[:500]}
        print(json.dumps({"run_id": result.run_id, "status": result.status, "artifact_dir": result.artifact_dir, "handoff": handoff, "results": [item.to_dict() for item in result.results]}, indent=2))
        return 0 if result.status in {"ready_for_human_review", "pr_created"} else 1
    if args.command == "benchmark":
        return _run_benchmark(args)
    health = {name: value.__dict__ for name, value in default_registry().health().items()}
    print(json.dumps(health, indent=2, default=str))
    return 0


def _run_benchmark(args) -> int:
    if not 0 <= args.max_fix_attempts <= 3:
        print(json.dumps({"status": "blocked", "reason": "max_fix_attempts_out_of_bounds"}, indent=2))
        return 1
    if not 0.001 <= args.task_timeout_s <= 900:
        print(json.dumps({"status": "blocked", "reason": "task_timeout_out_of_bounds"}, indent=2))
        return 1
    if args.provider_module and args.provider != "fake":
        print(json.dumps({"status": "blocked", "reason": "provider_module_conflicts_with_provider"}, indent=2))
        return 1
    if args.enable_pr_agent and args.fake_reviewer:
        print(json.dumps({"status": "blocked", "reason": "enable_pr_agent_conflicts_with_fake_reviewer"}, indent=2))
        return 1

    # Reject malformed metadata before creating fixture worktrees. This keeps
    # invalid invocations cheap and guarantees they cannot leave run artifacts.
    provider_metadata = {}
    if args.provider_metadata_json:
        try:
            provider_metadata = json.loads(args.provider_metadata_json)
        except json.JSONDecodeError as exc:
            print(json.dumps({"status": "blocked", "reason": "provider_metadata_json_invalid", "detail": str(exc)}, indent=2))
            return 1
        if not isinstance(provider_metadata, dict):
            print(json.dumps({"status": "blocked", "reason": "provider_metadata_json_must_be_an_object"}, indent=2))
            return 1

    tmp_root = Path(args.fixtures_root).expanduser().resolve() if args.fixtures_root else Path(tempfile.gettempdir()) / "repo-dev-runtime-benchmark"
    tmp_root.mkdir(parents=True, exist_ok=True)

    if args.provider_module:
        if not args.live:
            print(json.dumps({"status": "blocked", "reason": "real_provider_requires_live"}, indent=2))
            return 1
        policy = RuntimePolicy(network_access=True, allow_external_provider_benchmark=True)
        try:
            policy.authorize("external_provider_benchmark", approved=args.approve_external_provider_benchmark)
        except PermissionError as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, indent=2))
            return 1
        try:
            runtime = load_provider(args.provider_module, policy=policy)
        except ProviderLoadError as exc:
            print(json.dumps({"status": "blocked", "reason": "provider_module_not_loadable", "detail": str(exc)}, indent=2))
            return 1
        provider_name = args.provider_name or runtime.name

        def make_provider(case, _runtime=runtime):
            return _runtime

    elif args.provider == "fake":
        provider_name = "fake_coding_provider"
        make_provider = default_fake_provider_factory
    else:
        if not args.live:
            print(json.dumps({"status": "blocked", "reason": "real_provider_requires_live"}, indent=2))
            return 1
        policy = RuntimePolicy(
            allow_ollama=args.provider == "ollama", allow_omniroute=args.provider in ("openai_compatible", "hermes", "deerflow"),
            network_access=True, allow_external_provider_benchmark=True,
        )
        try:
            policy.authorize("external_provider_benchmark", approved=args.approve_external_provider_benchmark)
        except PermissionError as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, indent=2))
            return 1
        registry = default_registry(
            ollama_enabled=args.provider == "ollama" or None,
            omniroute_enabled=args.provider == "openai_compatible" or None,
            hermes_enabled=args.provider == "hermes" or None,
            deerflow_enabled=args.provider == "deerflow" or None,
        )
        runtime = registry.get(args.provider)
        provider_name = args.provider

        def make_provider(case, _runtime=runtime):
            return _runtime

    reviewer_adapter = None
    reviewer_kind = "none"
    if args.enable_pr_agent:
        if not args.live:
            print(json.dumps({"status": "blocked", "reason": "real_provider_requires_live"}, indent=2))
            return 1
        pr_agent_policy = RuntimePolicy(network_access=True, allow_external_provider_benchmark=True)
        try:
            pr_agent_policy.authorize("pr_agent_review", approved=args.approve_external_provider_benchmark)
        except PermissionError as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, indent=2))
            return 1

        from .eval.pr_agent import PRAgentReviewAdapter

        adapter = PRAgentReviewAdapter(
            # POSIX tokenization corrupts Windows executable paths by treating
            # backslashes as escapes. Keep this in sync with the environment
            # parser used by PRAgentReviewAdapter.
            command=shlex.split(args.pr_agent_command, posix=os.name != "nt") if args.pr_agent_command else None,
            enabled=True,
            required_credential=args.pr_agent_required_credential,
            policy=pr_agent_policy,
        )
        health = adapter.health()
        if not health.configured:
            print(json.dumps({"status": "blocked", "reason": "pr_agent_command_not_configured"}, indent=2))
            return 1
        if not health.reachable:
            print(json.dumps({"status": "blocked", "reason": "pr_agent_command_not_found", "detail": health.detail}, indent=2))
            return 1
        reviewer_adapter = adapter.review
        reviewer_kind = "real"
    elif args.fake_reviewer or args.provider == "fake":
        # The deterministic baseline must exercise every fixture, including
        # the independent-review case. Real providers only get this reviewer
        # when explicitly requested with --fake-reviewer.
        reviewer_adapter = FakePRAgentAdapter(
            approved=False,
            findings=[{"severity": "high", "path": "validator.py", "message": "removes required input validation"}],
        )
        reviewer_kind = "fake"

    # Record how this run was produced, so a synthetic run is never later
    # mistaken for evidence about a real provider or a real reviewer.
    provider_metadata = dict(provider_metadata) | {
        "benchmark_kind": "synthetic" if not args.provider_module and args.provider == "fake" else "live_provider",
        "reviewer_kind": reviewer_kind,
    }

    selected_cases = tuple(case for case in FIXTURE_CASES if not args.fixture or case.fixture_id in args.fixture)
    results = run_fixture_benchmark(
        selected_cases,
        make_provider=make_provider,
        provider_name=provider_name,
        reviewer_adapter=reviewer_adapter,
        tmp_root=tmp_root,
        max_fix_attempts=args.max_fix_attempts,
        task_timeout_s=args.task_timeout_s,
    )
    # Each fixture's own expected_outcome is the ground truth for reviewer
    # agreement/disagreement — without this, every case with a reviewer
    # opinion counts as "agreement" and disagreement is structurally always 0.
    expected_outcomes = {case.fixture_id: case.expected_outcome for case in selected_cases}
    scorecard = aggregate_scorecard(provider_name, results, provider_metadata=provider_metadata, expected_outcomes=expected_outcomes)

    provider_specs = []
    if args.enable_openhands or args.enable_mini_swe_agent:
        specs = {spec.provider: spec for spec in default_provider_specs()}
        if args.enable_openhands:
            provider_specs.append(specs["openhands"])
        if args.enable_mini_swe_agent:
            provider_specs.append(specs["mini_swe_agent"])

    admission = evaluate_limited_pilot_admission(scorecard, results)
    json_report = render_json_report(scorecards=[scorecard], fixture_results=results, provider_specs=provider_specs, admission_decisions=[admission])
    markdown_report = render_markdown_report(scorecards=[scorecard], fixture_results=results, provider_specs=provider_specs, admission_decisions=[admission])

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(json_report, indent=2), encoding="utf-8")
    else:
        print(json.dumps(json_report, indent=2))
    if args.markdown_out:
        Path(args.markdown_out).write_text(markdown_report, encoding="utf-8")
    if args.history_out is not None:
        append_history(json_report, path=args.history_out or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
