"""Read-only capability discovery for any repository host."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


COMMANDS = ("python", "node", "npm", "npx", "git", "ollama", "semgrep", "repomix", "uv", "docker", "codeql", "serena")


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


def validate_consumer(root: str | Path) -> dict[str, Any]:
    """Read-only validation for a repository using the runtime manifest."""
    path = Path(root).resolve()
    manifest = probe_repository(path)
    errors: list[str] = []
    manifest_path = path / ".dev-runtime" / "repository.json"
    if not manifest_path.exists():
        errors.append("missing .dev-runtime/repository.json")
    if not manifest["git"]:
        errors.append("repository is not a Git checkout")
    return {"root": str(path), "valid": not errors, "errors": errors, "capabilities": manifest}
