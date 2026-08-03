"""Reusable contract assertions for provider implementations.

This is an importable library, not a test module: when a real provider
adapter is built (a coding runtime, a reviewer bridge, a repository-context
provider), its own test file should call the relevant ``assert_*_contract``
here instead of re-deriving the same worktree-containment, fail-closed,
and credential-redaction checks. Keeping one implementation of these
checks means a governance rule tightened here tightens everywhere at once.

Every function raises ``AssertionError`` with a specific message on
violation and returns ``None`` on success, so they compose inside any
test framework without depending on one.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from ..contracts.models import DevResult, DevTask, RuntimeHealth
from ..governance.credentials import redact_json, redact_text
from .models import EvalRequest, EvalResult

# Capability names a bounded provider must never expose. A reviewer or
# context provider that grows one of these has escaped its boundary.
FORBIDDEN_CAPABILITY_NAMES = frozenset(
    {
        "apply",
        "apply_edits",
        "apply_patch",
        "commit",
        "merge",
        "push",
        "publish_branch",
        "create_pr",
        "create_pull_request",
        "create_from_worktree",
    }
)


def assert_no_forbidden_capabilities(subject: Any, *, label: str = "provider") -> None:
    """The subject must expose no edit/publish/merge capability."""
    present = FORBIDDEN_CAPABILITY_NAMES & set(dir(subject))
    assert not present, f"{label} exposes forbidden capabilities: {sorted(present)}"


def assert_no_credential_leak(payload: Any, *, secret: str, label: str = "output") -> None:
    """A known secret sentinel must not survive into provider-visible output.

    Accepts a string or any JSON-like structure; applies the same
    redaction the report layer applies, then asserts the sentinel is gone.
    """
    assert secret, "a non-empty secret sentinel is required"
    redacted = redact_text(payload) if isinstance(payload, str) else redact_json(payload)
    rendered = redacted if isinstance(redacted, str) else repr(redacted)
    assert secret not in rendered, f"{label} leaked the secret sentinel after redaction"


def assert_runtime_health_shape(health: Any, *, label: str = "provider") -> None:
    assert isinstance(health, RuntimeHealth), f"{label}.health() must return a RuntimeHealth"
    assert isinstance(health.name, str) and health.name, f"{label}.health().name must be a non-empty string"
    assert isinstance(health.configured, bool), f"{label}.health().configured must be a bool"
    assert isinstance(health.reachable, bool), f"{label}.health().reachable must be a bool"


def assert_development_runtime_contract(
    make_provider: Callable[[], Any],
    *,
    repository: str | Path,
    label: str = "provider",
) -> None:
    """Verify a coding provider satisfies the DevelopmentRuntime contract.

    ``repository`` should be a disposable checkout (the caller's fixture or
    worktree) — this runs one real ``execute()`` against it.
    """
    provider = make_provider()

    assert isinstance(getattr(provider, "name", None), str) and provider.name, f"{label} must expose a non-empty .name"
    assert callable(getattr(provider, "health", None)), f"{label} must expose health()"
    assert callable(getattr(provider, "execute", None)), f"{label} must expose execute()"
    assert_runtime_health_shape(provider.health(), label=label)

    task = DevTask(
        task_id=uuid.uuid4().hex,
        repository=str(repository),
        base_ref="HEAD",
        role="implementer",
        prompt="Conformance probe: describe a minimal change.",
        dry_run=False,
    )
    task.validate()

    try:
        result = provider.execute(task)
    except Exception as exc:  # noqa: BLE001 - the contract is that this cannot happen
        raise AssertionError(f"{label}.execute() raised {type(exc).__name__}; it must return a failed DevResult instead") from exc

    assert isinstance(result, DevResult), f"{label}.execute() must return a DevResult"
    result.validate()
    assert result.task_id == task.task_id, f"{label}.execute() must echo the task_id it was given"


def assert_reviewer_contract(
    review: Callable[[EvalRequest], EvalResult],
    *,
    label: str = "reviewer",
    malformed_probe: bool = True,
) -> None:
    """Verify a reviewer bridge is bounded, normalizing, and fails closed."""
    request = EvalRequest.create(kind="reviewer", objective="conformance probe", diff="--- a\n+++ b\n")

    try:
        result = review(request)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"{label} raised {type(exc).__name__}; it must return a failed/blocked EvalResult instead") from exc

    assert isinstance(result, EvalResult), f"{label} must return an EvalResult"
    result.validate()
    assert result.request_id == request.request_id, f"{label} must echo the request_id it was given"

    if result.status == "succeeded":
        assert isinstance(result.normalized.get("approved"), bool), (
            f"{label} returned status=succeeded without a normalized boolean 'approved' — "
            "a verdict must normalize into the RepoDev.ReviewVerdict.v1 shape"
        )
    else:
        assert not result.normalized, f"{label} must not emit a normalized verdict for a non-succeeded result (fail closed)"

    if malformed_probe:
        owner = getattr(review, "__self__", None)
        if owner is not None:
            assert_no_forbidden_capabilities(owner, label=label)


def assert_context_provider_contract(
    provider: Any,
    *,
    root: str | Path,
    objective: str = "conformance probe",
    max_bytes: int = 8_192,
    label: str = "context provider",
) -> None:
    """Verify a context provider matches the normalized context shape and
    declares honest capability metadata."""
    assert isinstance(getattr(provider, "name", None), str) and provider.name, f"{label} must expose a non-empty .name"
    assert callable(getattr(provider, "capabilities", None)), f"{label} must expose capabilities()"
    assert callable(getattr(provider, "build", None)), f"{label} must expose build()"

    capabilities = provider.capabilities()
    assert isinstance(capabilities, Mapping), f"{label}.capabilities() must return a mapping"
    assert "vendored" in capabilities, f"{label}.capabilities() must declare a 'vendored' flag"

    built = provider.build(root, objective=objective, allowed_paths=(), forbidden_paths=(), max_bytes=max_bytes)
    assert isinstance(built, tuple) and len(built) == 2, (
        f"{label}.build() must return a (full_context_text, map_text) 2-tuple, matching context.build_adaptive_context"
    )
    text, map_text = built
    assert isinstance(text, str) and isinstance(map_text, str), f"{label}.build() must return two strings"
    assert len(text.encode("utf-8")) <= max_bytes, f"{label}.build() exceeded the max_bytes budget it was given"


def assert_forbidden_path_respected(
    provider: Any,
    *,
    root: str | Path,
    forbidden_segment: str,
    label: str = "context provider",
) -> None:
    """A context provider must not surface content from a forbidden path."""
    text, map_text = provider.build(
        root, objective="conformance probe", allowed_paths=(), forbidden_paths=(forbidden_segment,), max_bytes=8_192
    )
    assert forbidden_segment not in text, f"{label} surfaced forbidden path {forbidden_segment!r} in its context"
    assert forbidden_segment not in map_text, f"{label} surfaced forbidden path {forbidden_segment!r} in its map"
