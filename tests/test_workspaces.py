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
