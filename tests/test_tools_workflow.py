import subprocess
import json
import re
import sys
import time
from pathlib import Path

import pytest

from repo_dev_runtime.governance.artifacts import RunEnvelope
from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.integrations.github import GitHubPublisher
from repo_dev_runtime.manifest import RepoManifest
from repo_dev_runtime.tools.runner import run_command
from repo_dev_runtime.workflow import DevelopmentWorkflow, run_quality_checks
from repo_dev_runtime.contracts.models import DevResult
from tests.support.processes import pid_is_running


class FakeRuntime:
    def execute(self, task):
        return DevResult(task.task_id, "fake", "succeeded", output=task.role)


class RaisingRuntime:
    name = "raising_runtime"

    def execute(self, task):
        raise RuntimeError("api_key=workflow-provider-secret")


class SecretEchoRuntime:
    secret = "workflow-secret-sentinel"

    def execute(self, task):
        return DevResult(
            task.task_id,
            "fake",
            "succeeded",
            output=f"{task.role} api_key={self.secret}",
            telemetry={"access_token": self.secret},
        )


class ProposalRuntime:
    def execute(self, task):
        if task.role == "implementer":
            base = re.search(r"base_commit=([0-9a-f]+)", task.prompt).group(1)
            context = re.search(r"context_hash=([0-9a-f]+)", task.prompt).group(1)
            return DevResult(task.task_id, "implementer_provider", "succeeded", output=json.dumps({
                "schema": "RepoDev.EditProposal.v1", "proposal_id": "proposal-1", "task_id": task.task_id,
                "base_commit": base, "context_hash": context, "summary": "change value",
                "edits": [{"path": "src/app.py", "format": "search_replace", "search": "value = 1", "replace": "value = 2"}],
            }))
        if task.role == "reviewer":
            return DevResult(task.task_id, "reviewer_provider", "succeeded", output='{"schema":"RepoDev.ReviewVerdict.v1","approved":true,"summary":"safe","findings":[]}')
        return DevResult(task.task_id, "fake", "succeeded", output=task.role)


class SameProviderReviewRuntime(ProposalRuntime):
    """A provider may not approve the patch it just proposed."""

    def execute(self, task):
        result = super().execute(task)
        if task.role in {"implementer", "reviewer"}:
            return DevResult(
                result.task_id,
                "same_provider",
                result.status,
                result.output,
                result.changed_files,
                result.commit_sha,
                result.tests,
                result.telemetry,
                result.error_type,
                result.error_message,
                result.created_at,
            )
        return result


class EvidenceCapturingReviewerRuntime(ProposalRuntime):
    def __init__(self):
        self.reviewer_prompt = ""

    def execute(self, task):
        if task.role == "reviewer":
            self.reviewer_prompt = task.prompt
        return super().execute(task)


class FailingContextProvider:
    name = "failing_context"

    def capabilities(self):
        return {"license": "test", "source_url": "", "vendored": False}

    def build(self, *_args, **_kwargs):
        raise RuntimeError("provider unavailable")


class MalformedThenRepairRuntime(ProposalRuntime):
    def __init__(self):
        self.implementer_calls = 0

    def execute(self, task):
        if task.role == "implementer":
            self.implementer_calls += 1
            if self.implementer_calls == 1:
                return DevResult(task.task_id, "fake", "succeeded", output="not proposal json")
        return super().execute(task)


class RaisingRepairRuntime(ProposalRuntime):
    def __init__(self):
        self.implementer_calls = 0

    def execute(self, task):
        if task.role == "implementer":
            self.implementer_calls += 1
            if self.implementer_calls == 1:
                return DevResult(task.task_id, "fake", "succeeded", output="not proposal json")
            raise RuntimeError("api_key=repair-provider-secret")
        return super().execute(task)


class InterruptedProposalRuntime(ProposalRuntime):
    def __init__(self):
        self.tester_calls = 0

    def execute(self, task):
        if task.role == "tester":
            self.tester_calls += 1
            if self.tester_calls == 1:
                return DevResult(task.task_id, "fake", "failed", error_type="transient_failure")
        return super().execute(task)


