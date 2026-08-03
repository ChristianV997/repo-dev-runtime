"""Deterministic five-role development workflow."""
from __future__ import annotations

import uuid
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contracts.models import DevResult, DevTask
from .governance.artifacts import RunEnvelope
from .governance.policy import RuntimePolicy
from .manifest import RepoManifest
from .tools.runner import run_command
from .runtimes.registry import RuntimeRouter
from .workspaces import WorktreeManager
from .integrations.github import GitHubPublisher
from .context import build_repository_context
from .contracts.models import sha256_json


ROLES = ("planner", "implementer", "tester", "reviewer", "integrator")


@dataclass(frozen=True)
class WorkflowResult:
    run_id: str
    status: str
    results: tuple[DevResult, ...]
    artifact_dir: str


class DevelopmentWorkflow:
    def __init__(self, *, manifest: RepoManifest, policy: RuntimePolicy, runtime: object, artifacts_root: str | Path | None = None) -> None:
        manifest.validate()
        policy.validate()
        self.manifest = manifest
        self.policy = policy
        self.runtime = runtime
        self.artifacts_root = Path(artifacts_root or Path(manifest.root) / ".dev-runtime" / "runs")

    def run(self, *, prompt: str, base_ref: str = "HEAD", dry_run: bool = True, run_id: str | None = None, resume: bool = False, approved: bool = False, publisher: GitHubPublisher | None = None, create_pr: bool = False) -> WorkflowResult:
        run_id = run_id or uuid.uuid4().hex
        run_dir = self.artifacts_root / run_id
        if resume and not run_dir.exists():
            raise FileNotFoundError(f"run does not exist: {run_id}")
        if not resume and run_dir.exists():
            raise FileExistsError(f"run already exists: {run_id}")
        envelope = RunEnvelope(run_id, run_dir)
        envelope.event("workflow_started", repository=self.manifest.name, dry_run=dry_run, resume=resume)
        results: list[DevResult] = []
        previous: list[str] = []
        worktree_path = self.manifest.root
        worktree = None
        if not dry_run:
            try:
                worktree = WorktreeManager(self.manifest.root).create(run_id=run_id, base_ref=base_ref)
                worktree_path = str(worktree.path)
                envelope.event("worktree_created", path=worktree_path, branch=worktree.branch)
            except Exception as exc:
                envelope.write_json("promotion.json", {"status": "blocked", "reason": "worktree_creation_failed", "error_type": type(exc).__name__})
                envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name})
                return WorkflowResult(run_id, "blocked", (), str(run_dir))
        repository_context = build_repository_context(worktree_path, allowed_paths=self.manifest.allowed_paths, forbidden_paths=self.manifest.forbidden_paths, max_bytes=self.manifest.context_max_bytes)
        envelope.event("context_captured", context_hash=sha256_json(repository_context), context_bytes=len(repository_context.encode("utf-8")))
        for role in ROLES:
            role_path = run_dir / f"{role}.json"
            if resume and role_path.exists():
                cached = DevResult(**json.loads(role_path.read_text(encoding="utf-8")))
                cached.validate()
                if cached.status in {"succeeded", "skipped"}:
                    results.append(cached)
                    previous.append(cached.task_id)
                    envelope.event("task_resumed", task_id=cached.task_id, role=role, status=cached.status)
                    continue
            context = "\n\nPrevious role output:\n" + results[-1].output[-4_000:] if results else ""
            role_prompt = f"Role: {role}\nYou are one stage in a governed coding workflow. Do not claim commands or tests you did not run. Do not edit files directly.\n\nObjective:\n{prompt}\n\nRepository context:\n{repository_context}{context}"
            task = DevTask.create(repository=worktree_path, base_ref=base_ref, role=role, prompt=role_prompt, acceptance=("return a structured result",), allowed_paths=self.manifest.allowed_paths, dry_run=dry_run, approval_state="approved" if approved else "not_required")
            envelope.event("task_started", task_id=task.task_id, role=role, task_hash=task.task_hash, parents=previous)
            envelope.write_json("checkpoint.json", {"run_id": run_id, "role": role, "status": "running", "task_id": task.task_id})
            if isinstance(self.runtime, RuntimeRouter):
                result = self.runtime.execute(task, approved=approved)
            else:
                result = self.runtime.execute(task)  # type: ignore[attr-defined]
            result.validate()
            results.append(result)
            envelope.write_json(f"{role}.json", result.to_dict())
            envelope.event("task_finished", task_id=task.task_id, role=role, status=result.status)
            envelope.write_json("checkpoint.json", {"run_id": run_id, "role": role, "status": result.status, "task_id": task.task_id})
            if result.status not in {"succeeded", "skipped"}:
                envelope.write_json("promotion.json", {"status": "blocked", "reason": f"{role}_failed"})
                envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name})
                if worktree is not None:
                    WorktreeManager(self.manifest.root).remove(worktree)
                return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
            previous.append(task.task_id)
        quality = run_quality_checks(self.manifest, cwd=worktree_path, dry_run=dry_run)
        envelope.write_json("quality.json", quality)
        if quality["status"] != "passed":
            envelope.write_json("promotion.json", {"status": "blocked", "reason": "quality_checks_failed", "quality": quality})
            envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name, "roles": list(ROLES)})
            if worktree is not None:
                WorktreeManager(self.manifest.root).remove(worktree)
            return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
        pr: dict[str, object] = {}
        if create_pr:
            if dry_run or publisher is None:
                envelope.write_json("promotion.json", {"status": "blocked", "reason": "pr_requires_live_publisher"})
                if worktree is not None:
                    WorktreeManager(self.manifest.root).remove(worktree)
                return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
            try:
                pr = publisher.create_from_worktree(worktree=worktree_path, repository=self.manifest.root, branch=worktree.branch if worktree else f"repo-dev/{run_id}", base=base_ref, title=f"repo-dev: {prompt[:72]}", body=f"Generated by repo-dev-runtime run `{run_id}`.\n\nReview artifacts: `{run_dir}`.")
            except Exception as exc:
                envelope.write_json("promotion.json", {"status": "blocked", "reason": "pr_creation_failed", "error_type": type(exc).__name__, "error_message": str(exc)[:500]})
                envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name, "roles": list(ROLES)})
                if worktree is not None:
                    WorktreeManager(self.manifest.root).remove(worktree)
                return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
        envelope.write_json("promotion.json", {"status": "pr_created" if pr else "ready_for_human_review", "merge": False, "pr_creation": bool(pr), "pull_request": pr})
        envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "ready_for_human_review", "repository": self.manifest.name, "roles": list(ROLES)})
        if worktree is not None:
            WorktreeManager(self.manifest.root).remove(worktree)
        return WorkflowResult(run_id, "ready_for_human_review", tuple(results), str(run_dir))


