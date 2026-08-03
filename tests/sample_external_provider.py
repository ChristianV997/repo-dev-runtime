"""A stand-in externally-defined provider, used to prove the
``--provider-module`` load-and-run path works for a provider defined
outside repo_dev_runtime.eval. Deliberately lives in tests/ so it is
never importable as part of the shipped package or reachable by routing.
"""
from __future__ import annotations

import json
import subprocess
import uuid

from repo_dev_runtime.contracts.models import DevResult, DevTask, RuntimeHealth


def _git_head(root: str) -> str:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip()


class SampleExternalProvider:
    """Zero-arg constructible; always proposes a no-op whole-file rewrite
    of a file it knows exists in the simplest fixture."""

    name = "sample_external_provider"

    def health(self) -> RuntimeHealth:
        return RuntimeHealth(self.name, True, True, ("sample",), "")

    def execute(self, task: DevTask) -> DevResult:
        proposal = {
            "schema": "RepoDev.EditProposal.v1",
            "proposal_id": str(uuid.uuid4()),
            "task_id": task.task_id,
            "base_commit": _git_head(task.repository),
            "context_hash": "eval",
            "summary": "sample external provider edit",
            "edits": [{"path": "calc.py", "format": "whole_file", "content": "def add(a, b):\n    return a + b\n"}],
        }
        return DevResult(task_id=task.task_id, runtime=self.name, status="succeeded", output=json.dumps(proposal))


class ProviderWithCreateClassmethod(SampleExternalProvider):
    """Exercises the ``create()`` construction convention."""

    name = "provider_with_create"

    def __init__(self, marker: str) -> None:
        self.marker = marker

    @classmethod
    def create(cls) -> "ProviderWithCreateClassmethod":
        return cls(marker="constructed-via-create")


class NotARuntime:
    """Missing health()/execute() — must be rejected by the loader."""

    name = "not_a_runtime"


class RequiresArguments:
    """Not zero-arg constructible and has no create() — must be rejected."""

    name = "requires_arguments"

    def __init__(self, required: str) -> None:
        self.required = required

    def health(self) -> RuntimeHealth:  # pragma: no cover - never constructed
        return RuntimeHealth(self.name, True, True, (), "")

    def execute(self, task: DevTask) -> DevResult:  # pragma: no cover - never constructed
        return DevResult(task_id=task.task_id, runtime=self.name, status="skipped")