class CredentialProposalRuntime(ProposalRuntime):
    def execute(self, task):
        if task.role == "implementer":
            base = re.search(r"base_commit=([0-9a-f]+)", task.prompt).group(1)
            context = re.search(r"context_hash=([0-9a-f]+)", task.prompt).group(1)
            return DevResult(task.task_id, "fake", "succeeded", output=json.dumps({
                "schema": "RepoDev.EditProposal.v1", "proposal_id": "credential-proposal", "task_id": task.task_id,
                "base_commit": base, "context_hash": context, "summary": "add a credential",
                "edits": [{"path": "src/app.py", "format": "whole_file", "content": 'api_key = "not-for-artifacts"\n'}],
            }))
        return super().execute(task)


def test_runner_redacts_credentials_and_blocks_network(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    result = run_command(["python", "-m", "pytest", "--version"], cwd=tmp_path)
    assert result.returncode == 0
    try:
        run_command(["curl", "https://example.com"], cwd=tmp_path)
    except PermissionError:
        pass
    else:
        raise AssertionError("network command should be blocked")


def test_runner_blocks_repository_mutation(tmp_path):
    try:
        run_command(["git", "push", "origin", "main"], cwd=tmp_path)
    except PermissionError:
        pass
    else:
        raise AssertionError("repository mutation should be blocked")


def test_runner_timeout_terminates_child_process_tree(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    script = tmp_path / "spawn_child.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    result = run_command([sys.executable, str(script)], cwd=tmp_path, timeout_s=0.2)

    assert result.timed_out is True
    assert result.returncode is None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not child_pid_file.exists():
        time.sleep(0.02)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert not pid_is_running(child_pid), "timed-out command left a child process running"


def test_workflow_contains_direct_provider_exception_and_persists_failure(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))

    result = DevelopmentWorkflow(
        manifest=manifest,
        policy=RuntimePolicy(),
        runtime=RaisingRuntime(),
        artifacts_root=tmp_path / "runs",
    ).run(prompt="inspect")

    assert result.status == "blocked"
    assert result.results[0].error_type == "RuntimeError"
    assert "workflow-provider-secret" not in result.results[0].error_message
    promotion = json.loads((tmp_path / "runs" / result.run_id / "promotion.json").read_text(encoding="utf-8"))
    assert promotion["reason"] == "planner_failed"


def test_live_edit_resume_replays_checksum_validated_patches(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=(".",), test_command=("git", "status", "--short"))
    runtime = InterruptedProposalRuntime()
    workflow = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=runtime, artifacts_root=tmp_path / "runs")

    first = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True)
    assert first.status == "blocked"
    run_dir = tmp_path / "runs" / first.run_id
    assert (run_dir / "patch_replay.jsonl").exists()
    retained_branch = subprocess.run(["git", "-C", str(tmp_path), "branch", "--list", f"repo-dev/{first.run_id}"], capture_output=True, text=True, check=True)
    assert retained_branch.stdout.strip()
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"

    resumed = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, run_id=first.run_id, resume=True)
    assert resumed.status == "ready_for_human_review"
    assert runtime.tester_calls == 2
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "proposal_replayed" in events and "patch_replay_completed" in events
    deleted_branch = subprocess.run(["git", "-C", str(tmp_path), "branch", "--list", f"repo-dev/{first.run_id}"], capture_output=True, text=True, check=True)
    assert deleted_branch.stdout.strip() == ""


def test_resume_rejects_changed_request_identity(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    run_root = tmp_path / "runs"
    workflow = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=run_root)
    first = workflow.run(prompt="inspect original")

    with pytest.raises(ValueError, match="does not match"):
        workflow.run(prompt="inspect changed", run_id=first.run_id, resume=True)


def test_resume_rejects_tampered_request_identity(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    run_root = tmp_path / "runs"
    workflow = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=run_root)
    first = workflow.run(prompt="inspect original")
    request_path = run_root / first.run_id / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["request_hash"] = "0" * 64
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        workflow.run(prompt="inspect original", run_id=first.run_id, resume=True)


