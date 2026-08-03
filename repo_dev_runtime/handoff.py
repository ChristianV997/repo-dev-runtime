"""Minimal, secret-free Markdown handoff for repository sessions."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def render_handoff(*, repository: str, run_id: str, status: str, next_action: str, tests: dict[str, Any] | None = None) -> str:
    safe_tests = json.dumps(tests or {}, sort_keys=True, indent=2, ensure_ascii=True)
    safe_tests = re.sub(r'"([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)"(?=\s*:)', '"[REDACTED]"', safe_tests)
    safe_tests = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", safe_tests)
    safe_tests = re.sub(r'(?i)("(?:value|token|secret|password|api_key)"\s*:\s*)"[^"]*"', r'\1"[REDACTED]"', safe_tests)
    return f"# Repository Development Handoff\n\n- Repository: `{repository}`\n- Run: `{run_id}`\n- Status: `{status}`\n- Next action: {next_action}\n\n## Test summary\n\n```json\n{safe_tests}\n```\n"


def write_handoff(path: str | Path, **kwargs: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_handoff(**kwargs), encoding="utf-8")
    return destination
