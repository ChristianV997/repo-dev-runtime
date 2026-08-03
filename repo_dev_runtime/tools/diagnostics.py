"""Bounded output reduction for agent-facing diagnostics."""
from __future__ import annotations


def actionable_output(text: str, *, max_lines: int = 80, max_chars: int = 20_000) -> str:
    if max_lines < 1 or max_chars < 1:
        raise ValueError("diagnostic bounds must be positive")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    selected = lines[-max_lines:]
    result = "\n".join(selected)
    if len(result) > max_chars:
        result = result[-max_chars:]
    return result

