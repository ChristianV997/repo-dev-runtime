"""Isolated Agent-Reach bridge adapter."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import uuid
from typing import Mapping, Sequence

from ..contracts.models import RuntimeHealth, SensorRequest, SensorResult


class AgentReachSensor:
    name = "agent_reach"

    def __init__(self, *, command: Sequence[str] | None = None, executable: str = "agent-reach", enabled: bool | None = None, max_output_bytes: int = 512_000) -> None:
        value = os.getenv("AGENT_REACH_COMMAND", "").strip()
        parsed: tuple[str, ...] = ()
        if value:
            try:
                loaded = json.loads(value)
                parsed = tuple(loaded) if isinstance(loaded, list) else tuple(shlex.split(value))
            except json.JSONDecodeError:
                parsed = tuple(shlex.split(value, posix=os.name != "nt"))
        self.command = tuple(command or parsed)
        self.executable = executable
        self.enabled = enabled if enabled is not None else os.getenv("DEV_RUNTIME_AGENT_REACH", "false").lower() in {"1", "true", "yes", "on"}
        self.max_output_bytes = max(1_024, int(max_output_bytes))

    def health(self) -> RuntimeHealth:
        executable = self.command[0] if self.command else self.executable
        return RuntimeHealth(self.name, self.enabled and bool(self.command), shutil.which(executable) is not None, ("isolated_subprocess", "bounded_output"), "command is not configured" if not self.command else "")

    def collect(self, request: SensorRequest) -> SensorResult:
        if not self.enabled:
            return SensorResult(request.request_id, self.name, "skipped", error_type="sensor_disabled")
        if not self.command:
            return SensorResult(request.request_id, self.name, "blocked", error_type="command_not_configured")
        payload = {"schema": "RepoDev.AgentReachRequest.v1", "request_id": request.request_id, **request.to_dict()}
        environment = {key: value for key, value in os.environ.items() if key.startswith(("AGENT_REACH_", "PATH", "HOME", "USERPROFILE", "TEMP", "TMP"))}
        try:
            result = subprocess.run(list(self.command), input=json.dumps(payload, sort_keys=True), text=True, capture_output=True, timeout=request.timeout_s, check=False, shell=False, env=environment)
            if result.returncode != 0:
                return SensorResult(request.request_id, self.name, "failed", error_type="bridge_exit", error_message=f"exit code {result.returncode}")
            raw = result.stdout.encode("utf-8", errors="replace")
            if len(raw) > self.max_output_bytes:
                return SensorResult(request.request_id, self.name, "failed", error_type="output_limit")
            body = json.loads(result.stdout)
            records = body if isinstance(body, list) else body.get("records", [])
            if not isinstance(records, list):
                raise ValueError("records must be a list")
            return SensorResult(request.request_id, self.name, "succeeded", tuple(x for x in records if isinstance(x, Mapping)))
        except subprocess.TimeoutExpired:
            return SensorResult(request.request_id, self.name, "failed", error_type="timeout")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return SensorResult(request.request_id, self.name, "failed", error_type=type(exc).__name__, error_message=str(exc)[:500])
