"""Non-persisting runtime benchmark."""
from __future__ import annotations

import time
from typing import Mapping

from .contracts.models import DevResult, DevTask


def benchmark_runtimes(task: DevTask, runtimes: Mapping[str, object]) -> dict[str, object]:
    results: dict[str, object] = {"task_id": task.task_id, "persisted": False, "results": {}}
    for name, runtime in runtimes.items():
        started = time.perf_counter()
        try:
            result = runtime.execute(task)  # type: ignore[attr-defined]
            if not isinstance(result, DevResult):
                raise TypeError("runtime returned an invalid result")
            results["results"][name] = result.to_dict() | {"duration_ms": round((time.perf_counter() - started) * 1000, 2)}  # type: ignore[index]
        except Exception as exc:
            results["results"][name] = {"status": "failed", "error_type": type(exc).__name__}  # type: ignore[index]
    return results
