"""Runtime registration, health filtering, and explicit routing."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Mapping

from ..contracts.models import DevResult, DevTask, RuntimeHealth
from ..governance.policy import RuntimePolicy


@dataclass(frozen=True)
class RoutingPolicy:
    """Role routing with a local-first, fail-closed default."""

    preferred_by_role: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: {
        "planner": ("ollama", "openai_compatible", "hermes", "deerflow"),
        "implementer": ("ollama", "openai_compatible", "hermes", "deerflow"),
        "tester": ("ollama", "openai_compatible"),
        "reviewer": ("ollama", "openai_compatible", "hermes", "deerflow"),
        "integrator": ("ollama", "openai_compatible"),
    })
    max_calls: int = 20

    def validate(self) -> None:
        if self.max_calls < 1 or self.max_calls > 100:
            raise ValueError("max_calls must be between 1 and 100")


class RuntimeRegistry:
    def __init__(self, runtimes: Mapping[str, object] | None = None) -> None:
        self._runtimes: dict[str, object] = dict(runtimes or {})

    def register(self, name: str, runtime: object) -> None:
        if not name.strip() or name in self._runtimes:
            raise ValueError("runtime name must be non-empty and unique")
        self._runtimes[name] = runtime

    def get(self, name: str) -> object:
        return self._runtimes[name]

    def health(self) -> dict[str, RuntimeHealth]:
        result: dict[str, RuntimeHealth] = {}
        for name, runtime in self._runtimes.items():
            health = runtime.health()  # type: ignore[attr-defined]
            result[name] = health
        return result

    def available(self, *, policy: RuntimePolicy) -> tuple[str, ...]:
        available: list[str] = []
        for name, health in self.health().items():
            if not health.configured or not health.reachable:
                continue
            if name == "ollama" and not policy.allow_ollama:
                continue
            if name == "openai_compatible" and not policy.allow_omniroute:
                continue
            if name == "hermes" and not policy.allow_omniroute:
                continue
            if name == "deerflow" and not policy.allow_omniroute:
                continue
            available.append(name)
        return tuple(available)


class RuntimeRouter:
    def __init__(
        self,
        registry: RuntimeRegistry,
        *,
        policy: RuntimePolicy,
        routing: RoutingPolicy | None = None,
        health_cache_ttl_s: float = 1.0,
    ) -> None:
        if not 0.0 <= float(health_cache_ttl_s) <= 60.0:
            raise ValueError("health_cache_ttl_s must be between 0 and 60 seconds")
        self.registry = registry
        self.policy = policy
        self.routing = routing or RoutingPolicy()
        self.routing.validate()
        self.health_cache_ttl_s = float(health_cache_ttl_s)
        self.calls = 0
        self._available_cache: tuple[str, ...] | None = None
        self._available_cache_at = 0.0

    def invalidate_health_cache(self) -> None:
        """Force the next route decision to refresh provider health."""
        self._available_cache = None
        self._available_cache_at = 0.0

    def _available(self) -> tuple[str, ...]:
        now = time.monotonic()
        if (
            self._available_cache is not None
            and self.health_cache_ttl_s > 0
            and now - self._available_cache_at < self.health_cache_ttl_s
        ):
            return self._available_cache
        available = self.registry.available(policy=self.policy)
        self._available_cache = available
        self._available_cache_at = now
        return available

    def route(self, task: DevTask, *, approved: bool = False) -> str | None:
        task.validate()
        if self.calls >= self.routing.max_calls:
            return None
        available = set(self._available())
        for candidate in self.routing.preferred_by_role.get(task.role, ("ollama",)):
            if candidate not in available:
                continue
            if candidate == "openai_compatible" or candidate in {"hermes", "deerflow"}:
                self.policy.authorize("paid_routing", approved=approved)
            return candidate
        return None

    def execute(self, task: DevTask, *, approved: bool = False) -> DevResult:
        name = self.route(task, approved=approved)
        if name is None:
            return DevResult(task.task_id, "router", "blocked", error_type="no_authorized_runtime")
        self.calls += 1
        result = self.registry.get(name).execute(task)  # type: ignore[attr-defined]
        telemetry = dict(result.telemetry) | {"routed_runtime": name, "router_calls": self.calls}
        return DevResult(task.task_id, result.runtime, result.status, result.output, result.changed_files, result.commit_sha, result.tests, telemetry, result.error_type, result.error_message, result.created_at)

