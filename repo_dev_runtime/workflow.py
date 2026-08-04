"""Deterministic five-role development workflow."""
from __future__ import annotations

import uuid
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts.models import DevResult, DevTask
from .governance.artifacts import RunEnvelope
from .governance.credentials import redact_json
from .governance.policy import RuntimePolicy
from .manifest import RepoManifest
from .tools.runner import run_command
from .runtimes.registry import RuntimeRouter
from .workspaces import WorktreeManager
from .integrations.github import GitHubPublisher
from .context import build_adaptive_context
from .contracts.models import sha256_json
from .edits import PatchApplier, PatchValidationError, parse_edit_proposal
from .review import ReviewValidationError, parse_review_verdict


ROLES = ("planner", "implementer", "tester", "reviewer", "integrator")
_REPLAY_ARTIFACT = "patch_replay.jsonl"
_REPLAY_SCHEMA = "RepoDev.PatchReplayRecord.v1"


def _write_artifact(envelope: RunEnvelope, name: str, payload: object) -> None:
    """Persist only redacted run data; execution keeps the original value."""
    envelope.write_json(name, redact_json(payload))


def _append_replay_record(envelope: RunEnvelope, proposal: object, *, context_hash: str, applied: object, source: str) -> None:
    """Persist an exact, replayable proposal only when it is safe to retain.

    Artifacts are normally redacted. A replay ledger needs the original edit
    text, so credential-shaped proposal content is rejected rather than
    storing an altered patch which could later be replayed incorrectly.
    """
    proposal_payload = proposal.to_dict()  # type: ignore[attr-defined]
    if redact_json(proposal_payload) != proposal_payload:
        raise PatchValidationError("proposal contains credential-shaped content and cannot be replayed")
    record = {
        "schema": _REPLAY_SCHEMA,
        "proposal": proposal_payload,
        "proposal_hash": proposal.proposal_hash,  # type: ignore[attr-defined]
        "context_hash": context_hash,
        "changed_files": list(applied.changed_files),  # type: ignore[attr-defined]
        "after_hashes": dict(applied.after_hashes),  # type: ignore[attr-defined]
        "source": source,
    }
    path = envelope.root / _REPLAY_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")


