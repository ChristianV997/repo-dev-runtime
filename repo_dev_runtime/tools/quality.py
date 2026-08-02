"""Bounded wrappers for repository quality tools."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .runner import run_command


def run_semgrep(root: str | Path, *, config: str = "semgrep/ai-safety.yml", fail_on_error: bool = False) -> dict[str, object]:
    command = ["semgrep", "scan", "--config", config, "--json"]
    if fail_on_error:
        command.append("--error")
    result = run_command(command, cwd=root, max_output_bytes=2_000_000)
    return {"tool": "semgrep", "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": result.timed_out}


def snapshot_with_repomix(root: str | Path, paths: Sequence[str]) -> dict[str, object]:
    bounded = [item for item in paths if item and not item.startswith(".") and ".." not in Path(item).parts]
    if not bounded:
        raise ValueError("at least one safe relative path is required")
    result = run_command(["repomix", *bounded, "--compress", "--no-files", "--stdout"], cwd=root, max_output_bytes=2_000_000)
    return {"tool": "repomix", "paths": bounded, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timed_out": result.timed_out}
