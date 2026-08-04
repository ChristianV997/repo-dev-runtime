"""Local Ollama runtime with no SDK dependency."""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..contracts.models import DevResult, DevTask, RuntimeHealth
from ..governance.credentials import redact_text


class OllamaRuntime:
    name = "ollama"

    def __init__(self, *, base_url: str | None = None, model: str | None = None, enabled: bool | None = None) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "mistral:7b")
        self.enabled = enabled if enabled is not None else os.getenv("DEV_RUNTIME_OLLAMA", "false").lower() in {"1", "true", "yes", "on"}

    def health(self) -> RuntimeHealth:
        if not self.enabled:
            return RuntimeHealth(self.name, False, False, ("local_inference",), "runtime disabled")
        try:
            with urlopen(f"{self.base_url}/api/tags", timeout=0.5) as response:
                return RuntimeHealth(self.name, True, response.status == 200, ("local_inference",))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return RuntimeHealth(self.name, True, False, ("local_inference",), type(exc).__name__)

    def execute(self, task: DevTask) -> DevResult:
        if not self.enabled:
            return DevResult(task.task_id, self.name, "skipped", error_type="runtime_disabled")
        payload = {"model": task.model if task.model != "default" else self.model, "messages": [{"role": "user", "content": task.prompt}], "stream": False}
        # Ollama's JSON mode reduces malformed contract responses without granting model authority.
        if task.role in {"implementer", "reviewer"}:
            payload["format"] = "json"
        started = time.perf_counter()
        try:
            request = Request(f"{self.base_url}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=task.timeout_s) as response:
                raw = response.read(task.max_output_bytes + 1)
            if len(raw) > task.max_output_bytes:
                raise ValueError("runtime output exceeded limit")
            body = json.loads(raw.decode("utf-8"))
            content = body["message"]["content"]
            return DevResult(task.task_id, self.name, "succeeded", output=str(content), telemetry={"duration_ms": round((time.perf_counter() - started) * 1000, 2), "model": payload["model"]})
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return DevResult(task.task_id, self.name, "failed", error_type=type(exc).__name__, error_message=redact_text(str(exc)[:500]), telemetry={"duration_ms": round((time.perf_counter() - started) * 1000, 2)})
