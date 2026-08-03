"""Bounded command execution for repository-local tools."""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..governance.command_policy import CommandPolicy, evaluate_command


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


def run_command(command: Sequence[str], *, cwd: str | Path, timeout_s: float = 120.0, max_output_bytes: int = 512_000, network_access: bool = False, allow_branch_publish: bool = False) -> CommandResult:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("command must be a non-empty sequence of strings")
    decision = evaluate_command(shlex.join(command), CommandPolicy(allow_network=network_access, allow_branch_publish=allow_branch_publish))
    if not decision.allowed:
        raise PermissionError(decision.reason)
    environment = {key: value for key, value in os.environ.items() if key not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}}
    try:
        completed = subprocess.run(list(command), cwd=str(Path(cwd).resolve()), capture_output=True, text=True, timeout=timeout_s, check=False, shell=False, env=environment)
        stdout = completed.stdout.encode("utf-8", errors="replace")[:max_output_bytes].decode("utf-8", errors="replace")
        stderr = completed.stderr.encode("utf-8", errors="replace")[:max_output_bytes].decode("utf-8", errors="replace")
        return CommandResult(tuple(command), completed.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(tuple(command), None, str(exc.stdout or "")[:max_output_bytes], str(exc.stderr or "")[:max_output_bytes], True)
