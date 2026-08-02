"""Deterministic five-role development workflow."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contracts.models import DevResult, DevTask
from .governance.artifacts import RunEnvelope
from .governance.policy import RuntimePolicy
from .manifest import RepoManifest
from .tools.runner import run_command
from .workspaces import WorktreeManager


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

    def run(self, *, prompt: str, base_ref: str = "HEAD", dry_run: bool = True) -> WorkflowResult:
        run_id = uuid.uuid4().hex
        run_dir = self.artifacts_root / run_id
        envelope = RunEnvelope(run_id, run_dir)
        envelope.event("workflow_started", repository=self.manifest.name, dry_run=dry_run)
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
        for role in ROLES:
            task = DevTask.create(repository=worktree_path, base_ref=base_ref, role=role, prompt=prompt, acceptance=("return a structured result",), allowed_paths=self.manifest.allowed_paths, dry_run=dry_run)
            envelope.event("task_started", task_id=task.task_id, role=role, task_hash=task.task_hash, parents=previous)
            result = self.runtime.execute(task)  # type: ignore[attr-defined]
            result.validate()
            results.append(result)
            envelope.write_json(f"{role}.json", result.to_dict())
            envelope.event("task_finished", task_id=task.task_id, role=role, status=result.status)
            if result.status not in {"succeeded", "skipped"}:
                envelope.write_json("promotion.json", {"status": "blocked", "reason": f"{role}_failed"})
                envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name})
                if worktree is not None:
                    WorktreeManager(self.manifest.root).remove(worktree)
                return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
            previous.append(task.task_id)
        # Integrator may prepare a PR artifact, but policy always rejects merge.
        envelope.write_json("promotion.json", {"status": "ready_for_human_review", "merge": False, "pr_creation": self.policy.allow_pr_creation})
        envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "ready_for_human_review", "repository": self.manifest.name, "roles": list(ROLES)})
        if worktree is not None:
            WorktreeManager(self.manifest.root).remove(worktree)
        return WorkflowResult(run_id, "ready_for_human_review", tuple(results), str(run_dir))


def run_tests(manifest: RepoManifest, *, timeout_s: float = 120.0) -> dict[str, object]:
    result = run_command(manifest.test_command, cwd=manifest.root, timeout_s=timeout_s, network_access=manifest.network_access)
    return {"command": list(result.command), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": result.timed_out}
