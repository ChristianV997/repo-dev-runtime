"""Bounded, disabled-by-default reviewer bridge to an external PR-Agent-
style CLI. PR-Agent itself is not vendored, and its exact current CLI/API
contract is not assumed — this module defines the *interface* (a subprocess
bridge with a JSON stdin/stdout contract) and normalizes any response into
the existing, authoritative RepoDev.ReviewVerdict.v1 contract. It cannot
apply edits, approve its own implementation, merge, push, or create a pull
request: the class exposes no such methods and is never given a
PatchApplier or GitHubPublisher reference.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from typing import Sequence

from ..contracts.models import RuntimeHealth
from ..governance.credentials import CredentialAllowlist, build_subprocess_env, missing_credential_result, redact_text
from ..review import ReviewValidationError, parse_review_verdict
from .models import EvalRequest, EvalResult


def _parse_command_env() -> tuple[str, ...]:
    """Parse ``PR_AGENT_COMMAND`` as either a JSON array or a shell-split
    string. Mirrors the parsing already used by ``sensors/agent_reach.py``
    so external-command configuration is consistent across bridges."""
    value = os.getenv("PR_AGENT_COMMAND", "").strip()
    if not value:
        return ()
    try:
        loaded = json.loads(value)
        return tuple(loaded) if isinstance(loaded, list) else tuple(shlex.split(value))
    except json.JSONDecodeError:
        return tuple(shlex.split(value, posix=os.name != "nt"))


class PRAgentReviewAdapter:
    """Independent reviewer boundary: receives a diff and objective, runs
    the configured external command in isolation, and returns a normalized
    RepoDev.ReviewVerdict.v1-shaped result. Disabled by default
    (``DEV_RUNTIME_PR_AGENT``); a missing credential or malformed provider
    output always produces a blocked/failed EvalResult, never an exception.
    """

    name = "pr_agent"

    def __init__(self, *, command: Sequence[str] | None = None, enabled: bool | None = None, max_output_bytes: int = 512_000, required_credential: str | None = None) -> None:
        self.command = tuple(command) if command else _parse_command_env()
        self.enabled = enabled if enabled is not None else os.getenv("DEV_RUNTIME_PR_AGENT", "false").lower() in {"1", "true", "yes", "on"}
        self.max_output_bytes = max(1_024, int(max_output_bytes))
        self.required_credential = required_credential if required_credential is not None else os.getenv("PR_AGENT_REQUIRED_CREDENTIAL", "").strip()

    def health(self) -> RuntimeHealth:
        if not self.enabled or not self.command:
            return RuntimeHealth(self.name, False, False, (), "pr_agent is disabled or not configured")
        executable = self.command[0]
        return RuntimeHealth(self.name, True, shutil.which(executable) is not None, ("subprocess_bridge", "bounded_output", "reviewer_only"), "")

    def review(self, request: EvalRequest) -> EvalResult:
        if not self.enabled:
            return EvalResult(request_id=request.request_id, provider=self.name, status="skipped", error_type="provider_disabled")
        if not self.command:
            return EvalResult(request_id=request.request_id, provider=self.name, status="blocked", error_type="command_not_configured")
        if self.required_credential and not os.getenv(self.required_credential):
            blocked = missing_credential_result(provider=self.name, name=self.required_credential)
            return EvalResult(request_id=request.request_id, provider=self.name, status=blocked["status"], error_type=blocked["error_type"], error_message=blocked["error_message"])

        payload = {"schema": "RepoDev.PRAgentReviewRequest.v1", "request_id": request.request_id, "objective": request.objective, "diff": request.diff}
        allowlist = CredentialAllowlist(provider=self.name, env_prefixes=("PR_AGENT_",))
        environment = build_subprocess_env(allowlist)
        try:
            completed = subprocess.run(
                list(self.command), input=json.dumps(payload, sort_keys=True), text=True,
                capture_output=True, timeout=request.timeout_s, check=False, shell=False, env=environment,
            )
        except subprocess.TimeoutExpired:
            return EvalResult(request_id=request.request_id, provider=self.name, status="failed", error_type="timeout")
        except OSError as exc:
            return EvalResult(request_id=request.request_id, provider=self.name, status="failed", error_type=type(exc).__name__, error_message=redact_text(str(exc)[:500]))

        raw_stdout = redact_text(completed.stdout[: self.max_output_bytes])
        if completed.returncode != 0:
            return EvalResult(request_id=request.request_id, provider=self.name, status="failed", raw_output=raw_stdout, error_type="bridge_exit", error_message=f"exit code {completed.returncode}")
        if len(completed.stdout.encode("utf-8")) > self.max_output_bytes:
            return EvalResult(request_id=request.request_id, provider=self.name, status="failed", error_type="output_limit")

        try:
            verdict = parse_review_verdict(completed.stdout)
        except ReviewValidationError as exc:
            # Fails closed: malformed provider output never becomes an
            # authoritative verdict.
            return EvalResult(request_id=request.request_id, provider=self.name, status="failed", raw_output=raw_stdout, error_type=type(exc).__name__, error_message=redact_text(str(exc)[:500]))

        return EvalResult(request_id=request.request_id, provider=self.name, status="succeeded", raw_output=raw_stdout, normalized=verdict.to_dict())
