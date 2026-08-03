import subprocess
import json
import re

import pytest

from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.manifest import RepoManifest
from repo_dev_runtime.tools.runner import run_command
from repo_dev_runtime.workflow import DevelopmentWorkflow
from repo_dev_runtime.contracts.models import DevResult


class FakeRuntime:
    def execute(self, task):
        return DevResult(task.task_id, "fake", "succeeded", output=task.role)


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


def test_live_edit_resume_fails_before_worktree_creation(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=(".",))

    with pytest.raises(ValueError, match="durable patch replay"):
        DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime()).run(
            prompt="resume edit",
            dry_run=False,
            resume=True,
            apply_edits=True,
        )


def test_five_role_workflow_writes_envelope(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    result = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=tmp_path / "runs").run(prompt="inspect")
    assert result.status == "ready_for_human_review"
    assert (tmp_path / "runs" / result.run_id / "promotion.json").exists()
    assert (tmp_path / "runs" / result.run_id / "checksums.json").exists()
    assert len(result.results) == 5


def test_live_proposal_workflow_only_changes_disposable_worktree(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",), test_command=("git", "status", "--short"))
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
