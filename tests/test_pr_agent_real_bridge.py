"""Real, non-mocked test of scripts/pr_agent_bridge.py against the actual
`pr-agent` package (pip install pr-agent GitPython), pointed at a local
OpenAI-compatible stub server so no real LLM credential or network call is
needed. Skipped entirely (not failed) in any environment without pr-agent
installed — this is an operator-installed bridge, not a repo_dev_runtime
dependency. See docs/pr-agent-real-integration.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pr_agent")

from repo_dev_runtime.eval.pr_agent import PRAgentReviewAdapter
from repo_dev_runtime.eval.models import EvalRequest
from repo_dev_runtime.review import parse_review_verdict

from tests.support.live_servers import json_response, stub_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BRIDGE_SCRIPT = _REPO_ROOT / "scripts" / "pr_agent_bridge.py"

_NEW_FILE_DIFF = (
    "diff --git a/new_feature.py b/new_feature.py\n"
    "new file mode 100644\n"
    "index 0000000..4693ad3\n"
    "--- /dev/null\n"
    "+++ b/new_feature.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def add(a, b):\n"
    "+    return a + b\n"
)

_CLEAN_REVIEW_YAML = """```yaml
review:
  estimated_effort_to_review_1-5: 2
  relevant_tests: "No"
  key_issues_to_review: []
  security_concerns: "No"
```"""

_ISSUES_REVIEW_YAML = """```yaml
review:
  estimated_effort_to_review_1-5: 4
  relevant_tests: "No"
  key_issues_to_review:
    - relevant_file: "new_feature.py"
      issue_header: "Possible Bug"
      issue_content: "add() does not validate input types"
      start_line: 1
      end_line: 2
  security_concerns: "Sensitive information exposure: none found, but no input validation"
```"""


def _llm_stub(content: str):
    return {
        ("GET", "/v1/models"): json_response(200, {"data": []}),
        ("POST", "/v1/chat/completions"): json_response(200, {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "model": "gpt-4-turbo-2024-04-09",
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }),
    }


def _run_bridge(base_url: str, diff: str, *, base_files: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    payload = json.dumps({"request_id": "test-1", "objective": "review this change", "diff": diff, "base_files": base_files or {}})
    return subprocess.run(
        [sys.executable, str(_BRIDGE_SCRIPT)],
        input=payload, capture_output=True, text=True, timeout=30,
        env={"PR_AGENT_OPENAI_API_BASE": base_url, "PATH": __import__("os").environ.get("PATH", "")},
    )


def test_bridge_produces_a_real_approved_verdict_via_real_pr_agent():
    with stub_server(_llm_stub(_CLEAN_REVIEW_YAML)) as server:
        result = _run_bridge(server.base_url, _NEW_FILE_DIFF)

    assert result.returncode == 0, result.stderr
    verdict = parse_review_verdict(result.stdout)
    assert verdict.approved is True
    assert verdict.findings == ()


def test_bridge_produces_a_real_rejected_verdict_with_findings():
    with stub_server(_llm_stub(_ISSUES_REVIEW_YAML)) as server:
        result = _run_bridge(server.base_url, _NEW_FILE_DIFF)

    assert result.returncode == 0, result.stderr
    verdict = parse_review_verdict(result.stdout)
    assert verdict.approved is False
    assert len(verdict.findings) == 1
    assert verdict.findings[0].severity == "high"
    assert "validate input" in verdict.findings[0].message


def test_bridge_fails_closed_on_malformed_llm_output_never_fabricates_approval():
    with stub_server(_llm_stub("This is not YAML at all, just prose.")) as server:
        result = _run_bridge(server.base_url, _NEW_FILE_DIFF)

    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_bridge_fails_closed_on_unapplicable_diff():
    modify_existing_file_diff = (
        "diff --git a/does_not_exist.py b/does_not_exist.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/does_not_exist.py\n"
        "+++ b/does_not_exist.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old line\n"
        "+new line\n"
    )
    with stub_server(_llm_stub(_CLEAN_REVIEW_YAML)) as server:
        result = _run_bridge(server.base_url, modify_existing_file_diff)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "could not be applied" in result.stderr


def test_bridge_reviews_modified_file_when_given_its_bounded_baseline():
    modify_existing_file_diff = (
        "diff --git a/validator.py b/validator.py\n"
        "index 7b82c68..36f17d0 100644\n"
        "--- a/validator.py\n"
        "+++ b/validator.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def validate(value):\n"
        "-    return value\n"
        "+    return value.strip()\n"
    )
    with stub_server(_llm_stub(_CLEAN_REVIEW_YAML)) as server:
        result = _run_bridge(
            server.base_url,
            modify_existing_file_diff,
            base_files={"validator.py": "def validate(value):\n    return value\n"},
        )

    assert result.returncode == 0, result.stderr
    assert parse_review_verdict(result.stdout).approved is True


def test_pr_agent_review_adapter_consumes_the_real_bridge_unmodified():
    """Proves PRAgentReviewAdapter (no code changes) correctly consumes a
    real bridge script's real output, end to end."""
    with stub_server(_llm_stub(_CLEAN_REVIEW_YAML)) as server:
        adapter = PRAgentReviewAdapter(
            command=(sys.executable, str(_BRIDGE_SCRIPT)),
            enabled=True,
        )
        # PRAgentReviewAdapter's own subprocess env is built from an
        # allowlist keyed on the PR_AGENT_ prefix, so this is exactly how a
        # real operator would configure it (PR_AGENT_OPENAI_API_BASE).
        import os
        os.environ["PR_AGENT_OPENAI_API_BASE"] = server.base_url
        try:
            request = EvalRequest.create(kind="reviewer", objective="review this change", diff=_NEW_FILE_DIFF)
            result = adapter.review(request)
        finally:
            os.environ.pop("PR_AGENT_OPENAI_API_BASE", None)

    assert result.status == "succeeded", result.error_message
    assert result.normalized["approved"] is True
