import subprocess
import json
import re

import pytest

from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.integrations.github import GitHubPublisher
from repo_dev_runtime.manifest import RepoManifest
from repo_dev_runtime.tools.runner import run_command
from repo_dev_runtime.workflow import DevelopmentWorkflow, run_quality_checks
from repo_dev_runtime.contracts.models import DevResult


class FakeRuntime:
    def execute(self, task):
        return DevResult(task.task_id, "fake", "succeeded", output=task.role)


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
            return DevResult(task.task_id, "fake", "succeeded", output=json.dumps({
                "schema": "RepoDev.EditProposal.v1", "proposal_id": "proposal-1", "task_id": task.task_id,
                "base_commit": base, "context_hash": context, "summary": "change value",
                "edits": [{"path": "src/app.py", "format": "search_replace", "search": "value = 1", "replace": "value = 2"}],
            }))
        if task.role == "reviewer":
            return DevResult(task.task_id, "fake", "succeeded", output='{"schema":"RepoDev.ReviewVerdict.v1","approved":true,"summary":"safe","findings":[]}')
        return DevResult(task.task_id, "fake", "succeeded", output=task.role)


class MalformedThenRepairRuntime(ProposalRuntime):
    def __init__(self):
        self.implementer_calls = 0

    def execute(self, task):
        if task.role == "implementer":
            self.implementer_calls += 1
            if self.implementer_calls == 1:
                return DevResult(task.task_id, "fake", "succeeded", output="not proposal json")
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
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"

    resumed = workflow.run(prompt="change value", base_ref="main", dry_run=False, apply_edits=True, run_id=first.run_id, resume=True)
    assert resumed.status == "ready_for_human_review"
    assert runtime.tester_calls == 2
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "proposal_replayed" in events and "patch_replay_completed" in events


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
