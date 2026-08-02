"""Disposable Git worktree lifecycle with path-bound metadata."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


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
        self.root.mkdir(parents=True, exist_ok=True)
        path = (self.root / run_id).resolve()
        if self.root not in path.parents or path == self.repository:
            raise ValueError("worktree path must remain inside the configured worktree root")
        branch = f"repo-dev/{run_id}"
        completed = subprocess.run(["git", "-C", str(self.repository), "worktree", "add", "-b", branch, str(path), base_ref], capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip()[:500] or "git worktree creation failed")
        return Worktree(path, branch, base_ref)

    def remove(self, worktree: Worktree) -> None:
        subprocess.run(["git", "-C", str(self.repository), "worktree", "remove", "--force", str(worktree.path)], capture_output=True, text=True, check=False)