def _replay_applied_proposals(envelope: RunEnvelope, *, worktree_path: str, manifest: RepoManifest) -> int:
    """Reapply only finalized, checksum-covered patch records to a new worktree."""
    path = envelope.root / _REPLAY_ARTIFACT
    if not path.exists():
        return 0
    envelope.verify_checksums(required=(_REPLAY_ARTIFACT,))
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PatchValidationError("patch replay artifact is invalid JSON") from exc
        if not isinstance(record, dict) or set(record) != {"schema", "proposal", "proposal_hash", "context_hash", "changed_files", "after_hashes", "source"}:
            raise PatchValidationError("patch replay artifact has invalid fields")
        if record["schema"] != _REPLAY_SCHEMA or not isinstance(record["context_hash"], str) or not isinstance(record["proposal_hash"], str):
            raise PatchValidationError("patch replay artifact has invalid metadata")
        records.append(record)
    applier = PatchApplier(worktree_path, allowed_paths=manifest.allowed_paths, forbidden_paths=manifest.forbidden_paths)
    for index, record in enumerate(records, start=1):
        proposal = parse_edit_proposal(json.dumps(record["proposal"], sort_keys=True))
        if proposal.proposal_hash != record["proposal_hash"]:
            raise PatchValidationError("patch replay proposal hash mismatch")
        applied = applier.apply(proposal, context_hash=record["context_hash"])
        if list(applied.changed_files) != record["changed_files"] or dict(applied.after_hashes) != record["after_hashes"]:
            raise PatchValidationError("patch replay result mismatch")
        envelope.event("proposal_replayed", proposal_hash=applied.proposal_hash, sequence=index, changed_files=list(applied.changed_files))
    return len(records)


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

    def run(self, *, prompt: str, base_ref: str = "HEAD", dry_run: bool = True, run_id: str | None = None, resume: bool = False, approved: bool = False, publisher: GitHubPublisher | None = None, create_pr: bool = False, apply_edits: bool = False, max_fix_attempts: int = 0) -> WorkflowResult:
        if apply_edits and dry_run:
            raise ValueError("apply_edits requires a live disposable worktree")
        if not 0 <= max_fix_attempts <= 3:
            raise ValueError("max_fix_attempts must be between 0 and 3")
        # A consumer manifest may request network-capable quality commands,
        # but it can never elevate a runtime policy that denies networking.
        # Dry runs do not execute commands, so they remain available offline.
        if not dry_run and self.manifest.network_access:
            self.policy.authorize("network")
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
                if resume and apply_edits:
                    replayed = _replay_applied_proposals(envelope, worktree_path=worktree_path, manifest=self.manifest)
                    envelope.event("patch_replay_completed", count=replayed)
            except Exception as exc:
                _write_artifact(envelope, "promotion.json", {"status": "blocked", "reason": "worktree_creation_or_patch_replay_failed", "error_type": type(exc).__name__})
                envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name})
                if worktree is not None:
                    WorktreeManager(self.manifest.root).remove(worktree)
                return WorkflowResult(run_id, "blocked", (), str(run_dir))
        repository_context, repository_map = build_adaptive_context(worktree_path, objective=prompt, allowed_paths=self.manifest.allowed_paths, forbidden_paths=self.manifest.forbidden_paths, max_bytes=self.manifest.context_max_bytes)
        context_hash = sha256_json(repository_context)
        _write_artifact(envelope, "repository_map.txt", {"map": repository_map})
        envelope.event("context_captured", context_hash=context_hash, context_bytes=len(repository_context.encode("utf-8")))
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
            task_id = uuid.uuid4().hex
            contract = ""
            if apply_edits and role == "implementer":
                head = subprocess.check_output(["git", "-C", worktree_path, "rev-parse", "HEAD"], text=True).strip()
                contract = f"\n\nReturn only one JSON object using schema RepoDev.EditProposal.v1. It must contain proposal_id, task_id={task_id}, base_commit={head}, context_hash={context_hash}, summary, and non-empty edits. Each edit is search_replace or whole_file and must be within allowed paths. Do not use markdown fences."
            if apply_edits and role == "reviewer":
                contract = "\n\nReturn only one JSON object using schema RepoDev.ReviewVerdict.v1 with approved, summary, and findings. Do not use markdown fences. Approval is permitted only when the diff is safe and the stated quality evidence is adequate."
            role_prompt = f"Role: {role}\nYou are one stage in a governed coding workflow. Do not claim commands or tests you did not run. Do not edit files directly.{contract}\n\nObjective:\n{prompt}\n\nRepository context:\n{repository_context}{context}"
            task = DevTask(task_id=task_id, repository=worktree_path, base_ref=base_ref, role=role, prompt=role_prompt, acceptance=("return a structured result",), allowed_paths=self.manifest.allowed_paths, dry_run=dry_run, approval_state="approved" if approved else "not_required")
            task.validate()
            envelope.event("task_started", task_id=task.task_id, role=role, task_hash=task.task_hash, parents=previous)
            _write_artifact(envelope, "checkpoint.json", {"run_id": run_id, "role": role, "status": "running", "task_id": task.task_id})
            if isinstance(self.runtime, RuntimeRouter):
                result = self.runtime.execute(task, approved=approved)
            else:
                result = self.runtime.execute(task)  # type: ignore[attr-defined]
            result.validate()
            if apply_edits and role == "implementer" and result.status == "succeeded":
                try:
                    proposal = parse_edit_proposal(result.output)
                    if proposal.task_id != task.task_id:
                        raise PatchValidationError("proposal task_id mismatch")
                    applied = PatchApplier(worktree_path, allowed_paths=self.manifest.allowed_paths, forbidden_paths=self.manifest.forbidden_paths).apply(proposal, context_hash=context_hash)
                    _append_replay_record(envelope, proposal, context_hash=context_hash, applied=applied, source="implementer")
                    _write_artifact(envelope, "proposal.json", proposal.to_dict())
                    _write_artifact(envelope, "applied_patch.json", {"proposal_hash": applied.proposal_hash, "checkpoint_id": applied.checkpoint_id, "changed_files": list(applied.changed_files), "before_hashes": dict(applied.before_hashes), "after_hashes": dict(applied.after_hashes)})
                    result = DevResult(result.task_id, result.runtime, result.status, result.output, applied.changed_files, result.commit_sha, result.tests, result.telemetry)
                    envelope.event("proposal_applied", task_id=result.task_id, proposal_hash=proposal.proposal_hash, changed_files=list(applied.changed_files))
                except (PatchValidationError, UnicodeDecodeError) as exc:
                    envelope.event("proposal_rejected", task_id=result.task_id, error_type=type(exc).__name__)
                    repaired = False
                    for repair_attempt in range(max_fix_attempts):
                        repair_task_id = uuid.uuid4().hex
                        repair_head = subprocess.check_output(["git", "-C", worktree_path, "rev-parse", "HEAD"], text=True).strip()
                        repair_prompt = f"Role: implementer\nReturn only valid JSON for RepoDev.EditProposal.v1. Repair the malformed proposal. task_id={repair_task_id}; base_commit={repair_head}; context_hash={context_hash}. Required fields: proposal_id, task_id, base_commit, context_hash, summary, edits. Each edit requires path, format, and format-specific fields.\n\nValidation error: {str(exc)[:500]}\nMalformed response:\n{result.output[:3000]}"
                        repair_task = DevTask(task_id=repair_task_id, repository=worktree_path, base_ref=base_ref, role="implementer", prompt=repair_prompt, acceptance=("return a valid repair proposal",), allowed_paths=self.manifest.allowed_paths, dry_run=False)
                        repair_task.validate()
                        candidate = self.runtime.execute(repair_task, approved=approved) if isinstance(self.runtime, RuntimeRouter) else self.runtime.execute(repair_task)  # type: ignore[attr-defined]
                        try:
                            candidate_proposal = parse_edit_proposal(candidate.output)
                            if candidate_proposal.task_id != repair_task.task_id:
                                raise PatchValidationError("proposal task_id mismatch")
                            candidate_applied = PatchApplier(worktree_path, allowed_paths=self.manifest.allowed_paths, forbidden_paths=self.manifest.forbidden_paths).apply(candidate_proposal, context_hash=context_hash)
                            _append_replay_record(envelope, candidate_proposal, context_hash=context_hash, applied=candidate_applied, source="proposal_repair")
                            result = DevResult(candidate.task_id, candidate.runtime, "succeeded", candidate.output, candidate_applied.changed_files, telemetry=candidate.telemetry)
                            _write_artifact(envelope, "proposal.json", candidate_proposal.to_dict())
                            _write_artifact(envelope, "applied_patch.json", {"proposal_hash": candidate_applied.proposal_hash, "checkpoint_id": candidate_applied.checkpoint_id, "changed_files": list(candidate_applied.changed_files), "before_hashes": dict(candidate_applied.before_hashes), "after_hashes": dict(candidate_applied.after_hashes)})
                            envelope.event("proposal_repaired", task_id=repair_task.task_id, attempt=repair_attempt + 1, changed_files=list(candidate_applied.changed_files))
                            task = repair_task
                            repaired = True
                            break
                        except (PatchValidationError, UnicodeDecodeError) as repair_exc:
                            envelope.event("proposal_repair_rejected", task_id=repair_task.task_id, attempt=repair_attempt + 1, error_type=type(repair_exc).__name__)
                    if not repaired:
                        result = DevResult(result.task_id, result.runtime, "blocked", result.output, error_type=type(exc).__name__, error_message=str(exc))
            if apply_edits and role == "reviewer" and result.status == "succeeded":
                try:
                    verdict = parse_review_verdict(result.output)
                    _write_artifact(envelope, "review_verdict.json", verdict.to_dict())
                    if not verdict.approved:
                        result = DevResult(result.task_id, result.runtime, "blocked", result.output, error_type="review_not_approved", error_message=verdict.summary)
                    envelope.event("review_recorded", task_id=result.task_id, approved=verdict.approved, findings=len(verdict.findings))
                except ReviewValidationError as exc:
                    result = DevResult(result.task_id, result.runtime, "blocked", result.output, error_type=type(exc).__name__, error_message=str(exc))
            results.append(result)
            _write_artifact(envelope, f"{role}.json", result.to_dict())
            envelope.event("task_finished", task_id=task.task_id, role=role, status=result.status)
            _write_artifact(envelope, "checkpoint.json", {"run_id": run_id, "role": role, "status": result.status, "task_id": task.task_id})
            if result.status not in {"succeeded", "skipped"}:
                _write_artifact(envelope, "promotion.json", {"status": "blocked", "reason": f"{role}_failed"})
                envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name})
                if worktree is not None:
                    WorktreeManager(self.manifest.root).remove(worktree)
                return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
            previous.append(task.task_id)
        quality = run_quality_checks(self.manifest, cwd=worktree_path, dry_run=dry_run, policy=self.policy)
        repairs_applied = False
        for attempt in range(max_fix_attempts):
            if quality["status"] == "passed":
                break
            if not apply_edits:
                break
            retry_context, retry_map = build_adaptive_context(worktree_path, objective=prompt + " repair failed quality checks", allowed_paths=self.manifest.allowed_paths, forbidden_paths=self.manifest.forbidden_paths, max_bytes=self.manifest.context_max_bytes)
            retry_hash = sha256_json(retry_context)
            head = subprocess.check_output(["git", "-C", worktree_path, "rev-parse", "HEAD"], text=True).strip()
            repair_task_id = uuid.uuid4().hex
            repair_prompt = f"Role: implementer\nReturn only RepoDev.EditProposal.v1 JSON. Repair these quality failures, using a minimal patch. task_id={repair_task_id}; base_commit={head}; context_hash={retry_hash}.\n\nQuality:\n{json.dumps(quality, sort_keys=True)[:8000]}\n\nContext:\n{retry_context}"
            task = DevTask(task_id=repair_task_id, repository=worktree_path, base_ref=base_ref, role="implementer", prompt=repair_prompt, acceptance=("return a structured repair proposal",), allowed_paths=self.manifest.allowed_paths, dry_run=False)
            task.validate()
            result = self.runtime.execute(task, approved=approved) if isinstance(self.runtime, RuntimeRouter) else self.runtime.execute(task)  # type: ignore[attr-defined]
            try:
                proposal = parse_edit_proposal(result.output)
                if proposal.task_id != task.task_id:
                    raise PatchValidationError("proposal task_id mismatch")
                applied = PatchApplier(worktree_path, allowed_paths=self.manifest.allowed_paths, forbidden_paths=self.manifest.forbidden_paths).apply(proposal, context_hash=retry_hash)
                _append_replay_record(envelope, proposal, context_hash=retry_hash, applied=applied, source="quality_repair")
                result = DevResult(result.task_id, result.runtime, "succeeded", result.output, applied.changed_files, telemetry=result.telemetry)
                _write_artifact(envelope, f"repair_{attempt + 1}.json", result.to_dict())
                envelope.event("repair_applied", task_id=result.task_id, attempt=attempt + 1, changed_files=list(applied.changed_files))
                results.append(result)
                repairs_applied = True
            except (PatchValidationError, ReviewValidationError, UnicodeDecodeError) as exc:
                envelope.event("repair_rejected", task_id=result.task_id, attempt=attempt + 1, error_type=type(exc).__name__)
                break
            quality = run_quality_checks(self.manifest, cwd=worktree_path, dry_run=False, policy=self.policy)
        if repairs_applied and quality["status"] == "passed":
            review_task_id = uuid.uuid4().hex
            review_prompt = f"Role: reviewer\nReturn only RepoDev.ReviewVerdict.v1 JSON with approved, summary, and findings. Review the final worktree diff after repair proposals and the passed quality result.\n\nObjective:\n{prompt}\n\nQuality:\n{json.dumps(quality, sort_keys=True)[:8000]}"
            task = DevTask(task_id=review_task_id, repository=worktree_path, base_ref=base_ref, role="reviewer", prompt=review_prompt, acceptance=("return a structured final review",), allowed_paths=self.manifest.allowed_paths, dry_run=False)
            task.validate()
            result = self.runtime.execute(task, approved=approved) if isinstance(self.runtime, RuntimeRouter) else self.runtime.execute(task)  # type: ignore[attr-defined]
            try:
                verdict = parse_review_verdict(result.output)
                _write_artifact(envelope, "final_review_verdict.json", verdict.to_dict())
                envelope.event("final_review_recorded", task_id=task.task_id, approved=verdict.approved, findings=len(verdict.findings))
                if not verdict.approved:
                    quality = {"status": "failed", "checks": quality.get("checks", {}), "reason": "final_review_not_approved"}
                results.append(result)
            except ReviewValidationError as exc:
                envelope.event("final_review_rejected", task_id=task.task_id, error_type=type(exc).__name__)
                quality = {"status": "failed", "checks": quality.get("checks", {}), "reason": "final_review_invalid"}
        _write_artifact(envelope, "quality.json", quality)
        if quality["status"] != "passed":
            _write_artifact(envelope, "promotion.json", {"status": "blocked", "reason": "quality_checks_failed", "quality": quality})
            envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name, "roles": list(ROLES)})
            if worktree is not None:
                WorktreeManager(self.manifest.root).remove(worktree)
            return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
        pr: dict[str, object] = {}
        if create_pr:
            if dry_run or publisher is None:
                _write_artifact(envelope, "promotion.json", {"status": "blocked", "reason": "pr_requires_live_publisher"})
                if worktree is not None:
                    WorktreeManager(self.manifest.root).remove(worktree)
                return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
            try:
                pr = publisher.create_from_worktree(worktree=worktree_path, repository=self.manifest.root, branch=worktree.branch if worktree else f"repo-dev/{run_id}", base=base_ref, title=f"repo-dev: {prompt[:72]}", body=f"Generated by repo-dev-runtime run `{run_id}`.\n\nReview artifacts: `{run_dir}`.")
            except Exception as exc:
                _write_artifact(envelope, "promotion.json", {"status": "blocked", "reason": "pr_creation_failed", "error_type": type(exc).__name__, "error_message": str(exc)[:500]})
                envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "blocked", "repository": self.manifest.name, "roles": list(ROLES)})
                if worktree is not None:
                    WorktreeManager(self.manifest.root).remove(worktree)
                return WorkflowResult(run_id, "blocked", tuple(results), str(run_dir))
        _write_artifact(envelope, "promotion.json", {"status": "pr_created" if pr else "ready_for_human_review", "merge": False, "pr_creation": bool(pr), "pull_request": pr})
        envelope.finalize({"schema": "RepoDev.WorkflowRun.v1", "run_id": run_id, "status": "ready_for_human_review", "repository": self.manifest.name, "roles": list(ROLES)})
        if worktree is not None:
            WorktreeManager(self.manifest.root).remove(worktree)
        return WorkflowResult(run_id, "ready_for_human_review", tuple(results), str(run_dir))


