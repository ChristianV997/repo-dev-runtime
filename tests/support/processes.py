"""Small cross-platform process-state helpers for subprocess tests."""
from __future__ import annotations

import os
from pathlib import Path


def pid_is_running(pid: int) -> bool:
    """Return false for missing or terminated zombie processes."""
    if os.name != "nt":
        stat_path = Path(f"/proc/{pid}/stat")
        if not stat_path.exists():
            return False
        try:
            state = stat_path.read_text(encoding="utf-8").split(") ", 1)[1].split()[0]
        except (OSError, IndexError):
            return False
        return state not in {"Z", "X"}
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
