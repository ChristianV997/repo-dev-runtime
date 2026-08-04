import subprocess

import pytest

from repo_dev_runtime.workspaces import WorktreeManager


def _git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, check=True)


def test_worktree_lifecycle(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial")
    manager = WorktreeManager(tmp_path, tmp_path.parent / "worktrees")
    worktree = manager.create(run_id="run-1")
    assert worktree.path.exists()
    assert (worktree.path / "README.md").exists()
    manager.remove(worktree)
    assert not worktree.path.exists()


def test_worktree_root_inside_repository_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        WorktreeManager(tmp_path, tmp_path / "nested")


def test_worktree_reuses_generated_branch_for_resume(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial")
    manager = WorktreeManager(tmp_path, tmp_path.parent / "worktrees-resume")
    first = manager.create(run_id="resume-1")
    manager.remove(first)
    second = manager.create(run_id="resume-1")
    assert second.branch == first.branch
    manager.remove(second)


def test_completed_worktree_branch_can_be_removed(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial")
    manager = WorktreeManager(tmp_path, tmp_path.parent / "worktrees-cleanup")
    worktree = manager.create(run_id="completed-1")

    assert manager.remove(worktree, delete_branch=True) is True
    branch = _git(tmp_path, "branch", "--list", worktree.branch)
    assert branch.stdout.strip() == ""


def test_non_runtime_branch_is_never_deleted(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial")
    manager = WorktreeManager(tmp_path, tmp_path.parent / "worktrees-nonruntime")
    worktree = manager.create(run_id="safe-1")
    foreign = type(worktree)(worktree.path, "feature/manual", worktree.base_ref)

    with pytest.raises(ValueError, match="non-runtime"):
        manager.remove(foreign, delete_branch=True)
    manager.remove(worktree)
