"""Bounded command execution for repository-local tools."""
from __future__ import annotations

import os
import signal
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..governance.command_policy import CommandPolicy, evaluate_command
from ..governance.credentials import redact_text


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
    if not 0.001 <= float(timeout_s):
        raise ValueError("timeout_s must be positive")
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be positive")
    decision = evaluate_command(shlex.join(command), CommandPolicy(allow_network=network_access, allow_branch_publish=allow_branch_publish))
    if not decision.allowed:
        raise PermissionError(decision.reason)
    environment = {key: value for key, value in os.environ.items() if key not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}}
    launch_kwargs: dict[str, object] = {}
    if os.name == "nt":
        launch_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        launch_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        list(command),
        cwd=str(Path(cwd).resolve()),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env=environment,
        **launch_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        stdout = redact_text(_bounded_text(stdout, max_output_bytes))
        stderr = redact_text(_bounded_text(stderr, max_output_bytes))
        return CommandResult(tuple(command), process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        trailing_stdout, trailing_stderr = process.communicate()
        stdout = _bounded_text(_coerce_output(exc.stdout) + _coerce_output(trailing_stdout), max_output_bytes)
        stderr = _bounded_text(_coerce_output(exc.stderr) + _coerce_output(trailing_stderr), max_output_bytes)
        return CommandResult(tuple(command), process.returncode, redact_text(stdout), redact_text(stderr), True)


def _coerce_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _bounded_text(value: object, max_output_bytes: int) -> str:
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be positive")
    text = _coerce_output(value)
    return text.encode("utf-8", errors="replace")[:max_output_bytes].decode("utf-8", errors="replace")


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop a timed-out command and descendants before returning.

    Quality checks and provider helpers may spawn test runners, shells, or
    workers. Killing only the direct process can leave those children running
    against a disposable worktree after the runtime has reported a timeout.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            process.kill()
