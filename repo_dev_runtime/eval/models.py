"""Typed contracts for the provider-evaluation layer: scorecards, fixture
results, benchmark-only provider specs, and the reviewer/context-provider
request/result envelope. Mirrors the conventions in
``repo_dev_runtime.contracts.models`` (frozen dataclasses, finite-JSON
validation, explicit schema strings, ``to_dict()``).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from ..contracts.models import _finite, now_iso

SCHEMA_SCORECARD = "RepoDev.ProviderScorecard.v1"
SCHEMA_FIXTURE_RESULT = "RepoDev.FixtureBenchmarkResult.v1"
SCHEMA_PROVIDER_SPEC = "RepoDev.BenchmarkProviderSpec.v1"
SCHEMA_EVAL_REQUEST = "RepoDev.EvalRequest.v1"
SCHEMA_EVAL_RESULT = "RepoDev.EvalResult.v1"

# The exact outcome vocabulary the fixture benchmark must distinguish between.
FIXTURE_OUTCOMES = (
    "succeeded",
    "safely_rejected",
    "provider_failure",
    "policy_blocked",
    "invalid_proposal",
    "test_failure",
    "reviewer_rejected",
)

_EVAL_RESULT_STATUSES = {"succeeded", "failed", "skipped", "blocked"}


@dataclass(frozen=True)
class ProviderScorecard:
    """Per-provider metrics. Deliberately has no single aggregate score —
    every metric required by the evaluation criteria is its own field so
    the distinct outcome categories are never collapsed into one number.
    """

    provider: str
    schema: str = SCHEMA_SCORECARD
    health_check_success: bool = False
    tasks_attempted: int = 0
    tasks_completed: int = 0
    tasks_safely_rejected: int = 0
    tasks_failed_provider: int = 0
    tasks_blocked_by_policy: int = 0
    structured_output_valid_count: int = 0
    structured_output_invalid_count: int = 0
    path_policy_violations: int = 0
    worktree_escapes_detected: int = 0
    test_pass_count: int = 0
    test_fail_count: int = 0
    repair_loop_attempts: int = 0
    repair_loop_successes: int = 0
    reviewer_agreement_count: int = 0
    reviewer_disagreement_count: int = 0
    timeout_count: int = 0
    output_size_violations: int = 0
    credential_leak_detected: bool = False
    prompt_injection_resisted: bool | None = None
    total_duration_ms: float = 0.0
    total_output_bytes: int = 0
    cost_telemetry: Mapping[str, Any] = field(default_factory=dict)
    failure_classification: Mapping[str, int] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("provider is required")
        for name in (
            "tasks_attempted",
            "tasks_completed",
            "tasks_safely_rejected",
            "tasks_failed_provider",
            "tasks_blocked_by_policy",
            "structured_output_valid_count",
            "structured_output_invalid_count",
            "path_policy_violations",
            "worktree_escapes_detected",
            "test_pass_count",
            "test_fail_count",
            "repair_loop_attempts",
            "repair_loop_successes",
            "reviewer_agreement_count",
            "reviewer_disagreement_count",
            "timeout_count",
            "output_size_violations",
            "total_output_bytes",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")
        if float(self.total_duration_ms) < 0:
            raise ValueError("total_duration_ms must be non-negative")
        for count in self.failure_classification.values():
            if int(count) < 0:
                raise ValueError("failure_classification counts must be non-negative")
        _finite(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["cost_telemetry"] = dict(self.cost_telemetry)
        payload["failure_classification"] = dict(self.failure_classification)
        return payload


@dataclass(frozen=True)
class FixtureCaseResult:
    fixture_id: str
    provider: str
    outcome: str
    before_git_head: str
    after_git_head: str
    changed_files: tuple[str, ...] = ()
    proposal_valid: bool | None = None
    test_result: Mapping[str, Any] = field(default_factory=dict)
    repair_attempts: int = 0
    repair_succeeded: bool | None = None
    reviewer_findings: tuple[Mapping[str, Any], ...] = ()
    reviewer_approved: bool | None = None
    prompt_injection_resisted: bool | None = None
    elapsed_ms: float = 0.0
    output_bytes: int = 0
    error_type: str = ""
    error_message: str = ""
    raw_output_redacted: str = ""
    schema: str = SCHEMA_FIXTURE_RESULT
    created_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        if not self.fixture_id or not self.fixture_id.strip():
            raise ValueError("fixture_id is required")
        if not self.provider or not self.provider.strip():
            raise ValueError("provider is required")
        if self.outcome not in FIXTURE_OUTCOMES:
            raise ValueError(f"unsupported outcome: {self.outcome!r}")
        if self.repair_attempts < 0:
            raise ValueError("repair_attempts must be non-negative")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be non-negative")
        if self.output_bytes < 0:
            raise ValueError("output_bytes must be non-negative")
        _finite(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {
            "changed_files": list(self.changed_files),
            "test_result": dict(self.test_result),
            "reviewer_findings": [dict(x) for x in self.reviewer_findings],
        }


@dataclass(frozen=True)
class BenchmarkProviderSpec:
    """Static evaluation-prep record for a provider that is being assessed
    on paper (installation, license, isolation model) rather than
    live-integrated. Producing one of these never creates a runtime
    adapter and never touches routing.
    """

    provider: str
    schema: str = SCHEMA_PROVIDER_SPEC
    installation_method: str = ""
    license: str = ""
    execution_interface: str = ""
    isolation_requirements: str = ""
    network_requirements: str = ""
    output_format: str = ""
    edits_files_directly: bool | None = None
    auto_commit_can_be_disabled: bool | None = None
    known_policy_risks: tuple[str, ...] = ()
    expected_integration_effort: str = ""
    evaluation_status: str = "blocked"
    blocked_reason: str = ""
    source_url: str = ""
    created_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("provider is required")
        if self.evaluation_status not in {"blocked", "benchmarked"}:
            raise ValueError("evaluation_status must be 'blocked' or 'benchmarked'")
        if self.evaluation_status == "blocked" and not self.blocked_reason.strip():
            raise ValueError("blocked_reason is required when evaluation_status is 'blocked'")
        _finite(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {"known_policy_risks": list(self.known_policy_risks)}


@dataclass(frozen=True)
class EvalRequest:
    """Request envelope for reviewer-bridge and context-provider adapters.
    Kept separate from ``contracts.models.SensorRequest`` since that shape
    (query/allowed_domains/max_records) is for read-only web/data sensing,
    not diff review or repository-context building.
    """

    request_id: str
    kind: str
    objective: str
    diff: str = ""
    repository_map_hint: str = ""
    timeout_s: float = 60.0
    max_output_bytes: int = 512_000
    dry_run: bool = True
    schema: str = SCHEMA_EVAL_REQUEST

    @classmethod
    def create(cls, *, kind: str, objective: str, **kwargs: Any) -> "EvalRequest":
        request = cls(request_id=str(uuid.uuid4()), kind=kind, objective=objective, **kwargs)
        request.validate()
        return request

    def validate(self) -> None:
        if self.kind not in {"reviewer", "context_provider"}:
            raise ValueError("kind must be 'reviewer' or 'context_provider'")
        if not self.objective or not self.objective.strip():
            raise ValueError("objective is required")
        if not 0.001 <= float(self.timeout_s) <= 900:
            raise ValueError("timeout_s is out of bounds")
        if not 1_024 <= int(self.max_output_bytes) <= 10_000_000:
            raise ValueError("max_output_bytes is out of bounds")
        _finite(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class EvalResult:
    request_id: str
    provider: str
    status: str
    raw_output: str = ""
    normalized: Mapping[str, Any] = field(default_factory=dict)
    telemetry: Mapping[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    schema: str = SCHEMA_EVAL_RESULT

    def validate(self) -> None:
        if self.status not in _EVAL_RESULT_STATUSES:
            raise ValueError("invalid eval result status")
        _finite(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {"normalized": dict(self.normalized), "telemetry": dict(self.telemetry)}
