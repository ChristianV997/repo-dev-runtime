"""Versioned, finite-JSON contracts shared by all runtime adapters."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numeric value")
    if isinstance(value, Mapping):
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


@dataclass(frozen=True)
class RuntimeHealth:
    name: str
    configured: bool
    reachable: bool
    capabilities: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class DevTask:
    task_id: str
    repository: str
    base_ref: str
    role: str
    prompt: str
    acceptance: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    timeout_s: float = 120.0
    max_output_bytes: int = 512_000
    model: str = "default"
    approval_state: str = "not_required"
    dry_run: bool = True
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def create(cls, *, repository: str, base_ref: str, role: str, prompt: str, **kwargs: Any) -> "DevTask":
        task = cls(task_id=str(uuid.uuid4()), repository=repository, base_ref=base_ref, role=role, prompt=prompt, **kwargs)
        task.validate()
        return task

    def validate(self) -> None:
        for name in ("task_id", "repository", "base_ref", "role", "prompt"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if self.role not in {"planner", "implementer", "tester", "reviewer", "integrator"}:
            raise ValueError("unsupported development role")
        if not 0.001 <= float(self.timeout_s) <= 3_600:
            raise ValueError("timeout_s must be between 0.001 and 3600")
        if not 1_024 <= int(self.max_output_bytes) <= 10_000_000:
            raise ValueError("max_output_bytes is out of bounds")
        if self.approval_state not in {"not_required", "pending", "approved", "denied"}:
            raise ValueError("invalid approval_state")
        _finite(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {"acceptance": list(self.acceptance), "allowed_paths": list(self.allowed_paths)}

    @property
    def task_hash(self) -> str:
        # Scheduling metadata must not change the identity of the requested work.
        payload = self.to_dict()
        payload.pop("created_at", None)
        return sha256_json(payload)


@dataclass(frozen=True)
class DevResult:
    task_id: str
    runtime: str
    status: str
    output: str = ""
    changed_files: tuple[str, ...] = ()
    commit_sha: str = ""
    tests: tuple[Mapping[str, Any], ...] = ()
    telemetry: Mapping[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    created_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        if self.status not in {"succeeded", "failed", "skipped", "blocked"}:
            raise ValueError("invalid result status")
        _finite(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {"changed_files": list(self.changed_files), "tests": [dict(x) for x in self.tests]}


@dataclass(frozen=True)
class SensorRequest:
    request_id: str
    query: str
    objective: str
    allowed_domains: tuple[str, ...] = ()
    max_records: int = 50
    timeout_s: float = 60.0
    dry_run: bool = True

    @classmethod
    def create(cls, *, query: str, objective: str, **kwargs: Any) -> "SensorRequest":
        request = cls(request_id=str(uuid.uuid4()), query=query, objective=objective, **kwargs)
        request.validate()
        return request

    def validate(self) -> None:
        if not self.query.strip() or not self.objective.strip():
            raise ValueError("query and objective are required")
        if not 1 <= int(self.max_records) <= 1_000:
            raise ValueError("max_records is out of bounds")
        if not 0.001 <= float(self.timeout_s) <= 900:
            raise ValueError("timeout_s is out of bounds")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {"allowed_domains": list(self.allowed_domains)}


@dataclass(frozen=True)
class SensorResult:
    request_id: str
    sensor: str
    status: str
    records: tuple[Mapping[str, Any], ...] = ()
    telemetry: Mapping[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self) | {"records": [dict(x) for x in self.records]}
        _finite(payload)
        return payload
