#!/usr/bin/env python3
"""Operator-installed bridge: drives the real `pr_agent` package's
LocalGitProvider review flow against a configurable OpenAI-compatible
chat/completions endpoint, and translates its output into this repo's
RepoDev.ReviewVerdict.v1 schema on stdout.

NOT part of the `repo_dev_runtime` package and NOT a dependency of it.
`pr-agent` is a heavy, operator-installed third-party tool
(``pip install pr-agent GitPython``), never vendored here. Configure
`repo_dev_runtime.eval.pr_agent.PRAgentReviewAdapter` to run this script
as its ``command`` (e.g. via ``PR_AGENT_COMMAND``) to use it. See
docs/pr-agent-real-integration.md for the install steps and the documented
approve/reject mapping heuristic (PR-Agent has no native approve/reject
signal — this bridge infers one).

Protocol:
  stdin:  one JSON object: {"request_id", "objective", "diff", "base_files"}
          (exactly what PRAgentReviewAdapter.review() sends).
  stdout: on success, one RepoDev.ReviewVerdict.v1 JSON object.
  exit:   0 on success; non-zero with a stderr message on any failure.
          Never fabricates an "approved" verdict on failure.

Environment:
  PR_AGENT_OPENAI_API_BASE  required. Base URL of an OpenAI-compatible
                            chat/completions endpoint.
  PR_AGENT_OPENAI_API_KEY   optional (default: a placeholder value, fine
                            against a local stub; set a real key yourself
                            if PR_AGENT_OPENAI_API_BASE points at a real
                            provider — that is your choice, not this
                            script's default).
  PR_AGENT_MODEL            optional, defaults to pr-agent's own default
                            model name (litellm routes by name + api_base,
                            not by contacting the real provider first).

The optional ``base_files`` mapping supplies only baseline contents for
files modified by the diff. The bridge materializes those contents in its
scratch repository, so it can review ordinary modified-file patches without
receiving a consumer checkout path. Missing baseline content still makes
``git apply`` fail closed rather than fabricating a verdict.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath

try:
    import pr_agent.git_providers as _git_providers
    from pr_agent.agent.pr_agent import PRAgent, command2class
    from pr_agent.algo.utils import load_yaml
    from pr_agent.config_loader import get_settings
    from pr_agent.git_providers.local_git_provider import LocalGitProvider
    from pr_agent.tools.pr_reviewer import PRReviewer
except ImportError:
    print("pr_agent_bridge requires the pr-agent package: pip install pr-agent GitPython", file=sys.stderr)
    raise SystemExit(2)


class _WorkingLocalGitProvider(LocalGitProvider):
    """pr-agent 0.2.4's LocalGitProvider does not implement four of
    GitProvider's abstract methods (add_eyes_reaction, get_commit_messages,
    get_repo_settings, remove_reaction) and so cannot be instantiated as
    shipped — confirmed by hitting `TypeError: Can't instantiate abstract
    class LocalGitProvider` against the real installed package. This
    subclass supplies the missing methods as harmless no-ops/defaults,
    following the same "not applicable to the local git provider" pattern
    LocalGitProvider already uses for publish_labels/remove_comment. It
    does not modify the installed pr-agent package."""

    def add_eyes_reaction(self, issue_comment_id, disable_eyes: bool = False):
        return None

    def remove_reaction(self, issue_comment_id, reaction_id) -> bool:
        return True

    def get_commit_messages(self) -> str:
        try:
            commits = list(self.repo.iter_commits(f"{self.target_branch_name}..HEAD"))
        except Exception:
            return ""
        return "\n".join(f"{i + 1}. {commit.message}" for i, commit in enumerate(reversed(commits)))

    def get_repo_settings(self):
        return b""


# Patch the registry dict pr_agent's own dispatcher looks up by provider-id
# string (get_git_provider_with_context indexes _GIT_PROVIDERS['local']
# directly), rather than passing our subclass in some other way — pr_agent
# only takes a pr_url/branch name and resolves the provider class itself.
_git_providers._GIT_PROVIDERS["local"] = _WorkingLocalGitProvider

# Captures the raw LLM prediction pr-agent's own PRReviewer computes, so we
# can parse it with pr-agent's own `load_yaml` (the same call
# PRReviewer._prepare_pr_review uses) instead of scraping its rendered,
# emoji-decorated markdown output — which is fragile across versions and,
# when publish_output is on, can also contain a stale "Preparing review..."
# placeholder if the AI call fails after that placeholder is written but
# before the real review (confirmed by reproducing exactly that failure
# mode against a broken LLM endpoint during development).
_CAPTURED: dict[str, str | None] = {"prediction": None}


class _CapturingPRReviewer(PRReviewer):
    async def _prepare_prediction(self, model: str) -> None:
        await super()._prepare_prediction(model)
        _CAPTURED["prediction"] = self.prediction


command2class["review"] = _CapturingPRReviewer
command2class["review_pr"] = _CapturingPRReviewer

# Matches PRReviewer._prepare_pr_review's own load_yaml call exactly, so our
# parse of the raw prediction stays consistent with pr-agent's own parsing.
_YAML_FIX_KEYS = ["estimated_effort_to_review_[1-5]:", "security_concerns:", "key_issues_to_review:",
                  "relevant_file:", "relevant_line:", "suggestion:"]

SEVERITY_DEFAULT = "medium"
SEVERITY_SECURITY = "high"
MAX_EFFORT_FOR_APPROVAL = 3


class BridgeError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise BridgeError(f"command failed: {' '.join(cmd)}\n{result.stderr}")


def _safe_base_path(repo: Path, relative: str) -> Path:
    parsed = PurePosixPath(relative)
    if not relative or not parsed.parts or parsed.is_absolute() or ".." in parsed.parts or "\\" in relative or str(parsed) != relative:
        raise BridgeError("base_files contains an unsafe path")
    path = (repo / relative).resolve()
    if repo not in path.parents:
        raise BridgeError("base_files path escapes the scratch repository")
    return path


def _build_repo(tmp_path: Path, diff: str, base_files: dict[str, str]) -> tuple[Path, str]:
    """Create a real, throwaway git repo with a `main` branch and a
    `review` branch that applies `diff`, for PR-Agent's LocalGitProvider
    to diff. See the module docstring's "Known limitation" note."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "bridge@example.com"], repo)
    _run(["git", "config", "user.name", "pr-agent-bridge"], repo)
    (repo / "README.md").write_text("placeholder\n", encoding="utf-8")
    for relative, content in base_files.items():
        if not isinstance(relative, str) or not isinstance(content, str):
            raise BridgeError("base_files must map paths to text")
        target = _safe_base_path(repo, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-q", "-m", "base"], repo)
    _run(["git", "branch", "-m", "main"], repo)
    _run(["git", "checkout", "-q", "-b", "review"], repo)
    if diff.strip():
        patch_file = tmp_path / "change.patch"
        patch_file.write_text(diff, encoding="utf-8")
        check = subprocess.run(["git", "apply", "--check", str(patch_file)], cwd=repo, capture_output=True, text=True)
        if check.returncode != 0:
            raise BridgeError(
                "diff could not be applied against this bridge's minimal scaffold repo "
                "(only new-file-adding diffs are supported without the original repository "
                f"content; see the module docstring's known limitation): {check.stderr.strip()}"
            )
        _run(["git", "apply", str(patch_file)], repo)
        _run(["git", "add", "-A"], repo)
        _run(["git", "commit", "-q", "-m", "review changes"], repo)
    return repo, "main"


def _configure_pr_agent(api_base: str, api_key: str, model: str) -> None:
    settings = get_settings()
    settings.set("CONFIG.GIT_PROVIDER", "local")
    # We parse the captured raw LLM prediction directly (see _CapturingPRReviewer)
    # rather than pr-agent's rendered review.md, so publishing is unnecessary —
    # keep it off entirely, consistent with this bridge never posting anywhere.
    settings.set("CONFIG.PUBLISH_OUTPUT", False)
    settings.set("CONFIG.MODEL", model)
    settings.set("CONFIG.FALLBACK_MODELS", [])
    settings.set("OPENAI.KEY", api_key)
    # litellm's OpenAI-compatible client appends "/chat/completions" to
    # api_base directly (it does not assume an OpenAI-style base already
    # ending in "/v1") — confirmed by watching a real request against a
    # logging stub arrive at "/chat/completions", not "/v1/chat/completions".
    # PR_AGENT_OPENAI_API_BASE stays a bare host root, consistent with this
    # repo's other *_URL env vars (OLLAMA_URL, DEV_OMNIROUTE_URL); this is
    # the one place that needs the "/v1" suffix.
    settings.set("OPENAI.API_BASE", f"{api_base.rstrip('/')}/v1")


def _read_review() -> dict:
    prediction = _CAPTURED.get("prediction")
    if not prediction or not prediction.strip():
        raise BridgeError("pr-agent produced no prediction (the AI review call failed or returned empty)")
    try:
        data = load_yaml(prediction.strip(), keys_fix_yaml=_YAML_FIX_KEYS, first_key="review", last_key="security_concerns")
    except Exception as exc:  # load_yaml raises broadly on malformed LLM output
        raise BridgeError(f"pr-agent could not parse its own raw prediction as YAML: {exc}\nraw prediction: {prediction[:500]!r}") from exc
    review = data.get("review") if isinstance(data, dict) else None
    if not isinstance(review, dict):
        raise BridgeError(f"pr-agent's parsed prediction had no 'review' mapping: {data!r}")
    return review


def _to_verdict(review: dict) -> dict:
    """Maps PR-Agent's native review fields into RepoDev.ReviewVerdict.v1.
    PR-Agent has no native approve/reject signal, so `approved` is a
    documented heuristic (see docs/pr-agent-real-integration.md): approved
    only if security_concerns is "No", estimated effort is low, and no
    issues were flagged.
    """
    security = str(review.get("security_concerns", "No")).strip()
    effort_raw = review.get("estimated_effort_to_review_1-5", review.get("estimated_effort_to_review", 0))
    try:
        effort = int(effort_raw)
    except (TypeError, ValueError):
        effort = 0
    issues = review.get("key_issues_to_review") or []
    findings = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        findings.append({
            "severity": SEVERITY_SECURITY if security.lower() != "no" else SEVERITY_DEFAULT,
            "path": str(issue.get("relevant_file", "")),
            "message": str(issue.get("issue_content") or issue.get("issue_header") or "unspecified issue"),
        })
    approved = security.lower() == "no" and effort <= MAX_EFFORT_FOR_APPROVAL and not findings
    summary = f"PR-Agent review: effort={effort}/5, security_concerns={security!r}, {len(findings)} issue(s) flagged."
    return {"schema": "RepoDev.ReviewVerdict.v1", "approved": approved, "summary": summary, "findings": findings}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(f"invalid request JSON on stdin: {exc}", file=sys.stderr)
        return 2
    diff = str(payload.get("diff", ""))
    base_files = payload.get("base_files", {})
    if not isinstance(base_files, dict):
        print("base_files must be an object", file=sys.stderr)
        return 2

    api_base = os.environ.get("PR_AGENT_OPENAI_API_BASE")
    if not api_base:
        print("PR_AGENT_OPENAI_API_BASE is required", file=sys.stderr)
        return 2
    api_key = os.environ.get("PR_AGENT_OPENAI_API_KEY", "placeholder-key")
    model = os.environ.get("PR_AGENT_MODEL", "gpt-4-turbo-2024-04-09")

    with tempfile.TemporaryDirectory(prefix="pr-agent-bridge-") as tmp:
        tmp_path = Path(tmp)
        try:
            repo, target_branch = _build_repo(tmp_path, diff, base_files)
        except BridgeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        _configure_pr_agent(api_base, api_key, model)

        cwd = os.getcwd()
        os.chdir(repo)
        try:
            ok = asyncio.run(PRAgent().handle_request(target_branch, ["review"]))
        except Exception as exc:  # pr-agent raises broadly; never fabricate a verdict on failure
            print(f"pr-agent review failed: {exc}", file=sys.stderr)
            return 1
        finally:
            os.chdir(cwd)

        if not ok:
            print("pr-agent declined to run the review command", file=sys.stderr)
            return 1

        try:
            review = _read_review()
        except BridgeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    print(json.dumps(_to_verdict(review), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
