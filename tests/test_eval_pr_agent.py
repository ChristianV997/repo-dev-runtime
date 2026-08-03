"""Tests for repo_dev_runtime.eval.pr_agent.PRAgentReviewAdapter."""
from __future__ import annotations

import sys

from repo_dev_runtime.eval.conformance import assert_no_forbidden_capabilities, assert_reviewer_contract
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


def test_oversized_output_on_success_is_classified_output_limit():
    script = "print('x' * 2000)"
    adapter = PRAgentReviewAdapter(command=[sys.executable, "-c", script], enabled=True, max_output_bytes=1_024)

    result = adapter.review(_request())
    assert result.status == "failed"
    assert result.error_type == "output_limit"
    assert result.raw_output == ""  # never persisted when oversized


def test_oversized_output_on_failing_exit_is_still_classified_output_limit():
    # Regression test: previously the output_limit check only ran when the
    # subprocess exited 0, so a failing bridge with oversized stdout was
    # misclassified as bridge_exit instead.
    script = "import sys; print('x' * 2000); sys.exit(1)"
    adapter = PRAgentReviewAdapter(command=[sys.executable, "-c", script], enabled=True, max_output_bytes=1_024)

    result = adapter.review(_request())
    assert result.status == "failed"
    assert result.error_type == "output_limit"


def test_bridge_exit_classified_when_output_is_within_budget():
    script = "import sys; sys.exit(1)"
    adapter = PRAgentReviewAdapter(command=[sys.executable, "-c", script], enabled=True)

    result = adapter.review(_request())
    assert result.status == "failed"
    assert result.error_type == "bridge_exit"


def test_subprocess_timeout_is_classified_failed_with_timeout_error_type():
    script = "import time; time.sleep(5)"
    adapter = PRAgentReviewAdapter(command=[sys.executable, "-c", script], enabled=True)
    short_request = EvalRequest.create(kind="reviewer", objective="probe", diff="x", timeout_s=0.2)

    result = adapter.review(short_request)
    assert result.status == "failed"
    assert result.error_type == "timeout"


def test_os_error_launching_subprocess_is_classified_failed():
    adapter = PRAgentReviewAdapter(command=["/this/binary/does/not/exist/anywhere"], enabled=True)

    result = adapter.review(_request())
    assert result.status == "failed"
    assert result.error_type == "FileNotFoundError"


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
    # Delegates to the shared conformance kit so this boundary rule has a
    # single definition that real adapters reuse.
    assert_no_forbidden_capabilities(PRAgentReviewAdapter(command=["pr-agent-cli"], enabled=True), label="pr_agent")


def test_real_adapter_satisfies_the_shared_reviewer_contract(monkeypatch):
    monkeypatch.delenv("DEV_RUNTIME_PR_AGENT", raising=False)
    monkeypatch.delenv("PR_AGENT_COMMAND", raising=False)
    assert_reviewer_contract(PRAgentReviewAdapter().review, label="pr_agent")


def test_command_configurable_via_pr_agent_command_env_shell_string(monkeypatch):
    monkeypatch.setenv("PR_AGENT_COMMAND", "pr-agent-cli --review --json")
    adapter = PRAgentReviewAdapter(enabled=True)

    assert adapter.command == ("pr-agent-cli", "--review", "--json")


def test_command_configurable_via_pr_agent_command_env_json_array(monkeypatch):
    monkeypatch.setenv("PR_AGENT_COMMAND", '["pr-agent-cli", "--review"]')
    adapter = PRAgentReviewAdapter(enabled=True)

    assert adapter.command == ("pr-agent-cli", "--review")


def test_explicit_command_argument_overrides_env(monkeypatch):
    monkeypatch.setenv("PR_AGENT_COMMAND", "from-env")
    adapter = PRAgentReviewAdapter(command=["from-argument"], enabled=True)

    assert adapter.command == ("from-argument",)


def test_required_credential_configurable_via_env(monkeypatch):
    monkeypatch.setenv("PR_AGENT_COMMAND", "pr-agent-cli")
    monkeypatch.setenv("PR_AGENT_REQUIRED_CREDENTIAL", "PR_AGENT_TOKEN")
    monkeypatch.delenv("PR_AGENT_TOKEN", raising=False)
    adapter = PRAgentReviewAdapter(enabled=True)

    assert adapter.required_credential == "PR_AGENT_TOKEN"
    result = adapter.review(_request())
    assert result.status == "blocked"
    assert result.error_type == "credential_missing"


def test_no_env_configuration_leaves_command_unset(monkeypatch):
    monkeypatch.delenv("PR_AGENT_COMMAND", raising=False)
    monkeypatch.delenv("PR_AGENT_REQUIRED_CREDENTIAL", raising=False)
    adapter = PRAgentReviewAdapter(enabled=True)

    assert adapter.command == ()
    assert adapter.required_credential == ""


def test_review_denied_when_policy_disallows_it():
    from repo_dev_runtime.governance.policy import RuntimePolicy

    adapter = PRAgentReviewAdapter(command=[sys.executable, "-c", "pass"], enabled=True, policy=RuntimePolicy())
    result = adapter.review(_request())

    assert result.status == "blocked"
    assert result.error_type == "policy_denied"


def test_review_succeeds_with_an_explicitly_authorized_policy():
    from repo_dev_runtime.governance.policy import RuntimePolicy

    script = (
        "import json; print(json.dumps({'schema': 'RepoDev.ReviewVerdict.v1', "
        "'approved': True, 'summary': 'ok', 'findings': []}))"
    )
    adapter = PRAgentReviewAdapter(
        command=[sys.executable, "-c", script], enabled=True,
        policy=RuntimePolicy(network_access=True, allow_external_provider_benchmark=True),
    )
    result = adapter.review(_request())

    assert result.status == "succeeded"


def test_fake_pr_agent_adapter_is_deterministic():
    adapter = FakePRAgentAdapter(approved=False, findings=[{"severity": "high", "path": "x.py", "message": "bad"}])
    result = adapter(_request())

    assert result.status == "succeeded"
    assert result.normalized["approved"] is False
    assert result.normalized["findings"]
