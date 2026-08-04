import json

import pytest
from concurrent.futures import ThreadPoolExecutor

from repo_dev_runtime.discovery import validate_consumer
from repo_dev_runtime.governance.artifacts import RunEnvelope
from repo_dev_runtime.governance.command_policy import CommandPolicy, evaluate_command
from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.governance.provenance import load_provenance
from repo_dev_runtime.scheduler import SchedulePlan, TaskStateStore
from repo_dev_runtime.tools.diagnostics import actionable_output


def test_provenance_inventory_is_valid():
    components = load_provenance("provenance/source_inventory.json")
    assert components
    assert any(item.status == "excluded" for item in components)


def test_command_policy_blocks_mutation_and_network():
    assert not evaluate_command("git push origin main").allowed
    assert not evaluate_command("curl https://example.com").allowed
    assert evaluate_command("curl https://example.com", CommandPolicy(allow_network=True)).allowed
    assert evaluate_command("Invoke-WebRequest https://example.com", CommandPolicy(allow_network=True)).allowed
    assert evaluate_command("git status").allowed
    assert evaluate_command("git push", CommandPolicy(allow_network=True)).allowed is False
    assert evaluate_command("aws s3 cp local s3://bucket/path", CommandPolicy(allow_network=True)).allowed is False


def test_command_policy_blocks_package_manager_network_installs():
    """Regression test: the network blocklist only covered curl/wget/etc.
    and the literal "python -m pip install" string, but not bare
    "pip install", "npm install", and similar package-manager fetches —
    a real fail-open path for the manifest test/lint/security commands
    that route through this policy (tools/runner.run_command)."""
    for command in (
        "pip install requests",
        "pip3 install requests",
        "npm install",
        "npm ci",
        "yarn add left-pad",
        "pnpm install",
        "poetry install",
        "gem install rails",
        "cargo install ripgrep",
        "go install example.com/tool@latest",
    ):
        assert not evaluate_command(command).allowed, f"{command!r} should be blocked without network access"
        assert evaluate_command(command, CommandPolicy(allow_network=True)).allowed, f"{command!r} should be allowed with network access"


def test_scheduler_state_is_resumable_and_atomic(tmp_path):
    plan = SchedulePlan("python -m pytest", dry_run_command="python -m pytest --collect-only")
    plan.validate()
    store = TaskStateStore(tmp_path / "state.json")
    assert store.update("task-1", "running", attempt=1)["status"] == "running"
    assert store.load()["task-1"]["attempt"] == 1
    assert store.update("task-1", "succeeded")["status"] == "succeeded"


def test_scheduler_state_serializes_concurrent_updates(tmp_path):
    store = TaskStateStore(tmp_path / "state.json")

    def update(index):
        return store.update(f"task-{index}", "succeeded", worker=index)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update, range(24)))

    state = store.load()
    assert len(state) == 24
    assert state["task-17"] == {"status": "succeeded", "worker": 17}


def test_scheduler_state_rejects_symlinked_state_file(tmp_path):
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "state.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ValueError, match="regular file"):
        TaskStateStore(link).load()


def test_task_state_claim_is_atomic_check_and_set(tmp_path):
    """Regression test: TaskStateStore exposed only load() and update(),
    each taking the file lock separately, so a caller had to do
    check-then-claim across two acquisitions. Two concurrent invocations
    sharing a schedule key could both observe an unclaimed task and both
    proceed — a TOCTOU race callers could not close from the outside.
    claim() performs the read and the conditional write under one lock."""
    store = TaskStateStore(tmp_path / "state.json")
    guarded = ("running", "succeeded")

    first = store.claim("nightly", "running", unless_status=guarded, run_id="a")
    assert first is not None and first["status"] == "running"

    # A second claimant must lose, and must not overwrite the winner.
    assert store.claim("nightly", "running", unless_status=guarded, run_id="b") is None
    assert store.load()["nightly"]["run_id"] == "a"

    # A finished task stays claimed; an unguarded status can still be retried.
    store.update("nightly", "succeeded")
    assert store.claim("nightly", "running", unless_status=guarded) is None
    store.update("nightly", "failed")
    assert store.claim("nightly", "running", unless_status=guarded) is not None


