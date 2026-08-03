"""Non-network runtime used for deterministic workflow previews."""
from __future__ import annotations

from ..contracts.models import DevResult, DevTask, RuntimeHealth


class DryRunRuntime:
    name = "dry_run"

    def health(self) -> RuntimeHealth:
        return RuntimeHealth(self.name, True, True, ("preview",))

    def execute(self, task: DevTask) -> DevResult:
        return DevResult(task.task_id, self.name, "skipped", output=f"dry-run preview for role {task.role}")

