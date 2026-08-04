"""Regression test: no test anywhere drove the actual CLI entry points
through more than one subcommand in sequence. test_cli.py only calls
`run`/`benchmark` once per test; test_orchestration.py and
test_tools_workflow.py construct DevelopmentWorkflow/RepoManifest directly,
bypassing argument parsing entirely. Nothing proved the advertised
`probe -> init-manifest -> validate-consumers -> run -> resume ->
create-pr` lifecycle works when a user actually types these commands."""
import json
import re
import subprocess

import pytest

from repo_dev_runtime import cli
from repo_dev_runtime.contracts.models import DevResult, RuntimeHealth
from repo_dev_runtime.integrations.github import GitHubPublisher
from repo_dev_runtime.runtimes.registry import RuntimeRegistry


def _run_cli(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


class FakeOllamaRuntime:
    """Stands in for a real Ollama endpoint: always healthy, and returns
    schema-valid EditProposal/ReviewVerdict JSON for the implementer/
    reviewer roles, matching the contract DevelopmentWorkflow enforces."""

    name = "ollama"

    def health(self) -> RuntimeHealth:
        return RuntimeHealth(name="ollama", configured=True, reachable=True)

    def execute(self, task):
        if task.role == "implementer":
            base = re.search(r"base_commit=([0-9a-f]+)", task.prompt).group(1)
            context = re.search(r"context_hash=([0-9a-f]+)", task.prompt).group(1)
            return DevResult(task.task_id, "ollama", "succeeded", output=json.dumps({
                "schema": "RepoDev.EditProposal.v1", "proposal_id": "proposal-1", "task_id": task.task_id,
                "base_commit": base, "context_hash": context, "summary": "change value",
                "edits": [{"path": "src/app.py", "format": "search_replace", "search": "value = 1", "replace": "value = 2"}],
            }))
        if task.role == "reviewer":
            return DevResult(task.task_id, "ollama", "succeeded", output='{"schema":"RepoDev.ReviewVerdict.v1","approved":true,"summary":"safe","findings":[]}')
        return DevResult(task.task_id, "ollama", "succeeded", output=task.role)


@pytest.fixture
def fake_default_registry(monkeypatch):
    def spying_default_registry(**_kwargs):
        return RuntimeRegistry({"ollama": FakeOllamaRuntime()})

    monkeypatch.setattr(cli, "default_registry", spying_default_registry)


def test_full_consumer_lifecycle_through_the_real_cli_entry_points(tmp_path, capsys, monkeypatch, fake_default_registry):
    """Drive probe -> init-manifest -> validate-consumers -> run (dry-run)
    -> run (--live --apply-edits) -> run (--resume) -> run (--create-pr)
    through cli.main against one real throwaway git repo, asserting each
    stage's real output/artifacts are consumed correctly by the next -
    the advertised lifecycle as a user actually types it, not just via
    direct DevelopmentWorkflow construction."""
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    artifacts_root = tmp_path / "runs"

    # 1. probe: read-only capability discovery against a repo with no manifest yet.
    code, probe_result = _run_cli(["probe", str(repo)], capsys)
    assert code == 0
    assert probe_result["manifest"]["name"]

    # 2. init-manifest: writes .dev-runtime/repository.json with auto-detected defaults.
    code, manifest_result = _run_cli(["init-manifest", str(repo)], capsys)
    assert code == 0
    manifest_path = repo / ".dev-runtime" / "repository.json"
    assert manifest_path.exists()
    assert manifest_result["allowed_paths"] == ["."]

    # A brand-new consumer repo's default manifest disables PR creation and
    # allows the whole repo by default; the documented onboarding step is to
    # scope it before a live --create-pr run, exactly as docs/
    # quickstart-consumer-onboarding.md walks through.
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["allowed_paths"] = ["src"]
    manifest_payload["pull_request_creation"] = True
    manifest_path.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")

    # 3. validate-consumers: read-only check that the manifest is well-formed.
    code, consumers_result = _run_cli(["validate-consumers", str(repo)], capsys)
    assert code == 0
    assert consumers_result[0]["valid"] is True

    # 4. run (dry-run): no worktree, no edits, exercises all five roles.
    code, dry_result = _run_cli([
        "run", str(repo), "--prompt", "inspect the repository",
        "--artifacts-root", str(artifacts_root),
    ], capsys)
    assert code == 0
    assert dry_result["status"] == "ready_for_human_review"
    assert len(dry_result["results"]) == 5

    # 5. run (--live --apply-edits): a real disposable worktree, a real
    # implementer proposal applied and merged back via fast-forward.
    code, live_result = _run_cli([
        "run", str(repo), "--prompt", "change value", "--base-ref", "main",
        "--live", "--enable-ollama", "--apply-edits",
        "--artifacts-root", str(artifacts_root),
    ], capsys)
    assert code == 0
    assert live_result["status"] == "ready_for_human_review"
    assert (repo / "src" / "app.py").read_text() == "value = 1\n", "edits land only in the disposable worktree, never the consumer checkout directly"
    run_id = live_result["run_id"]
    assert (artifacts_root / run_id / "proposal.json").exists()

    # 6. run (--resume): the same run_id replays cached role results without
    # rebuilding a worktree or re-running quality checks.
    code, resumed_result = _run_cli([
        "run", str(repo), "--prompt", "change value", "--base-ref", "main",
        "--live", "--enable-ollama", "--apply-edits", "--resume",
        "--run-id", run_id, "--artifacts-root", str(artifacts_root),
    ], capsys)
    assert code == 0
    assert resumed_result["status"] == "ready_for_human_review"

    # 7. run (--create-pr): a fresh run that actually publishes, with the
    # GitHub calls monkeypatched so no real network/API call is made.
    calls = {"create_pull_request": 0, "publish_branch": 0}

    def fake_publish_branch(self, *, repository, branch, base="main"):
        calls["publish_branch"] += 1
        return {"branch": branch, "base": base, "pushed": True}

    def fake_create_pull_request(self, *, repository, branch, base, title, body):
        calls["create_pull_request"] += 1
        return {"url": "https://example.invalid/pr/1", "number": 1, "branch": branch, "base": base}

    monkeypatch.setattr(GitHubPublisher, "publish_branch", fake_publish_branch)
    monkeypatch.setattr(GitHubPublisher, "create_pull_request", fake_create_pull_request)

    code, pr_result = _run_cli([
        "run", str(repo), "--prompt", "change value again", "--base-ref", "main",
        "--live", "--enable-ollama", "--apply-edits", "--create-pr",
        "--artifacts-root", str(artifacts_root),
    ], capsys)
    assert code == 0
    assert pr_result["status"] == "ready_for_human_review"
    assert calls["create_pull_request"] == 1
    assert calls["publish_branch"] == 1
    promotion = json.loads((artifacts_root / pr_result["run_id"] / "promotion.json").read_text(encoding="utf-8"))
    assert promotion["status"] == "pr_created"
