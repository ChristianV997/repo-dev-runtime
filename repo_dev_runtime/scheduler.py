"""Declarative, one-shot scheduling and resumable task state."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json


@dataclass(frozen=True)
class SchedulePlan:
    command: str
    interval_minutes: int = 60
    dry_run_command: str = ""
    daemon_implemented: bool = False

    def validate(self) -> None:
        if not self.command.strip() or not 1 <= self.interval_minutes <= 10080:
            raise ValueError("invalid schedule plan")
        if self.daemon_implemented:
            raise ValueError("background daemons are not supported by the v1 runtime")


class TaskStateStore:
    """Small atomic JSON state store used to resume tasks without a daemon."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("task state must be an object")
        return value

    def update(self, task_id: str, status: str, **details: Any) -> dict[str, Any]:
        if not task_id.strip() or status not in {"pending", "running", "succeeded", "failed", "blocked"}:
            raise ValueError("invalid task state")
        state = self.load()
        state[task_id] = {"status": status, **details}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return state[task_id]