def run_tests(manifest: RepoManifest, *, timeout_s: float = 120.0) -> dict[str, object]:
    result = run_command(manifest.test_command, cwd=manifest.root, timeout_s=timeout_s, network_access=manifest.network_access)
    return {"command": list(result.command), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": result.timed_out}


def run_quality_checks(manifest: RepoManifest, *, cwd: str, dry_run: bool) -> dict[str, object]:
    """Run every manifest-declared check and fail closed on any non-zero result."""
    commands = [
        ("tests", manifest.test_command),
        ("lint", manifest.lint_command),
        ("security", manifest.security_command),
    ]
    checks: dict[str, object] = {}
    failed = False
    for name, command in commands:
        if not command:
            checks[name] = {"status": "not_configured"}
            continue
        if dry_run:
            checks[name] = {"status": "dry_run", "command": list(command)}
            continue
        try:
            result = run_command(command, cwd=cwd, timeout_s=manifest.check_timeout_s, network_access=manifest.network_access)
            item = {"status": "passed" if result.returncode == 0 and not result.timed_out else "failed", "command": list(command), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": result.timed_out}
        except Exception as exc:
            item = {"status": "failed", "command": list(command), "error_type": type(exc).__name__, "error_message": str(exc)[:500]}
        checks[name] = item
        failed = failed or item["status"] == "failed"
    return {"status": "failed" if failed else "passed", "checks": checks}
