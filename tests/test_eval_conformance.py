"""Tests for repo_dev_runtime.eval.conformance — the reusable contract
assertions real provider adapters should call instead of re-deriving them."""
from __future__ import annotations

import subprocess

import pytest

from repo_dev_runtime.eval.conformance import (
    assert_context_provider_contract,
    assert_development_runtime_contract,
    assert_disabled_runtime_contract,
    assert_forbidden_path_respected,
    assert_no_credential_leak,
    assert_no_forbidden_capabilities,
    assert_reviewer_contract,
)
from repo_dev_runtime.eval.context_providers import StaticMapContextProvider
from repo_dev_runtime.eval.fakes import (
    FakePRAgentAdapter,
    FakeRepoAgentContextProvider,
    FakeCodingProvider,
    ProviderTurn,
)
from repo_dev_runtime.eval.models import EvalRequest, EvalResult
from repo_dev_runtime.eval.pr_agent import PRAgentReviewAdapter


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "--quiet"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--quiet", "-m", "init"], check=True)
    return tmp_path


# --- DevelopmentRuntime contract -------------------------------------------------


def test_development_runtime_contract_passes_for_a_conforming_provider(git_repo):
    assert_development_runtime_contract(
        lambda: FakeCodingProvider((ProviderTurn(kind="give_up"),)),
        repository=git_repo,
    )


def test_disabled_runtime_contract_proves_ollama_cannot_mutate_checkout(git_repo):
    from repo_dev_runtime.runtimes.ollama import OllamaRuntime

    assert_disabled_runtime_contract(lambda: OllamaRuntime(enabled=False), repository=git_repo, label="ollama")


def test_disabled_runtime_contract_rejects_a_provider_that_mutates(git_repo):
    class MutatingDisabled:
        name = "mutating-disabled"

        def execute(self, task):
            (git_repo / "unexpected.txt").write_text("mutation\n", encoding="utf-8")
            from repo_dev_runtime.contracts.models import DevResult

            return DevResult(task.task_id, self.name, "skipped")

    with pytest.raises(AssertionError, match="mutated the checkout"):
        assert_disabled_runtime_contract(MutatingDisabled, repository=git_repo)


def test_development_runtime_contract_rejects_a_provider_that_raises(git_repo):
    class Raising:
        name = "raising"

        def health(self):
            from repo_dev_runtime.contracts.models import RuntimeHealth

            return RuntimeHealth(self.name, True, True)

        def execute(self, task):
            raise RuntimeError("boom")

    with pytest.raises(AssertionError, match="must return a failed DevResult instead"):
        assert_development_runtime_contract(Raising, repository=git_repo)


def test_development_runtime_contract_rejects_a_provider_missing_execute(git_repo):
    class Incomplete:
        name = "incomplete"

        def health(self):
            from repo_dev_runtime.contracts.models import RuntimeHealth

            return RuntimeHealth(self.name, True, True)

    with pytest.raises(AssertionError, match="must expose execute"):
        assert_development_runtime_contract(Incomplete, repository=git_repo)


# --- Reviewer contract -----------------------------------------------------------


def test_reviewer_contract_passes_for_the_fake_adapter():
    assert_reviewer_contract(FakePRAgentAdapter(approved=True))


def test_reviewer_contract_passes_for_a_disabled_real_adapter(monkeypatch):
    monkeypatch.delenv("DEV_RUNTIME_PR_AGENT", raising=False)
    monkeypatch.delenv("PR_AGENT_COMMAND", raising=False)
    assert_reviewer_contract(PRAgentReviewAdapter().review)


def test_reviewer_contract_rejects_a_normalized_verdict_on_a_failed_result():
    def bad_reviewer(request: EvalRequest) -> EvalResult:
        # Fails closed incorrectly: emits a verdict alongside a failure.
        return EvalResult(request_id=request.request_id, provider="bad", status="failed", normalized={"approved": True})

    with pytest.raises(AssertionError, match="fail closed"):
        assert_reviewer_contract(bad_reviewer)


def test_reviewer_contract_rejects_success_without_a_normalized_verdict():
    def bad_reviewer(request: EvalRequest) -> EvalResult:
        return EvalResult(request_id=request.request_id, provider="bad", status="succeeded", normalized={})

    with pytest.raises(AssertionError, match="normalized boolean 'approved'"):
        assert_reviewer_contract(bad_reviewer)


def test_reviewer_contract_rejects_a_reviewer_that_raises():
    def raising_reviewer(request: EvalRequest) -> EvalResult:
        raise RuntimeError("boom")

    with pytest.raises(AssertionError, match="must return a failed/blocked EvalResult instead"):
        assert_reviewer_contract(raising_reviewer)


# --- Forbidden capabilities ------------------------------------------------------


def test_no_forbidden_capabilities_passes_for_the_real_reviewer_adapter():
    assert_no_forbidden_capabilities(PRAgentReviewAdapter(), label="pr_agent")


def test_no_forbidden_capabilities_detects_an_escaped_boundary():
    class Overreaching:
        name = "overreaching"

        def create_pull_request(self):  # pragma: no cover - never called
            ...

    with pytest.raises(AssertionError, match="forbidden capabilities"):
        assert_no_forbidden_capabilities(Overreaching(), label="overreaching")


# --- Credential leak -------------------------------------------------------------


def test_no_credential_leak_passes_when_redaction_scrubs_the_sentinel():
    assert_no_credential_leak("api_key: hunter2", secret="hunter2")
    assert_no_credential_leak({"telemetry": {"token": "hunter2"}}, secret="hunter2")


def test_no_credential_leak_detects_an_unredactable_sentinel():
    with pytest.raises(AssertionError, match="leaked the secret sentinel"):
        assert_no_credential_leak("the value is hunter2 in plain prose", secret="hunter2")


# --- Context provider contract ---------------------------------------------------


def test_context_provider_contract_passes_for_the_static_map(git_repo):
    assert_context_provider_contract(StaticMapContextProvider(), root=git_repo)


def test_context_provider_contract_passes_for_a_fake_provider(git_repo):
    assert_context_provider_contract(FakeRepoAgentContextProvider(), root=git_repo)


def test_context_provider_contract_rejects_a_wrong_return_shape(git_repo):
    class WrongShape:
        name = "wrong_shape"

        def capabilities(self):
            return {"vendored": False}

        def build(self, root, *, objective, allowed_paths, forbidden_paths, max_bytes):
            return "just one string"

    with pytest.raises(AssertionError, match="2-tuple"):
        assert_context_provider_contract(WrongShape(), root=git_repo)


def test_context_provider_contract_rejects_missing_capability_metadata(git_repo):
    class NoMetadata:
        name = "no_metadata"

        def capabilities(self):
            return {}

        def build(self, root, *, objective, allowed_paths, forbidden_paths, max_bytes):
            return "", ""

    with pytest.raises(AssertionError, match="'vendored' flag"):
        assert_context_provider_contract(NoMetadata(), root=git_repo)


def test_static_map_respects_forbidden_paths(git_repo):
    secrets = git_repo / "secretstuff"
    secrets.mkdir()
    (secrets / "creds.py").write_text("TOKEN = 'x'\n", encoding="utf-8")

    assert_forbidden_path_respected(StaticMapContextProvider(), root=git_repo, forbidden_segment="secretstuff")
