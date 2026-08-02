"""Fail-closed OpenClaw sidecar boundary.

The v1 adapter intentionally performs no host execution. A future gateway
client may implement the documented WebSocket protocol behind this boundary.
"""
from __future__ import annotations

import os

from ..contracts.models import DevResult, DevTask, RuntimeHealth


class OpenClawRuntime:
    name = "openclaw"

    def __init__(self, *, gateway_url: str | None = None, token: str | None = None, enabled: bool | None = None) -> None:
        self.gateway_url = gateway_url or os.getenv("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789")
        self.token = token if token is not None else os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
        self.enabled = enabled if enabled is not None else os.getenv("DEV_RUNTIME_OPENCLAW", "false").lower() in {"1", "true", "yes", "on"}

    def health(self) -> RuntimeHealth:
        configured = bool(self.token and self.gateway_url.startswith("ws://127.0.0.1"))
        return RuntimeHealth(self.name, self.enabled and configured, False, ("scoped_gateway", "isolated_sessions"), "WebSocket client not enabled in v1")

    def execute(self, task: DevTask) -> DevResult:
        if not self.enabled:
            return DevResult(task.task_id, self.name, "skipped", error_type="runtime_disabled")
        return DevResult(task.task_id, self.name, "blocked", error_type="gateway_client_unimplemented", error_message="OpenClaw execution remains disabled until scoped gateway client validation is complete")
