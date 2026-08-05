"""Declarative, one-shot scheduling and resumable task state.

Callers that need to serialize repeated invocations of the same task (for
example a cron-driven runner keyed on a schedule name) should use
``TaskStateStore.claim()`` rather than ``load()`` + ``update()``: only
``claim()`` performs the check and the write under a single lock, so two
concurrent invocations cannot both decide the task is unclaimed.

DevelopmentWorkflow (workflow.py) does not call TaskStateStore. Its own
resume mechanism is self-sufficient
- it tracks per-role/promotion status directly via checksum-covered
RunEnvelope artifacts, so it does not need this module's lower-level,
single-host, one-shot task-state tracking underneath it. This module
exists as a reusable library for callers who want that lower-level
primitive on its own; it is not wired into any runtime entry point today.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection


_MAX_STATE_BYTES = 2_000_000
_LOCK_TIMEOUT_S = 10.0
_VALID_STATUSES = frozenset({"pending", "running", "succeeded", "failed", "blocked"})


def _validate_task_entry(task_id: str, status: str) -> None:
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 256 or status not in _VALID_STATUSES:
        raise ValueError("invalid task state")


@dataclass(frozen=True)
class SchedulePlan:
    command: str
    interval_minutes: int = 60
    dry_run_command: str = ""
    daemon_implemented: bool = False

    def validate(self) -> None:
        if not self.command.strip() or not 1 <= self.interval_minutes <= 10080:
            raise ValueError("invalid schedule plan")
        if self.daemon_implemented:
            raise ValueError("background daemons are not supported by the v1 runtime")


class TaskStateStore:
    """Small atomic JSON state store used to resume tasks without a daemon."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().absolute()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.is_symlink():
            raise ValueError("task state lock must not be a symlink")
        with self.lock_path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                deadline = time.monotonic() + _LOCK_TIMEOUT_S
                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("timed out acquiring task state lock")
                        time.sleep(0.01)
            else:
                import fcntl

                deadline = time.monotonic() + _LOCK_TIMEOUT_S
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("timed out acquiring task state lock")
                        time.sleep(0.01)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("task state must be a regular file")
        if self.path.stat().st_size > _MAX_STATE_BYTES:
            raise ValueError("task state exceeds size limit")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("task state must be an object")
        return value

    def load(self) -> dict[str, Any]:
        with self._lock():
            return self._load_unlocked()

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        """Replace the state file atomically. Caller must hold ``_lock()``."""
        payload = json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if len(payload.encode("utf-8")) > _MAX_STATE_BYTES:
            raise ValueError("task state exceeds size limit")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def update(self, task_id: str, status: str, **details: Any) -> dict[str, Any]:
        _validate_task_entry(task_id, status)
        with self._lock():
            state = self._load_unlocked()
            state[task_id] = {"status": status, **details}
            self._write_unlocked(state)
            return state[task_id]

    def claim(self, task_id: str, status: str, *, unless_status: Collection[str] = (), **details: Any) -> dict[str, Any] | None:
        """Atomically take ownership of ``task_id`` unless already claimed.

        ``load()`` followed by ``update()`` is two separate lock
        acquisitions, so two concurrent callers can both observe an
        unclaimed task and both proceed - a time-of-check/time-of-use race
        that callers cannot close from the outside. This performs the read
        and the conditional write inside a *single* lock acquisition.

        Returns the new entry when the claim succeeded, or ``None`` when
        the existing entry's status is in ``unless_status`` (already
        claimed or already finished), leaving the stored state untouched.
        """
        _validate_task_entry(task_id, status)
        blocked_statuses = frozenset(unless_status)
        with self._lock():
            state = self._load_unlocked()
            existing = state.get(task_id)
            if isinstance(existing, dict) and existing.get("status") in blocked_statuses:
                return None
            state[task_id] = {"status": status, **details}
            self._write_unlocked(state)
            return state[task_id]

    def reap_stuck(self, task_id: str, *, stuck_status: str = "running", timeout_s: float) -> bool:
        """Clear ``task_id`` if it is stuck at ``stuck_status`` past ``timeout_s``.

        A crashed or killed process can leave a claim() at "running"
        forever, since nothing here runs a background reaper. An external
        caller (a cron/systemd wrapper) is expected to call this before
        claim() on each scheduled fire. No per-entry timestamp exists in
        this store's schema, so the state file's own mtime is used as a
        proxy for "when was this entry last written" - exact enough for a
        state file dedicated to a single task_id, which is how the
        AWS autonomous-deployment wrapper uses this. Performing the
        mtime check and the conditional delete inside one lock
        acquisition (rather than a caller doing its own unlocked
        read-then-write) avoids corrupting or losing a concurrent
        claim()/update() call.

        Returns True if a stuck entry was cleared, False otherwise
        (nothing to reap, wrong status, or not yet past the timeout).
        """
        if not task_id.strip():
            raise ValueError("invalid task state")
        with self._lock():
            if not self.path.exists():
                return False
            state = self._load_unlocked()
            entry = state.get(task_id)
            if not isinstance(entry, dict) or entry.get("status") != stuck_status:
                return False
            if time.time() - self.path.stat().st_mtime <= timeout_s:
                return False
            del state[task_id]
            self._write_unlocked(state)
            return True

