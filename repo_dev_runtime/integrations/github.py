"""Explicit, generated-branch-only GitHub PR publisher."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

from ..governance.policy import RuntimePolicy
from ..tools.runner import run_command


class GitHubPublisher:
    def __init__(self, *, policy: RuntimePolicy, token: str | None = None, api_url: str = "https://api.github.com") -> None:
        self.policy = policy
        self.token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN", "")
        self.api_url = api_url.rstrip("/")

    def publish_branch(self, *, repository: str | Path, branch: str, base: str = "main") -> dict[str, object]:
        self.policy.authorize("branch_publish")
        if not re.fullmatch(r"repo-dev/[A-Za-z0-9._/-]+", branch):
            raise PermissionError("only runtime-generated branches may be published")
        result = run_command(["git", "push", "origin", branch], cwd=repository, network_access=True, allow_branch_publish=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[:500] or "generated branch push failed")
        return {"branch": branch, "base": base, "pushed": True}

    def create_pull_request(self, *, repository: str | Path, branch: str, base: str, title: str, body: str) -> dict[str, object]:
        self.policy.authorize("pr_creation")
        if not self.token:
            raise PermissionError("GITHUB_TOKEN or GH_TOKEN is required for PR creation")
        owner, name = self._owner_repo(repository)
        payload = json.dumps({"title": title, "head": branch, "base": base, "body": body}).encode("utf-8")
        request = Request(f"{self.api_url}/repos/{owner}/{name}/pulls", data=payload, method="POST", headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "User-Agent": "repo-dev-runtime"})
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(1_000_001).decode("utf-8"))
        if not isinstance(result, dict) or not result.get("html_url"):
            raise ValueError("GitHub did not return a pull request URL")
        return {"url": result["html_url"], "number": result.get("number"), "branch": branch, "base": base}

    def create_from_worktree(self, *, worktree: str | Path, repository: str | Path, branch: str, base: str, title: str, body: str, allowed_paths: tuple[str, ...], forbidden_paths: tuple[str, ...]) -> dict[str, object]:
        changed = self._changed_paths(worktree)
        if not changed:
            raise ValueError("cannot create a pull request without changes")
        for path in changed:
            if not self._allowed_path(path, allowed_paths, forbidden_paths):
                raise PermissionError(f"changed path is outside policy: {path}")
        add = run_command(["git", "add", "--", *changed], cwd=worktree, timeout_s=30)
        if add.returncode != 0:
            raise RuntimeError(add.stderr[:500] or "git add failed")
        commit = run_command(["git", "commit", "-m", title], cwd=worktree, timeout_s=60)
        if commit.returncode != 0:
            raise RuntimeError(commit.stderr[:500] or "git commit failed")
        self.publish_branch(repository=worktree, branch=branch, base=base)
        return self.create_pull_request(repository=repository, branch=branch, base=base, title=title, body=body)

    @staticmethod
    def _changed_paths(worktree: str | Path) -> list[str]:
        import subprocess
        result = subprocess.run(["git", "-C", str(Path(worktree).resolve()), "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True, timeout=20, check=False)
        if result.returncode != 0:
            raise RuntimeError("unable to inspect worktree")
        paths: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            raw = line[3:]
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            paths.append(raw.replace("\\", "/"))
        return paths

    @staticmethod
    def _allowed_path(path: str, allowed: tuple[str, ...], forbidden: tuple[str, ...]) -> bool:
        parts = set(Path(path).parts)
        if any(item in parts or path.startswith(item.rstrip("/") + "/") for item in forbidden):
            return False
        return "." in allowed or any(path == item or path.startswith(item.rstrip("/") + "/") for item in allowed)

    def _owner_repo(self, repository: str | Path) -> tuple[str, str]:
        import subprocess
        result = subprocess.run(["git", "-C", str(Path(repository).resolve()), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10, check=False)
        remote = result.stdout.strip()
        match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote)
        if result.returncode != 0 or not match:
            raise ValueError("origin is not a GitHub remote")
        return match.group(1), match.group(2)
