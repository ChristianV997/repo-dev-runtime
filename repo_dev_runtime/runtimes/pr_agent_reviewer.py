"""Workflow-facing, reviewer-only wrapper for the bounded PR-Agent bridge."""
from __future__ import annotations

import json

from ..contracts.models import DevResult, DevTask, RuntimeHealth
from ..eval.models import EvalRequest
from ..eval.pr_agent import PRAgentReviewAdapter


_DIFF_MARKER = "\n\nFinal diff:\n"


class PRAgentReviewerRuntime:
    """Adapt the PR-Agent bridge to the workflow runtime contract.

    It receives only the final-review prompt, containing a bounded diff and
    test evidence. It has no filesystem, patch, Git, or publishing authority
    and rejects every role other than ``reviewer``.
    """

    name = "pr_agent"

    def __init__(self, adapter: PRAgentReviewAdapter) -> None:
        self.adapter = adapter

    def health(self) -> RuntimeHealth:
        return self.adapter.health()

    def execute(self, task: DevTask) -> DevResult:
        if task.role != "reviewer":
            return DevResult(task.task_id, self.name, "blocked", error_type="reviewer_only", error_message="PR-Agent may only execute the reviewer role")
        if _DIFF_MARKER not in task.prompt:
            return DevResult(task.task_id, self.name, "blocked", error_type="final_diff_missing", error_message="PR-Agent requires a final diff and quality evidence")
        objective, diff = task.prompt.split(_DIFF_MARKER, 1)
        try:
            request = EvalRequest.create(kind="reviewer", objective=objective, diff=diff, timeout_s=60.0, dry_run=task.dry_run)
            evaluated = self.adapter.review(request)
            evaluated.validate()
        except Exception as exc:
            return DevResult(task.task_id, self.name, "failed", error_type=type(exc).__name__, error_message=str(exc)[:500])
        if evaluated.status != "succeeded":
            return DevResult(
                task.task_id,
                self.name,
                "blocked" if evaluated.status == "blocked" else "failed",
                output=evaluated.raw_output,
                telemetry=evaluated.telemetry,
                error_type=evaluated.error_type or "review_failed",
                error_message=evaluated.error_message,
            )
        return DevResult(task.task_id, self.name, "succeeded", output=json.dumps(dict(evaluated.normalized), sort_keys=True), telemetry=evaluated.telemetry)
