"""Tests for repo_dev_runtime.eval.pr_agent.PRAgentReviewAdapter."""
from __future__ import annotations

import sys

from repo_dev_runtime.eval.fakes import FakePRAgentAdapter
from repo_dev_runtime.eval.models import EvalRequest
from repo_dev_runtime.eval.pr_agent import PRAgentReviewAdapter


def _request(diff="--- a\n+++ b\n") -> EvalRequest:
    return EvalRequest.create(kind="reviewer", objective="review a diff", diff=diff)


def test_disabled_by_default_when_no_env_flag(monkeypatch):
    monkeypatch.delenv("DEV_RUNTIME_PR_AGENT", raising=False)
    adapter = PRAgentReviewAdapter(command=["pr-agent-cli"])

    assert adapter.enabled is False
    health = adapter.health()
    assert health.configured is False

    result = adapter.review(_request())
    assert result.status == "skipped"
    assert result.error_type == "provider_disabled"


def test_blocked_when_enabled_but_no_command_configured():
    adapter = PRAgentReviewAdapter(command=None, enabled=True)

    result = adapter.review(_request())
    assert result.status == "blocked"
    assert result.error_type == "command_not_configured"


def test_blocked_on_missing_required_credential(monkeypatch):
    monkeypatch.delenv("PR_AGENT_TOKEN", raising=False)
    adapter = PRAgentReviewAdapter(command=["pr-agent-cli"], enabled=True, required_credential="PR_AGENT_TOKEN")

    result = adapter.review(_request())
    assert result.status == "blocked"
    assert result.error_type == "credential_missing"
    assert "PR_AGENT_TOKEN" in result.error_message


def test_malformed_provider_output_fails_closed():
    script = "import sys; print('not valid json at all')"
    adapter = PRAgentReviewAdapter(command=[sys.executable, "-c", script], enabled=True)

    result = adapter.review(_request())
    assert result.status == "failed"
    assert result.normalized == {}
    assert result.raw_output  # raw output retained separately from (empty) normalized verdict


def test_valid_verdict_normalized_and_raw_kept_separately():
    script = (
        "import sys, json\n"
        "print(json.dumps({'schema': 'RepoDev.ReviewVerdict.v1', 'approved': False, "
        "'summary': 'unsafe change', 'findings': [{'severity': 'high', 'path': 'x.py', 'message': 'bad'}]}))"
    )
    adapter = PRAgentReviewAdapter(command=[sys.executable, "-c", script], enabled=True)

    result = adapter.review(_request())
    assert result.status == "succeeded"
    assert result.normalized["approved"] is False
    assert result.raw_output != ""
    assert result.raw_output != str(result.normalized)


def test_adapter_has_no_apply_merge_push_or_pr_capability():
    adapter = PRAgentReviewAdapter(command=["pr-agent-cli"], enabled=True)
    forbidden_names = {"apply", "apply_edits", "merge", "push", "create_pull_request", "create_pr", "publish_branch"}
    assert not (forbidden_names & set(dir(adapter)))


def test_fake_pr_agent_adapter_is_deterministic():
    adapter = FakePRAgentAdapter(approved=False, findings=[{"severity": "high", "path": "x.py", "message": "bad"}])
    result = adapter(_request())

    assert result.status == "succeeded"
    assert result.normalized["approved"] is False
    assert result.normalized["findings"]
