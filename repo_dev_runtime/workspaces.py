"""Disposable Git worktree lifecycle with path-bound metadata."""
from __future__ import annotations

import subprocess
import re
from dataclasses import dataclass
from pathlib import Path


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RUNTIME_BRANCH = re.compile(r"^repo-dev/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_ref: str


class WorktreeManager:
    def __init__(self, repository: str | Path, root: str | Path | None = None) -> None:
        self.repository = Path(repository).resolve()
        self.root = Path(root or self.repository.parent / ".repo-dev-worktrees").resolve()
        if self.root == self.repository or self.repository in self.root.parents:
            raise ValueError("worktree root must not be inside the repository")

    def create(self, *, run_id: str, base_ref: str = "HEAD") -> Worktree:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be 1-128 safe alphanumeric characters, dots, underscores, or hyphens")
        self.root.mkdir(parents=True, exist_ok=True)
        path = (self.root / run_id).resolve()
        if self.root not in path.parents or path == self.repository:
            raise ValueError("worktree path must remain inside the configured worktree root")
        branch = f"repo-dev/{run_id}"
        branch_exists = subprocess.run(["git", "-C", str(self.repository), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], capture_output=True, text=True, check=False).returncode == 0
        command = ["git", "-C", str(self.repository), "worktree", "add", str(path), branch] if branch_exists else ["git", "-C", str(self.repository), "worktree", "add", "-b", branch, str(path), base_ref]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip()[:500] or "git worktree creation failed")
        return Worktree(path, branch, base_ref)

    def remove(self, worktree: Worktree, *, delete_branch: bool = False) -> bool:
        """Remove a disposable worktree and optionally its generated branch.

        Failed runs retain their branch for resume. Completed runs can delete
        it safely, preventing unbounded ``repo-dev/<run-id>`` accumulation.
        The method remains best-effort like the original lifecycle cleanup and
        returns whether all requested cleanup steps succeeded.
        """
        if delete_branch and not worktree.branch.startswith("repo-dev/"):
            raise ValueError("refusing to delete a non-runtime worktree branch")
        path = Path(worktree.path).resolve()
        if path == self.repository or self.root not in path.parents:
            raise ValueError("refusing to remove a worktree outside the configured root")
        if delete_branch and not _RUNTIME_BRANCH.fullmatch(worktree.branch):
            raise ValueError("refusing to delete an invalid runtime branch")
        removed = subprocess.run(
            ["git", "-C", str(self.repository), "worktree", "remove", "--force", str(path)],
            capture_output=True, text=True, check=False,
        ).returncode == 0
        if not delete_branch:
            return removed
        deleted = subprocess.run(
            ["git", "-C", str(self.repository), "branch", "-D", worktree.branch],
            capture_output=True, text=True, check=False,
        ).returncode == 0
        return removed and deleted
