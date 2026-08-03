"""Hermes and DeerFlow adapters used by the non-persisting benchmark."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..contracts.models import DevResult, DevTask, RuntimeHealth


def _prompt(task: DevTask, runtime: str) -> str:
    return (
        "Return a concise development result. Do not edit files, execute commands, "
        "or invent test output.\n"
        f"runtime={runtime}\nrole={task.role}\nrepository={task.repository}\n"
        f"acceptance={list(task.acceptance)}\n\n{task.prompt}"
    )


def _post_json(url: str, body: Mapping[str, Any], headers: Mapping[str, str], timeout: float) -> dict[str, Any]:
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read(2_000_001).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("sidecar response must be an object")
    return value


class HermesRuntime:
    name = "hermes"

    def __init__(self, *, base_url: str | None = None, token: str | None = None, model: str | None = None, request: Callable[..., dict[str, Any]] = _post_json, enabled: bool | None = None) -> None:
        self.base_url = (base_url or os.getenv("HERMES_URL", "http://127.0.0.1:8642")).rstrip("/")
        self.token = token if token is not None else os.getenv("HERMES_API_TOKEN", "")
        self.model = model or os.getenv("HERMES_MODEL", "hermes-agent")
        self.request = request
        self.enabled = enabled if enabled is not None else os.getenv("DEV_RUNTIME_HERMES", "false").lower() in {"1", "true", "yes", "on"}

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
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        started = time.perf_counter()
        try:
            body = self.request(f"{self.base_url}/v1/chat/completions", {"model": task.model if task.model != "default" else self.model, "messages": [{"role": "user", "content": _prompt(task, self.name)}], "stream": False}, headers, task.timeout_s)
            content = body["choices"][0]["message"]["content"]
            return DevResult(task.task_id, self.name, "succeeded", output=str(content), telemetry={"duration_ms": round((time.perf_counter() - started) * 1000, 2)})
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return DevResult(task.task_id, self.name, "failed", error_type=type(exc).__name__, error_message=str(exc)[:500], telemetry={"duration_ms": round((time.perf_counter() - started) * 1000, 2)})


class DeerFlowRuntime:
    name = "deerflow"

    def __init__(self, *, base_url: str | None = None, token: str | None = None, owner_id: str | None = None, enabled: bool | None = None) -> None:
        self.base_url = (base_url or os.getenv("DEERFLOW_URL", "http://127.0.0.1:2026")).rstrip("/")
        self.token = token if token is not None else os.getenv("DEER_FLOW_INTERNAL_AUTH_TOKEN", "")
        self.owner_id = owner_id or os.getenv("DEER_FLOW_OWNER_USER_ID", "repo-dev-runtime")
        self.enabled = enabled if enabled is not None else os.getenv("DEV_RUNTIME_DEERFLOW", "false").lower() in {"1", "true", "yes", "on"}

    def health(self) -> RuntimeHealth:
        if not self.enabled:
            return RuntimeHealth(self.name, False, False, ("langgraph_sse",), "runtime disabled")
        try:
            with urlopen(f"{self.base_url}/health", timeout=0.75) as response:
                return RuntimeHealth(self.name, True, response.status == 200, ("langgraph_sse",))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return RuntimeHealth(self.name, True, False, ("langgraph_sse",), type(exc).__name__)

    def execute(self, task: DevTask) -> DevResult:
        if not self.enabled:
            return DevResult(task.task_id, self.name, "skipped", error_type="runtime_disabled")
        headers = {"Accept": "text/event-stream"}
        if self.token:
            headers.update({"X-DeerFlow-Internal-Token": self.token, "X-DeerFlow-Owner-User-Id": self.owner_id})
        payload = {"input": {"messages": [{"role": "user", "content": _prompt(task, self.name)}]}, "stream_mode": ["values", "messages-tuple", "custom"]}
        started = time.perf_counter()
        try:
            request = Request(f"{self.base_url}/api/langgraph/runs/stream", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
            with urlopen(request, timeout=task.timeout_s) as response:
                raw = response.read(task.max_output_bytes + 1)
            if len(raw) > task.max_output_bytes:
                raise ValueError("sidecar output exceeded limit")
            content = ""
            for line in raw.decode("utf-8", errors="replace").splitlines():
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if not value or value == "[DONE]":
                    continue
                try:
                    event = json.loads(value)
                except json.JSONDecodeError:
                    continue
                messages = event.get("messages") if isinstance(event, dict) else None
                if isinstance(messages, list):
                    for message in reversed(messages):
                        if isinstance(message, dict) and message.get("content"):
                            content = str(message["content"])
                            break
                elif isinstance(event, dict) and event.get("content"):
                    content = str(event["content"])
            if not content:
                raise ValueError("DeerFlow stream contained no assistant content")
            return DevResult(task.task_id, self.name, "succeeded", output=content, telemetry={"duration_ms": round((time.perf_counter() - started) * 1000, 2)})
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return DevResult(task.task_id, self.name, "failed", error_type=type(exc).__name__, error_message=str(exc)[:500], telemetry={"duration_ms": round((time.perf_counter() - started) * 1000, 2)})