def test_concurrent_claims_elect_exactly_one_winner(tmp_path):
    """The TOCTOU this closes is only observable under real concurrency:
    with load()+update() every racer sees an unclaimed task and proceeds."""
    store = TaskStateStore(tmp_path / "state.json")

    def attempt(worker: int):
        return store.claim("nightly", "running", unless_status=("running", "succeeded"), run_id=str(worker))

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))

    winners = [item for item in outcomes if item is not None]
    assert len(winners) == 1, f"exactly one claimant must win, got {len(winners)}"
    # The stored state agrees with the single winner (no lost update).
    assert store.load()["nightly"]["run_id"] == winners[0]["run_id"]


def test_task_state_claim_validates_like_update(tmp_path):
    store = TaskStateStore(tmp_path / "state.json")

    with pytest.raises(ValueError, match="invalid task state"):
        store.claim("", "running")
    with pytest.raises(ValueError, match="invalid task state"):
        store.claim("nightly", "not-a-status")


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


def test_event_data_is_redacted_before_persistence(tmp_path):
    envelope = RunEnvelope("one", tmp_path / "one")

    envelope.event("provider_finished", access_token="event-secret")

    persisted = (tmp_path / "one" / "events.jsonl").read_text(encoding="utf-8")
    assert "event-secret" not in persisted
    assert "[REDACTED]" in persisted


def test_tampered_event_log_is_rejected_on_load(tmp_path):
    envelope = RunEnvelope("one", tmp_path / "one")
    envelope.event("task_started", task_id="x")
    event_path = tmp_path / "one" / "events.jsonl"
    event_path.write_text(event_path.read_text(encoding="utf-8").replace('"task_started"', '"task_finished"'), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        RunEnvelope("one", tmp_path / "one")


def test_artifact_names_cannot_escape_the_run_envelope(tmp_path):
    envelope = RunEnvelope("one", tmp_path / "one")

    with pytest.raises(ValueError):
        envelope.write_json("../outside.json", {"safe": True})
    with pytest.raises(ValueError):
        envelope.write_json("C:\\outside.json", {"safe": True})


def test_nested_artifact_checksums_round_trip(tmp_path):
    envelope = RunEnvelope("one", tmp_path / "one")
    envelope.write_json("nested/result.json", {"status": "ok"})
    envelope.finalize({"status": "ok"})

    envelope.verify_checksums(required=("nested/result.json",))


def test_event_log_is_bounded_against_unbounded_growth(tmp_path, monkeypatch):
    """Regression test: events.jsonl is append-only and grows across every
    --resume of the same run, but no cap existed anywhere on its size -
    unlike every other external-facing size bound in this codebase
    (TaskStateStore._MAX_STATE_BYTES, Aider's _MAX_INPUT_BYTES,
    run_command's max_output_bytes). A run pushed past a sane bound must
    fail closed with a clear error rather than growing the log forever."""
    from repo_dev_runtime.governance import artifacts as artifacts_module

    monkeypatch.setattr(artifacts_module, "_MAX_EVENTS_BYTES", 200)
    envelope = RunEnvelope("one", tmp_path / "one")

    with pytest.raises(ValueError, match="event log exceeds maximum size"):
        for _ in range(50):
            envelope.event("task_started", task_id="x", detail="padding" * 5)


def test_run_envelope_finalize_is_bounded_against_unbounded_growth(tmp_path, monkeypatch):
    """Regression test: total run-artifact bytes had no cap anywhere,
    unlike this codebase's other explicit external-facing size bounds.
    finalize() already walks the whole run directory once to compute
    checksums, so the bound is enforced there at zero extra I/O cost."""
    from repo_dev_runtime.governance import artifacts as artifacts_module

    monkeypatch.setattr(artifacts_module, "_MAX_RUN_ARTIFACT_BYTES", 100)
    envelope = RunEnvelope("one", tmp_path / "one")
    envelope.write_json("big.json", {"data": "x" * 1000})

    with pytest.raises(ValueError, match="run envelope exceeds maximum size"):
        envelope.finalize({"status": "ok"})


def test_run_envelope_rejects_symlinked_artifacts(tmp_path):
    envelope = RunEnvelope("one", tmp_path / "one")
    envelope.event("started")
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "one" / "linked.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    with pytest.raises(ValueError, match="symlink"):
        envelope.finalize({"status": "blocked"})


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


def test_unknown_capability_is_denied_fail_closed():
    with pytest.raises(PermissionError, match="unsupported capability"):
        RuntimePolicy().authorize("future_unreviewed_capability")
