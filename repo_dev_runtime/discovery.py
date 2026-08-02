"""Read-only capability discovery for any repository host."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


COMMANDS = ("python", "node", "npm", "git", "ollama", "semgrep", "repomix", "uv", "docker")


def _version(command: str) -> str:
    path = shutil.which(command)
    if not path:
        return ""
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return "available"
    return (result.stdout or result.stderr).strip().splitlines()[0][:200]


def probe_repository(root: str | Path) -> dict[str, Any]:
    path = Path(root).resolve()
    return {
        "root": str(path),
        "git": (path / ".git").exists(),
        "manifest": (path / ".dev-runtime" / "repository.json").exists(),
        "tools": {command: _version(command) for command in COMMANDS},
        "markers": {name: (path / name).exists() for name in ("pyproject.toml", "package.json", "pytest.ini", "tests", "AGENTS.md", "CLAUDE.md", ".serena", "semgrep")},
    }
