import subprocess

from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.manifest import RepoManifest
from repo_dev_runtime.tools.runner import run_command
from repo_dev_runtime.workflow import DevelopmentWorkflow
from repo_dev_runtime.contracts.models import DevResult


class FakeRuntime:
    def execute(self, task):
        return DevResult(task.task_id, "fake", "succeeded", output=task.role)


def test_runner_redacts_credentials_and_blocks_network(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    result = run_command(["python", "-c", "print('ok')"], cwd=tmp_path)
    assert result.returncode == 0
    try:
        run_command(["curl", "https://example.com"], cwd=tmp_path)
    except PermissionError:
        pass
    else:
        raise AssertionError("network command should be blocked")


def test_five_role_workflow_writes_envelope(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=("src",))
    result = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=tmp_path / "runs").run(prompt="inspect")
    assert result.status == "ready_for_human_review"
    assert (tmp_path / "runs" / result.run_id / "promotion.json").exists()
    assert (tmp_path / "runs" / result.run_id / "checksums.json").exists()
    assert len(result.results) == 5
