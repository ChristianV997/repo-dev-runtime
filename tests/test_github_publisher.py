"""Branch coverage for repo_dev_runtime.integrations.github.GitHubPublisher
beyond the already-fixed create_from_worktree call-site bug (see
test_tools_workflow.py::test_create_pr_workflow_calls_publisher_with_correct_signature).
No real network calls anywhere in this file."""
from __future__ import annotations

import subprocess

import pytest

from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.integrations.github import GitHubPublisher
from repo_dev_runtime.tools.runner import CommandResult


def _init_repo(tmp_path, *, remote: str | None = None) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "README.md").write_text("placeholder\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    if remote:
        subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", remote], check=True)


def test_owner_repo_parses_a_real_github_remote(tmp_path, monkeypatch):
    # This sandbox's git proxy transparently rewrites github.com remote
    # URLs (via a global url.<proxy>.insteadOf config), so a real
    # `git remote get-url origin` here never returns the literal
    # "github.com" host regardless of what was configured - an
    # environment artifact, not something _owner_repo can control.
    # Isolate the actual regex-parsing logic by controlling exactly what
    # subprocess.run returns, matching what a real, unproxied environment
    # would give `git remote get-url origin`.
    _init_repo(tmp_path)

    class FakeCompleted:
        returncode = 0
        stdout = "https://github.com/example-owner/example-repo.git\n"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeCompleted())
    publisher = GitHubPublisher(policy=RuntimePolicy())
    assert publisher._owner_repo(tmp_path) == ("example-owner", "example-repo")


def test_owner_repo_rejects_a_non_github_remote(tmp_path):
    _init_repo(tmp_path, remote="https://gitlab.com/example-owner/example-repo.git")
    publisher = GitHubPublisher(policy=RuntimePolicy())
    with pytest.raises(ValueError, match="not a GitHub remote"):
        publisher._owner_repo(tmp_path)


def test_owner_repo_rejects_a_repo_with_no_remote(tmp_path):
    _init_repo(tmp_path)
    publisher = GitHubPublisher(policy=RuntimePolicy())
    with pytest.raises(ValueError, match="not a GitHub remote"):
        publisher._owner_repo(tmp_path)


def test_create_from_worktree_rejects_a_clean_worktree(tmp_path):
    _init_repo(tmp_path)
    publisher = GitHubPublisher(policy=RuntimePolicy(allow_pr_creation=True, allow_branch_publish=True), token="fake-token")
    with pytest.raises(ValueError, match="without changes"):
        publisher.create_from_worktree(
            worktree=tmp_path, repository=tmp_path, branch="repo-dev/run1", base="main",
            title="t", body="b", allowed_paths=(".",), forbidden_paths=(),
        )


def test_create_from_worktree_rejects_a_change_outside_allowed_paths(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    publisher = GitHubPublisher(policy=RuntimePolicy(allow_pr_creation=True, allow_branch_publish=True), token="fake-token")
    with pytest.raises(PermissionError, match="outside policy"):
        publisher.create_from_worktree(
            worktree=tmp_path, repository=tmp_path, branch="repo-dev/run1", base="main",
            title="t", body="b", allowed_paths=("src",), forbidden_paths=(),
        )


def test_create_from_worktree_surfaces_git_add_failure(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "changed.py").write_text("x = 1\n", encoding="utf-8")

    def fake_run_command(command, *, cwd, timeout_s=120.0, **kwargs):
        if list(command[:2]) == ["git", "add"]:
            return CommandResult(command=tuple(command), returncode=1, stdout="", stderr="fatal: simulated add failure")
        raise AssertionError(f"unexpected command in this test: {command}")

    monkeypatch.setattr("repo_dev_runtime.integrations.github.run_command", fake_run_command)
    publisher = GitHubPublisher(policy=RuntimePolicy(allow_pr_creation=True, allow_branch_publish=True), token="fake-token")
    with pytest.raises(RuntimeError, match="simulated add failure"):
        publisher.create_from_worktree(
            worktree=tmp_path, repository=tmp_path, branch="repo-dev/run1", base="main",
            title="t", body="b", allowed_paths=(".",), forbidden_paths=(),
        )


def test_create_from_worktree_surfaces_git_commit_failure(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "changed.py").write_text("x = 1\n", encoding="utf-8")

    def fake_run_command(command, *, cwd, timeout_s=120.0, **kwargs):
        if list(command[:2]) == ["git", "add"]:
            return CommandResult(command=tuple(command), returncode=0, stdout="", stderr="")
        if list(command[:2]) == ["git", "commit"]:
            return CommandResult(command=tuple(command), returncode=1, stdout="", stderr="fatal: simulated commit failure")
        raise AssertionError(f"unexpected command in this test: {command}")

    monkeypatch.setattr("repo_dev_runtime.integrations.github.run_command", fake_run_command)
    publisher = GitHubPublisher(policy=RuntimePolicy(allow_pr_creation=True, allow_branch_publish=True), token="fake-token")
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        publisher.create_from_worktree(
            worktree=tmp_path, repository=tmp_path, branch="repo-dev/run1", base="main",
            title="t", body="b", allowed_paths=(".",), forbidden_paths=(),
        )