def test_resume_rejects_tampered_promotion_state(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    run_root = tmp_path / "runs"
    workflow = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=run_root)
    first = workflow.run(prompt="inspect original")
    promotion_path = run_root / first.run_id / "promotion.json"
    promotion = json.loads(promotion_path.read_text(encoding="utf-8"))
    promotion["status"] = "pr_created"
    promotion_path.write_text(json.dumps(promotion), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        workflow.run(prompt="inspect original", run_id=first.run_id, resume=True)


def test_resume_rejects_tampered_role_artifact(tmp_path):
    """Regression test: _verify_resume_request's `required` checksum list
    was built from bare ROLES names ("planner", "implementer", ...), but
    role artifacts are actually written as "{role}.json" (e.g.
    "planner.json"). run_dir / "planner" never exists, so no role file was
    ever added to `required` and its checksum was silently never checked -
    only promotion.json (whose name happens to be a literal string) was
    actually protected. Tampering with a role's cached output and
    resuming must be rejected the same way tampering with promotion.json
    already is."""
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    run_root = tmp_path / "runs"
    workflow = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=run_root)
    first = workflow.run(prompt="inspect original")
    planner_path = run_root / first.run_id / "planner.json"
    planner = json.loads(planner_path.read_text(encoding="utf-8"))
    planner["output"] = "tampered"
    planner_path.write_text(json.dumps(planner), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        workflow.run(prompt="inspect original", run_id=first.run_id, resume=True)


def test_resume_constructs_run_envelope_only_once(tmp_path, monkeypatch):
    """Regression test: DevelopmentWorkflow.run() used to construct a
    RunEnvelope once inside _verify_resume_request and again for the rest
    of run() itself - two full parses/re-validations of events.jsonl per
    resume call for no reason, since the second construction only needed
    the first one's already-verified envelope. A resume call must
    construct RunEnvelope exactly once."""
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    run_root = tmp_path / "runs"
    workflow = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=run_root)
    first = workflow.run(prompt="inspect original")

    construction_count = {"n": 0}
    real_init = RunEnvelope.__init__

    def counting_init(self, *args, **kwargs):
        construction_count["n"] += 1
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(RunEnvelope, "__init__", counting_init)

    workflow.run(prompt="inspect original", run_id=first.run_id, resume=True)

    assert construction_count["n"] == 1


def test_live_edit_resume_blocks_tampered_replay_artifact(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=(".",))
    workflow = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=InterruptedProposalRuntime(), artifacts_root=tmp_path / "runs")
    first = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True)
    replay_path = tmp_path / "runs" / first.run_id / "patch_replay.jsonl"
    replay_path.write_text(replay_path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    resumed = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, run_id=first.run_id, resume=True)
    assert resumed.status == "blocked"
    promotion = json.loads((tmp_path / "runs" / first.run_id / "promotion.json").read_text(encoding="utf-8"))
    assert promotion["reason"] == "worktree_creation_or_patch_replay_failed"


def test_live_edit_rejects_credential_shaped_proposals_before_replay_persistence(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=(".",))

    result = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=CredentialProposalRuntime(), artifacts_root=tmp_path / "runs").run(
        prompt="change value", base_ref="main", dry_run=False, apply_edits=True,
    )
    assert result.status == "blocked"
    assert not (tmp_path / "runs" / result.run_id / "patch_replay.jsonl").exists()
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_manifest_cannot_elevate_network_policy(tmp_path):
    manifest = RepoManifest(
        name="fixture",
        root=str(tmp_path),
        network_access=True,
        test_command=("curl", "https://example.com"),
    )

    with pytest.raises(PermissionError, match="network access"):
        DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime()).run(
            prompt="network-enabled test",
            dry_run=False,
        )

    checks = run_quality_checks(manifest, cwd=str(tmp_path), dry_run=False, policy=RuntimePolicy())
    assert checks["status"] == "failed"
    assert checks["checks"]["tests"]["error_type"] == "PermissionError"

def test_five_role_workflow_writes_envelope(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    result = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=tmp_path / "runs").run(prompt="inspect")
    assert result.status == "ready_for_human_review"
    assert (tmp_path / "runs" / result.run_id / "promotion.json").exists()
    assert (tmp_path / "runs" / result.run_id / "checksums.json").exists()
    assert len(result.results) == 5


def test_workflow_records_context_provider_and_falls_back_safely(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=(".",))
    result = DevelopmentWorkflow(
        manifest=manifest,
        policy=RuntimePolicy(),
        runtime=FakeRuntime(),
        context_provider=FailingContextProvider(),
        artifacts_root=tmp_path / "runs",
    ).run(prompt="inspect")

    provider = json.loads((tmp_path / "runs" / result.run_id / "context_provider.json").read_text(encoding="utf-8"))
    assert provider["requested"] == "failing_context"
    assert provider["used"] == "static_map"


