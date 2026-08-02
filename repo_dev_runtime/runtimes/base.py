from __future__ import annotations

from typing import Protocol

from ..contracts.models import DevResult, DevTask, RuntimeHealth


class DevelopmentRuntime(Protocol):
    name: str

    def health(self) -> RuntimeHealth: ...

    def execute(self, task: DevTask) -> DevResult: ...
