"""Minimal, secret-free Markdown handoff for repository sessions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_handoff(*, repository: str, run_id: str, status: str, next_action: str, tests: dict[str, Any] | None = None) -> str:
    safe_tests = json.dumps(tests or {}, sort_keys=True, indent=2).replace("OPENAI_API_KEY", "[REDACTED]").replace("ANTHROPIC_API_KEY", "[REDACTED]")
    return f"# Repository Development Handoff\n\n- Repository: `{repository}`\n- Run: `{run_id}`\n- Status: `{status}`\n- Next action: {next_action}\n\n## Test summary\n\n```json\n{safe_tests}\n```\n"


def write_handoff(path: str | Path, **kwargs: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_handoff(**kwargs), encoding="utf-8")
    return destination