def test_workflow_redacts_provider_output_before_artifact_persistence(tmp_path):
    runtime = SecretEchoRuntime()
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    result = DevelopmentWorkflow(
        manifest=manifest,
        policy=RuntimePolicy(),
        runtime=runtime,
        artifacts_root=tmp_path / "runs",
    ).run(prompt="inspect")

    artifact_dir = tmp_path / "runs" / result.run_id
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in artifact_dir.iterdir() if path.is_file())
    assert runtime.secret not in persisted
    assert "[REDACTED]" in (artifact_dir / "planner.json").read_text(encoding="utf-8")
    # Redaction is an artifact boundary, not a mutation of the live result.
    assert runtime.secret in result.results[0].output


def test_live_proposal_workflow_only_changes_disposable_worktree(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    # This is the auto-detected manifest scope: permit normal edits anywhere
    # inside the disposable repository worktree, never outside it.
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=(".",), test_command=("git", "status", "--short"))
    result = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=ProposalRuntime(), artifacts_root=tmp_path / "runs").run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True)
    assert result.status == "ready_for_human_review"
    assert (tmp_path / "src" / "app.py").read_text() == "value = 1\n"
    assert (tmp_path / "runs" / result.run_id / "proposal.json").exists()
    assert (tmp_path / "runs" / result.run_id / "review_verdict.json").exists()
    assert not subprocess.run(["git", "-C", str(tmp_path), "branch", "--list", f"repo-dev/{result.run_id}"], capture_output=True, text=True, check=True).stdout.strip()


def test_live_edit_requires_independent_post_quality_reviewer(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"))
    runtime = EvidenceCapturingReviewerRuntime()

    result = DevelopmentWorkflow(
        manifest=manifest,
        policy=RuntimePolicy(),
        runtime=runtime,
        artifacts_root=tmp_path / "runs",
    ).run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True)

    assert result.status == "ready_for_human_review"
    assert "Quality:" in runtime.reviewer_prompt
    assert "Final diff:" in runtime.reviewer_prompt
    assert "value = 2" in runtime.reviewer_prompt
    artifact_dir = tmp_path / "runs" / result.run_id
    assert (artifact_dir / "review_verdict.json").exists()
    assert (artifact_dir / "reviewer.json").exists()


def test_live_edit_self_review_is_recorded_as_a_warning_when_no_independent_reviewer_is_available(tmp_path):
    """Regression test: enforcing "implementer cannot review its own
    patch" unconditionally deadlocked every single-provider --apply-edits
    run, including the primary documented path (--live --enable-ollama
    --apply-edits) - one runtime object, no router, no distinct
    reviewer_runtime. A real single adapter reports the same fixed
    self.name for every role, so excluding it could never produce a
    different provider, and the gate always raised. This is exactly that
    shape: SameProviderReviewRuntime reports "same_provider" for both
    implementer and reviewer, from a single non-router runtime object.
    Independent review is now only enforced when a second, authorized
    provider was structurally available; otherwise the run proceeds and a
    self_reviewed_warning event records the fact for a human to see,
    instead of blocking the run forever with no security benefit (quality
    checks already ran; there is no second party to defer to)."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"))

    result = DevelopmentWorkflow(
        manifest=manifest,
        policy=RuntimePolicy(),
        runtime=SameProviderReviewRuntime(),
        artifacts_root=tmp_path / "runs",
    ).run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True)

    assert result.status == "ready_for_human_review"
    events = (tmp_path / "runs" / result.run_id / "events.jsonl").read_text(encoding="utf-8")
    assert "self_reviewed_warning" in events


def test_live_edit_still_blocks_self_review_when_independence_was_available(tmp_path):
    """Companion to the warning-not-block test above: when a genuinely
    distinct reviewer_runtime IS configured (independent review was
    structurally possible), the hard block must still fire if that
    reviewer's result nonetheless reports the implementer's own provider
    name - e.g. a misconfigured or misreporting adapter."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"))

    class SpoofedIdentityReviewerRuntime:
        name = "reviewer_provider"

        def execute(self, task):
            return DevResult(task.task_id, "implementer_provider", "succeeded", output='{"schema":"RepoDev.ReviewVerdict.v1","approved":true,"summary":"safe","findings":[]}')

    result = DevelopmentWorkflow(
        manifest=manifest,
        policy=RuntimePolicy(),
        runtime=ProposalRuntime(),
        reviewer_runtime=SpoofedIdentityReviewerRuntime(),
        artifacts_root=tmp_path / "runs",
    ).run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True)

    assert result.status == "blocked"
    promotion = json.loads((tmp_path / "runs" / result.run_id / "promotion.json").read_text(encoding="utf-8"))
    assert promotion["status"] == "blocked"
    assert promotion["quality"]["reason"] == "final_review_invalid"