def _network_access_allowed(manifest: RepoManifest, policy: RuntimePolicy | None) -> bool:
    """Return network authority only when both manifest and policy allow it."""
    if not manifest.network_access or policy is None:
        return False
    policy.authorize("network")
    return True


def run_tests(manifest: RepoManifest, *, timeout_s: float = 120.0, policy: RuntimePolicy | None = None) -> dict[str, object]:
    result = run_command(
        manifest.test_command,
        cwd=manifest.root,
        timeout_s=timeout_s,
        network_access=_network_access_allowed(manifest, policy),
    )
    return {"command": list(result.command), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": result.timed_out}


def run_quality_checks(manifest: RepoManifest, *, cwd: str, dry_run: bool, policy: RuntimePolicy | None = None) -> dict[str, object]:
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
            result = run_command(
                command,
                cwd=cwd,
                timeout_s=manifest.check_timeout_s,
                network_access=_network_access_allowed(manifest, policy),
            )
            item = {"status": "passed" if result.returncode == 0 and not result.timed_out else "failed", "command": list(command), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": result.timed_out}
        except Exception as exc:
            item = {"status": "failed", "command": list(command), "error_type": type(exc).__name__, "error_message": str(exc)[:500]}
        checks[name] = item
        failed = failed or item["status"] == "failed"
    return {"status": "failed" if failed else "passed", "checks": checks}
