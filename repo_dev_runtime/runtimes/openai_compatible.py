"""Bounded OpenAI-compatible runtime used by OmniRoute and similar gateways."""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..contracts.models import DevResult, DevTask, RuntimeHealth
from ..governance.credentials import redact_text


class OpenAICompatibleRuntime:
    name = "openai_compatible"

    def __init__(self, *, base_url: str | None = None, token: str | None = None, model: str | None = None, enabled: bool | None = None) -> None:
        self.base_url = (base_url or os.getenv("DEV_OMNIROUTE_URL", "http://127.0.0.1:20128")).rstrip("/")
        self.token = token if token is not None else os.getenv("DEV_OMNIROUTE_TOKEN", "")
        self.model = model or os.getenv("DEV_OMNIROUTE_MODEL", "default")
        self.enabled = enabled if enabled is not None else os.getenv("DEV_OMNIROUTE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

    def health(self) -> RuntimeHealth:
        if not self.enabled:
            return RuntimeHealth(self.name, False, False, ("openai_chat_completions",), "runtime disabled")
        try:
            with urlopen(f"{self.base_url}/v1/models", timeout=0.75) as response:
                return RuntimeHealth(self.name, True, response.status == 200, ("openai_chat_completions",))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return RuntimeHealth(self.name, True, False, ("openai_chat_completions",), type(exc).__name__)

    def execute(self, task: DevTask) -> DevResult:
        if not self.enabled:
            return DevResult(task.task_id, self.name, "skipped", error_type="runtime_disabled")
        payload = {"model": task.model if task.model != "default" else self.model, "messages": [{"role": "user", "content": task.prompt}], "stream": False}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        started = time.perf_counter()
        try:
            request = Request(f"{self.base_url}/v1/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
            with urlopen(request, timeout=task.timeout_s) as response:
                raw = response.read(task.max_output_bytes + 1)
            if len(raw) > task.max_output_bytes:
                raise ValueError("runtime output exceeded limit")
            body = json.loads(raw.decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            return DevResult(task.task_id, self.name, "succeeded", output=str(content), telemetry={"duration_ms": round((time.perf_counter() - started) * 1000, 2), "model": payload["model"]})
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return DevResult(task.task_id, self.name, "failed", error_type=type(exc).__name__, error_message=redact_text(str(exc)[:500]), telemetry={"duration_ms": round((time.perf_counter() - started) * 1000, 2)})