def test_malformed_proposal_uses_bounded_repair(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"))
    runtime = MalformedThenRepairRuntime()
    result = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=runtime, artifacts_root=tmp_path / "runs").run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, max_fix_attempts=1)
    events = (tmp_path / "runs" / result.run_id / "events.jsonl").read_text()
    assert result.status == "ready_for_human_review"
    assert "proposal_rejected" in events and "proposal_repaired" in events
    assert (tmp_path / "src" / "app.py").read_text() == "value = 1\n"
    assert json.loads((tmp_path / "runs" / result.run_id / "applied_patch.json").read_text())["changed_files"] == ["src/app.py"]


def test_repair_provider_exception_is_contained_and_redacted(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"))

    result = DevelopmentWorkflow(
        manifest=manifest,
        policy=RuntimePolicy(),
        runtime=RaisingRepairRuntime(),
        artifacts_root=tmp_path / "runs",
    ).run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, max_fix_attempts=1)

    assert result.status == "blocked"
    assert all("repair-provider-secret" not in item.error_message for item in result.results)
    promotion = json.loads((tmp_path / "runs" / result.run_id / "promotion.json").read_text(encoding="utf-8"))
    assert "repair-provider-secret" not in json.dumps(promotion)


def test_create_pr_workflow_calls_publisher_with_correct_signature(tmp_path, monkeypatch):
    """Regression test: GitHubPublisher.create_from_worktree requires
    allowed_paths/forbidden_paths (a real policy gate on which changed paths
    may be published), but the workflow's call site previously omitted both
    - a live --create-pr run would raise
    'TypeError: create_from_worktree() missing 2 required keyword-only
    arguments' immediately, with zero test coverage catching it. Network
    calls are monkeypatched out (publish_branch/create_pull_request); the
    point of this test is the call signature and path-policy enforcement
    actually reaching the real GitHubPublisher method, not the network."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)

    monkeypatch.setattr(GitHubPublisher, "publish_branch", lambda self, *, repository, branch, base="main": {"branch": branch, "base": base, "pushed": True})
    monkeypatch.setattr(GitHubPublisher, "create_pull_request", lambda self, *, repository, branch, base, title, body: {"url": "https://example.invalid/pr/1", "number": 1, "branch": branch, "base": base})

    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"), pull_request_creation=True)
    policy = RuntimePolicy(allow_pr_creation=True, allow_branch_publish=True)
    publisher = GitHubPublisher(policy=policy, token="fake-token")
    result = DevelopmentWorkflow(manifest=manifest, policy=policy, runtime=ProposalRuntime(), artifacts_root=tmp_path / "runs").run(
        prompt="change value", base_ref="main", dry_run=False, apply_edits=True, create_pr=True, publisher=publisher,
    )
    assert result.status == "ready_for_human_review"
    promotion = json.loads((tmp_path / "runs" / result.run_id / "promotion.json").read_text())
    assert promotion["status"] == "pr_created"
    assert promotion["pull_request"]["url"] == "https://example.invalid/pr/1"


def test_resuming_a_completed_create_pr_run_does_not_publish_a_second_pull_request(tmp_path, monkeypatch):
    """Regression test: nothing in DevelopmentWorkflow.run() previously
    checked whether a resumed run had already reached a success terminal
    state (promotion.json's "status", not WorkflowResult.status) before
    falling through to quality checks and the create_pr block again.
    --resume --apply-edits --create-pr against an already-completed run_id
    would build a brand-new worktree/branch (the original may already be
    deleted - see WorktreeManager.remove(delete_branch=True)) and call
    GitHubPublisher.create_from_worktree a second time: a real duplicate
    PR/branch push."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)

    calls = {"create_pull_request": 0, "publish_branch": 0}

    def fake_publish_branch(self, *, repository, branch, base="main"):
        calls["publish_branch"] += 1
        return {"branch": branch, "base": base, "pushed": True}

    def fake_create_pull_request(self, *, repository, branch, base, title, body):
        calls["create_pull_request"] += 1
        return {"url": f"https://example.invalid/pr/{calls['create_pull_request']}", "number": calls["create_pull_request"], "branch": branch, "base": base}

    monkeypatch.setattr(GitHubPublisher, "publish_branch", fake_publish_branch)
    monkeypatch.setattr(GitHubPublisher, "create_pull_request", fake_create_pull_request)

    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"), pull_request_creation=True)
    policy = RuntimePolicy(allow_pr_creation=True, allow_branch_publish=True)
    publisher = GitHubPublisher(policy=policy, token="fake-token")
    workflow = DevelopmentWorkflow(manifest=manifest, policy=policy, runtime=ProposalRuntime(), artifacts_root=tmp_path / "runs")

    first = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, create_pr=True, publisher=publisher)
    assert first.status == "ready_for_human_review"
    assert calls["create_pull_request"] == 1
    assert calls["publish_branch"] == 1

    resumed = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, create_pr=True, publisher=publisher, run_id=first.run_id, resume=True)
    assert resumed.status == "ready_for_human_review"
    assert calls["create_pull_request"] == 1, "resuming an already-completed create_pr run must not publish a second pull request"
    assert calls["publish_branch"] == 1


