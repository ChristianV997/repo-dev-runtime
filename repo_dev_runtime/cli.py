from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from .manifest import load_manifest
from .discovery import probe_repository, validate_consumer
from .runtimes.ollama import OllamaRuntime
from .runtimes.openai_compatible import OpenAICompatibleRuntime
from .runtimes.factory import default_registry
from .runtimes.registry import RuntimeRouter
from .runtimes.dry_run import DryRunRuntime
from .governance.policy import RuntimePolicy
from .workflow import DevelopmentWorkflow
from .integrations.github import GitHubPublisher
from .eval.fakes import default_fake_provider_factory
from .eval.fixtures import FIXTURE_CASES
from .eval.harness import aggregate_scorecard, run_fixture_benchmark
from .eval.provider_specs import default_provider_specs
from .eval.report import render_json_report, render_markdown_report


def main() -> int:
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
    run.add_argument("--enable-omniroute", action="store_true")
    run.add_argument("--enable-sidecars", action="store_true")
    run.add_argument("--approve-paid", action="store_true")
    run.add_argument("--create-pr", action="store_true")
    run.add_argument("--apply-edits", action="store_true", help="accept only validated implementer proposals in a disposable worktree")
    run.add_argument("--max-fix-attempts", type=int, default=0, help="bounded repair proposals after failed quality checks (0-3)")
    run.add_argument("--artifacts-root")
    benchmark = sub.add_parser("benchmark", help="run the deterministic fixture benchmark against a coding-agent provider")
    benchmark.add_argument("--fixtures-root", help="temp directory root for synthetic fixture repos (defaults to the OS temp dir)")
    benchmark.add_argument("--provider", choices=["fake", "ollama", "openai_compatible"], default="fake")
    benchmark.add_argument("--live", action="store_true", help="required to run a real (non-fake) provider")
    benchmark.add_argument("--enable-pr-agent", action="store_true", help="opt in to the disabled-by-default PR-Agent reviewer bridge (still requires its own command/credential to be configured)")
    benchmark.add_argument("--enable-openhands", action="store_true", help="prep-only: emits a blocked BenchmarkProviderSpec, never installs or executes OpenHands")
    benchmark.add_argument("--enable-mini-swe-agent", action="store_true", help="prep-only: emits a blocked BenchmarkProviderSpec, never installs or executes mini-SWE-agent")
    benchmark.add_argument("--max-fix-attempts", type=int, default=1)
    benchmark.add_argument("--json-out")
    benchmark.add_argument("--markdown-out")
    args = parser.parse_args()
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
            allow_omniroute=allow_omniroute,
            allow_paid_routing=allow_paid,
            allow_pr_creation=args.create_pr and manifest.pull_request_creation,
            allow_branch_publish=args.create_pr and manifest.pull_request_creation,
            network_access=args.live,
        )
        if args.create_pr and not manifest.pull_request_creation:
            print(json.dumps({"status": "blocked", "reason": "manifest_disables_pull_request_creation"}, indent=2))
            return 1
        if args.apply_edits and not args.live:
            print(json.dumps({"status": "blocked", "reason": "apply_edits_requires_live"}, indent=2))
            return 1
        artifacts_root = Path(args.artifacts_root).expanduser() if args.artifacts_root else Path.home() / ".repo-dev-runtime" / "runs" / manifest.name
        runtime = RuntimeRouter(default_registry(ollama_enabled=args.enable_ollama if args.live else None, omniroute_enabled=args.enable_omniroute if args.live else None, hermes_enabled=args.enable_sidecars if args.live else None, deerflow_enabled=args.enable_sidecars if args.live else None), policy=policy) if args.live else DryRunRuntime()
        publisher = GitHubPublisher(policy=policy) if args.create_pr else None
        result = DevelopmentWorkflow(manifest=manifest, policy=policy, runtime=runtime, artifacts_root=artifacts_root).run(prompt=args.prompt, base_ref=args.base_ref, dry_run=not args.live, run_id=args.run_id, resume=args.resume, approved=args.approve_paid, publisher=publisher, create_pr=args.create_pr, apply_edits=args.apply_edits, max_fix_attempts=args.max_fix_attempts)
        print(json.dumps({"run_id": result.run_id, "status": result.status, "artifact_dir": result.artifact_dir, "results": [item.to_dict() for item in result.results]}, indent=2))
        return 0 if result.status in {"ready_for_human_review", "pr_created"} else 1
    if args.command == "benchmark":
        return _run_benchmark(args)
    health = {name: value.__dict__ for name, value in default_registry().health().items()}
    print(json.dumps(health, indent=2, default=str))
    return 0


def _run_benchmark(args) -> int:
    tmp_root = Path(args.fixtures_root).expanduser().resolve() if args.fixtures_root else Path(tempfile.gettempdir()) / "repo-dev-runtime-benchmark"
    tmp_root.mkdir(parents=True, exist_ok=True)

    if args.provider == "fake":
        provider_name = "fake_coding_provider"
        make_provider = default_fake_provider_factory
    else:
        if not args.live:
            print(json.dumps({"status": "blocked", "reason": "real_provider_requires_live"}, indent=2))
            return 1
        policy = RuntimePolicy(
            allow_ollama=args.provider == "ollama", allow_omniroute=args.provider == "openai_compatible",
            network_access=True, allow_external_provider_benchmark=True,
        )
        try:
            policy.authorize("external_provider_benchmark")
        except PermissionError as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, indent=2))
            return 1
        registry = default_registry(ollama_enabled=args.provider == "ollama" or None, omniroute_enabled=args.provider == "openai_compatible" or None)
        runtime = registry.get(args.provider)
        provider_name = args.provider

        def make_provider(case, _runtime=runtime):
            return _runtime

    reviewer_adapter = None
    if args.enable_pr_agent:
        from .eval.pr_agent import PRAgentReviewAdapter

        reviewer_adapter = PRAgentReviewAdapter(enabled=True).review

    results = run_fixture_benchmark(FIXTURE_CASES, make_provider=make_provider, provider_name=provider_name, reviewer_adapter=reviewer_adapter, tmp_root=tmp_root, max_fix_attempts=args.max_fix_attempts)
    scorecard = aggregate_scorecard(provider_name, results)

    provider_specs = []
    if args.enable_openhands or args.enable_mini_swe_agent:
        specs = {spec.provider: spec for spec in default_provider_specs()}
        if args.enable_openhands:
            provider_specs.append(specs["openhands"])
        if args.enable_mini_swe_agent:
            provider_specs.append(specs["mini_swe_agent"])

    json_report = render_json_report(scorecards=[scorecard], fixture_results=results, provider_specs=provider_specs)
    markdown_report = render_markdown_report(scorecards=[scorecard], fixture_results=results, provider_specs=provider_specs)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(json_report, indent=2), encoding="utf-8")
    else:
        print(json.dumps(json_report, indent=2))
    if args.markdown_out:
        Path(args.markdown_out).write_text(markdown_report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
