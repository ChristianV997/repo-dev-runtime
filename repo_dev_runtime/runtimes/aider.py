"""Sandboxed Aider adapter for controlled fixture benchmarking.

This adapter is deliberately not registered in ``default_registry``. Aider
edits files directly, so it runs only in a fresh temporary copy of a bounded
set of text files. The governed worktree receives a normal
``RepoDev.EditProposal.v1`` generated from that sandbox diff and remains
unchanged until ``PatchApplier`` validates it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from ..contracts.models import DevResult, DevTask, RuntimeHealth
from ..edits import SCHEMA
from ..governance.credentials import CredentialAllowlist, build_subprocess_env, redact_text

_CONTRACT_VALUE = re.compile(r"\b(?P<name>task_id|base_commit|context_hash)=([0-9a-f]+)")
_MAX_FILES = 100
_MAX_INPUT_BYTES = 2_000_000
_EXCLUDED_PARTS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def _command_from_env() -> tuple[str, ...]:
    value = os.getenv("DEV_RUNTIME_AIDER_COMMAND", "").strip()
    if not value:
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return tuple(shlex.split(value, posix=os.name != "nt"))
    return tuple(parsed) if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed) else ()


def _inside_allowed(relative: str, allowed_paths: tuple[str, ...]) -> bool:
    if not allowed_paths:
        return False
    return "." in allowed_paths or any(
        relative == prefix.rstrip("/") or relative.startswith(prefix.rstrip("/") + "/")
        for prefix in allowed_paths
    )


def _safe_relative(root: Path, path: Path, allowed_paths: tuple[str, ...]) -> str | None:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return None
    parts = PurePosixPath(relative).parts
    if not relative or any(part in _EXCLUDED_PARTS or part.startswith(".") for part in parts):
        return None
    if Path(relative).name.startswith(".env") or not _inside_allowed(relative, allowed_paths):
        return None
    return relative


def _read_text(path: Path, *, max_bytes: int) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data or len(data) > max_bytes or b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _snapshot(root: Path, allowed_paths: tuple[str, ...], *, max_files: int, max_bytes: int) -> dict[str, str]:
    """Collect bounded, ordinary text files without following symlinks."""
    files: dict[str, str] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if len(files) >= max_files:
            break
        if not path.is_file() or path.is_symlink():
            continue
        relative = _safe_relative(root, path, allowed_paths)
        if relative is None:
            continue
        content = _read_text(path, max_bytes=max_bytes)
        if content is None:
            continue
        size = len(content.encode("utf-8"))
        if total + size > max_bytes:
            continue
        files[relative] = content
        total += size
    return files


def _bindings(prompt: str) -> Mapping[str, str]:
    values: dict[str, str] = {}
    for match in _CONTRACT_VALUE.finditer(prompt):
        values[match.group("name")] = match.group(2)
    return values


def _aider_objective(prompt: str) -> str:
    """Extract the actual objective from a governed implementer prompt.

    The outer contract correctly tells ordinary models to return JSON. Aider
    is different: it edits its private sandbox as its native protocol, then
    this adapter translates the resulting diff back into that JSON contract.
    Passing the outer instruction through would make the two protocols
    contradict one another and produces no edits.
    """
    marker = "\n\nObjective:\n"
    if marker not in prompt:
        return prompt
    objective = prompt.split(marker, 1)[1]
    return objective.split("\n\nRepository context:", 1)[0].strip()


def _run_aider_command(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    """Run Aider in its own process group and kill the whole tree on timeout."""
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env=dict(environment),
        start_new_session=os.name != "nt",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        # ``Popen.kill`` only terminates the launcher. Aider can start helper
        # processes, so Windows needs taskkill's explicit tree mode.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            shell=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class AiderRuntime:
    """Run Aider in a copied sandbox and return a validated proposal payload.

    Configuration is explicit: ``DEV_RUNTIME_AIDER=true``,
    ``DEV_RUNTIME_AIDER_COMMAND`` (a JSON argv array or simple command), and
    ``AIDER_MODEL`` are all required. Credentials are forwarded only to the
    Aider subprocess through an allowlisted environment.
    """

    name = "aider"

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        model: str | None = None,
        enabled: bool | None = None,
        sandbox_root: str | Path | None = None,
    ) -> None:
        self.command = tuple(command) if command else _command_from_env()
        self.model = model or os.getenv("AIDER_MODEL", "").strip()
        self.enabled = enabled if enabled is not None else os.getenv("DEV_RUNTIME_AIDER", "false").lower() in {"1", "true", "yes", "on"}
        self.sandbox_root = Path(sandbox_root).resolve() if sandbox_root else None

    @classmethod
    def create(cls) -> "AiderRuntime":
        return cls()

    def health(self) -> RuntimeHealth:
        if not self.enabled:
            return RuntimeHealth(self.name, False, False, ("sandboxed_editing", "proposal_translation"), "runtime disabled")
        if not self.command or not self.model:
            return RuntimeHealth(self.name, False, False, ("sandboxed_editing", "proposal_translation"), "Aider command or model is not configured")
        executable = self.command[0]
        return RuntimeHealth(
            self.name,
            True,
            shutil.which(executable) is not None,
            ("sandboxed_editing", "proposal_translation", "no_default_routing"),
            "",
        )

    def execute(self, task: DevTask) -> DevResult:
        if not self.enabled:
            return DevResult(task.task_id, self.name, "skipped", error_type="runtime_disabled")
        health = self.health()
        if not health.configured:
            return DevResult(task.task_id, self.name, "blocked", error_type="aider_not_configured", error_message=health.detail)
        if not health.reachable:
            return DevResult(task.task_id, self.name, "blocked", error_type="aider_command_not_found")
        if task.role != "implementer":
            return DevResult(task.task_id, self.name, "skipped", error_type="unsupported_role")
        if not task.allowed_paths:
            return DevResult(task.task_id, self.name, "blocked", error_type="allowed_paths_required")

        bindings = _bindings(task.prompt)
        if bindings.get("task_id") != task.task_id or not bindings.get("base_commit") or not bindings.get("context_hash"):
            return DevResult(task.task_id, self.name, "blocked", error_type="missing_edit_contract")

        source = Path(task.repository).resolve()
        if not source.is_dir():
            return DevResult(task.task_id, self.name, "failed", error_type="repository_not_found")
        before = _snapshot(source, task.allowed_paths, max_files=_MAX_FILES, max_bytes=_MAX_INPUT_BYTES)
        if not before:
            return DevResult(task.task_id, self.name, "blocked", error_type="no_safe_text_files")

        started = time.perf_counter()
        try:
            # On Windows, Aider's optional UI dependency stack can retain a
            # cache handle briefly after its process exits. Best-effort
            # cleanup must not discard an otherwise valid, sandbox-derived
            # proposal; any retained directory is still an isolated temp path.
            with tempfile.TemporaryDirectory(
                prefix="repo-dev-aider-",
                dir=str(self.sandbox_root) if self.sandbox_root else None,
                ignore_cleanup_errors=True,
            ) as temporary:
                sandbox = Path(temporary) / "workspace"
                sandbox.mkdir()
                for relative, content in before.items():
                    target = sandbox / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Preserve source bytes exactly. Text-mode writes can
                    # rewrite LF/CRLF on Windows and manufacture a false
                    # proposal before Aider touches anything.
                    target.write_bytes(content.encode("utf-8"))
                home = Path(temporary) / "home"
                home.mkdir()
                allowlist = CredentialAllowlist(
                    provider=self.name,
                    env_prefixes=("AIDER_", "OLLAMA_", "OPENAI_", "ANTHROPIC_"),
                )
                environment = build_subprocess_env(allowlist)
                # Keep all user-profile locations inside the disposable
                # sandbox. Some Windows dependencies resolve browser/config
                # paths through APPDATA/LOCALAPPDATA rather than HOME.
                environment.update(
                    {
                        "HOME": str(home),
                        "USERPROFILE": str(home),
                        "APPDATA": str(home / "AppData" / "Roaming"),
                        "LOCALAPPDATA": str(home / "AppData" / "Local"),
                        "TEMP": str(home),
                        "TMP": str(home),
                    }
                )
                command = [
                    *self.command,
                    "--model", self.model,
                    "--no-git",
                    "--no-gitignore",
                    "--no-auto-commits",
                    "--no-analytics",
                    "--no-browser",
                    "--no-gui",
                    "--yes-always",
                    "--message", _aider_objective(task.prompt),
                    *sorted(before),
                ]
                completed = _run_aider_command(
                    command,
                    cwd=sandbox,
                    environment=environment,
                    timeout_s=task.timeout_s,
                )
                if completed.returncode != 0:
                    message = redact_text((completed.stderr or completed.stdout).strip()[:500])
                    return DevResult(task.task_id, self.name, "failed", error_type="aider_exit", error_message=message, telemetry=_telemetry(started, self.model))
                after = _snapshot(sandbox, task.allowed_paths, max_files=_MAX_FILES, max_bytes=_MAX_INPUT_BYTES)
        except subprocess.TimeoutExpired:
            return DevResult(task.task_id, self.name, "failed", error_type="timeout", telemetry=_telemetry(started, self.model))
        except OSError as exc:
            return DevResult(task.task_id, self.name, "failed", error_type=type(exc).__name__, error_message=redact_text(str(exc)[:500]), telemetry=_telemetry(started, self.model))

        deleted = set(before) - set(after)
        if deleted:
            return DevResult(task.task_id, self.name, "failed", error_type="unsupported_deletion", error_message=", ".join(sorted(deleted)), telemetry=_telemetry(started, self.model))
        changed = tuple(path for path in sorted(after) if before.get(path) != after[path])
        if not changed:
            return DevResult(task.task_id, self.name, "failed", error_type="no_edits", telemetry=_telemetry(started, self.model))
        edits = [
            {
                "path": path,
                "format": "whole_file",
                "content": after[path],
                "expected_file_hash": hashlib.sha256(before[path].encode("utf-8")).hexdigest() if path in before else "",
            }
            for path in changed
        ]
        proposal = {
            "schema": SCHEMA,
            "proposal_id": f"aider-{uuid.uuid4().hex}",
            "task_id": task.task_id,
            "base_commit": bindings["base_commit"],
            "context_hash": bindings["context_hash"],
            "summary": "sandboxed Aider proposal",
            "edits": edits,
        }
        output = json.dumps(proposal, ensure_ascii=False)
        if len(output.encode("utf-8")) > task.max_output_bytes:
            return DevResult(task.task_id, self.name, "failed", error_type="output_limit", telemetry=_telemetry(started, self.model))
        return DevResult(task.task_id, self.name, "succeeded", output=output, changed_files=changed, telemetry=_telemetry(started, self.model) | {"changed_file_count": len(changed)})


def _telemetry(started: float, model: str) -> dict[str, object]:
    return {
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "model": model,
        "execution_mode": "sandbox_copy",
        "sandbox_cleanup": "best_effort",
    }