def test_resuming_a_completed_create_pr_run_without_create_pr_flag_preserves_original_pr_record(tmp_path, monkeypatch):
    """Regression test: the prior fix for the duplicate-PR bug only
    guarded the case where the *resume* call itself also passed
    create_pr=True. But a run that originally completed with
    create_pr=True (promotion.json status "pr_created") can later be
    resumed with --resume and no --create-pr at all (e.g. an operator
    just wants to inspect/replay the run). Nothing checked whether the
    run itself had already reached a terminal success state before
    falling through to build a brand-new worktree/branch (the original
    was already deleted on success - see
    WorktreeManager.remove(delete_branch=True)), re-run quality checks,
    and overwrite promotion.json with {"status": "ready_for_human_review"},
    destroying the record that a PR was actually created, including its
    URL/number. The short-circuit must key off the run's own recorded
    completion status, independent of this call's create_pr value."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)

    calls = {"create_pull_request": 0, "publish_branch": 0}

    def fake_publish_branch(self, *, repository, branch, base="main"):
        calls["publish_branch"] += 1
        return {"branch": branch, "base": base, "pushed": True}

    def fake_create_pull_request(self, *, repository, branch, base, title, body):
        calls["create_pull_request"] += 1
        return {"url": f"https://example.invalid/pr/{calls['create_pull_request']}", "number": calls["create_pull_request"], "branch": branch, "base": base}

    monkeypatch.setattr(GitHubPublisher, "publish_branch", fake_publish_branch)
    monkeypatch.setattr(GitHubPublisher, "create_pull_request", fake_create_pull_request)

    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"), pull_request_creation=True)
    policy = RuntimePolicy(allow_pr_creation=True, allow_branch_publish=True)
    publisher = GitHubPublisher(policy=policy, token="fake-token")
    workflow = DevelopmentWorkflow(manifest=manifest, policy=policy, runtime=ProposalRuntime(), artifacts_root=tmp_path / "runs")

    first = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, create_pr=True, publisher=publisher)
    assert first.status == "ready_for_human_review"
    assert calls["create_pull_request"] == 1
    assert calls["publish_branch"] == 1

    run_dir = Path(first.artifact_dir)
    original_promotion = json.loads((run_dir / "promotion.json").read_text(encoding="utf-8"))
    assert original_promotion["status"] == "pr_created"

    resumed = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, create_pr=False, publisher=publisher, run_id=first.run_id, resume=True)
    assert resumed.status == "ready_for_human_review"
    assert calls["create_pull_request"] == 1, "resuming without create_pr must not publish a second pull request"
    assert calls["publish_branch"] == 1, "resuming without create_pr must not push a second branch"

    reloaded_promotion = json.loads((run_dir / "promotion.json").read_text(encoding="utf-8"))
    assert reloaded_promotion["status"] == "pr_created", "resuming without create_pr must not overwrite the original pr_created record"
    assert reloaded_promotion.get("pull_request") == original_promotion.get("pull_request"), "the original PR url/number must be preserved across a non-create_pr resume"
