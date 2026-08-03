import json

import pytest

from repo_dev_runtime.discovery import validate_consumer
from repo_dev_runtime.governance.artifacts import RunEnvelope
from repo_dev_runtime.governance.command_policy import CommandPolicy, evaluate_command
from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.governance.provenance import load_provenance
from repo_dev_runtime.governance.roles import role_map
from repo_dev_runtime.scheduler import SchedulePlan, TaskStateStore
from repo_dev_runtime.tools.diagnostics import actionable_output


def test_provenance_inventory_is_valid():
    components = load_provenance("provenance/source_inventory.json")
    assert components
    assert any(item.status == "excluded" for item in components)


def test_command_policy_blocks_mutation_and_network():
    assert not evaluate_command("git push origin main").allowed
    assert not evaluate_command("curl https://example.com").allowed
    assert evaluate_command("git status").allowed
    assert evaluate_command("git push", CommandPolicy(allow_network=True)).allowed is False


def test_roles_have_review_boundary():
    roles = role_map()
    assert set(roles) == {"planner", "implementer", "tester", "reviewer", "integrator"}
    assert roles["integrator"].requires_human_review


def test_scheduler_state_is_resumable_and_atomic(tmp_path):
    plan = SchedulePlan("python -m pytest", dry_run_command="python -m pytest --collect-only")
    plan.validate()
    store = TaskStateStore(tmp_path / "state.json")
    assert store.update("task-1", "running", attempt=1)["status"] == "running"
    assert store.load()["task-1"]["attempt"] == 1
    assert store.update("task-1", "succeeded")["status"] == "succeeded"


def test_event_hash_is_stable_for_same_event_shape(tmp_path):
    first = RunEnvelope("one", tmp_path / "one")
    second = RunEnvelope("two", tmp_path / "two")
    first.event("task_started", task_id="x")
    second.event("task_started", task_id="x")
    assert first.events[0]["event_hash"] == second.events[0]["event_hash"]
    assert "created_at" in first.events[0]


def test_event_log_continues_after_resume(tmp_path):
    first = RunEnvelope("one", tmp_path / "one")
    first.event("started")
    resumed = RunEnvelope("one", tmp_path / "one")
    resumed.event("continued")
    lines = (tmp_path / "one" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["sequence"] == 1


def test_diagnostics_are_bounded():
    result = actionable_output("\n".join(f"line-{i}" for i in range(100)), max_lines=3, max_chars=100)
    assert result.splitlines() == ["line-97", "line-98", "line-99"]


def test_consumer_validation_is_read_only(tmp_path):
    (tmp_path / ".git").mkdir()
    result = validate_consumer(tmp_path)
    assert not result["valid"]
    assert not (tmp_path / ".dev-runtime").exists()


def test_external_provider_benchmark_disabled_by_default():
    policy = RuntimePolicy()
    with pytest.raises(PermissionError):
        policy.authorize("external_provider_benchmark")


def test_external_provider_benchmark_allowed_when_enabled_and_approved():
    policy = RuntimePolicy(allow_external_provider_benchmark=True, network_access=True)
    policy.authorize("external_provider_benchmark", approved=True)  # must not raise


def test_external_provider_benchmark_requires_explicit_approval_even_when_enabled():
    # Regression test: allow_external_provider_benchmark=True alone must not
    # be sufficient — this is what would have caught the original bug where
    # cli.py constructed the policy with the flag already True and then
    # authorized against that same object, making the check tautological.
    policy = RuntimePolicy(allow_external_provider_benchmark=True, network_access=True)
    with pytest.raises(PermissionError):
        policy.authorize("external_provider_benchmark", approved=False)


def test_external_provider_benchmark_requires_network_access():
    with pytest.raises(ValueError, match="network_access"):
        RuntimePolicy(allow_external_provider_benchmark=True, network_access=False).validate()


def test_pr_agent_review_disabled_by_default():
    policy = RuntimePolicy()
    with pytest.raises(PermissionError):
        policy.authorize("pr_agent_review")


def test_pr_agent_review_requires_explicit_approval():
    policy = RuntimePolicy(allow_external_provider_benchmark=True, network_access=True)
    with pytest.raises(PermissionError):
        policy.authorize("pr_agent_review", approved=False)
    policy.authorize("pr_agent_review", approved=True)  # must not raise
