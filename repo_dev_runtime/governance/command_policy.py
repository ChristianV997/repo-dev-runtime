"""Fail-closed command policy independent of any source repository."""
from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDecision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class CommandPolicy:
    blocked_substrings: tuple[str, ...] = (
        "git push", "git merge", "git reset --hard", "git rebase", "rm -rf", "sudo ",
        "aws s3 cp", "curl ", "wget ", "invoke-webrequest", "python -c", "python -m pip install",
    )
    allow_network: bool = False


def evaluate_command(command: str, policy: CommandPolicy | None = None) -> CommandDecision:
    if not isinstance(command, str) or not command.strip():
        return CommandDecision(False, "empty command")
    active = policy or CommandPolicy()
    normalized = " ".join(shlex.split(command)).lower()
    for blocked in active.blocked_substrings:
        if blocked.lower() in normalized:
            return CommandDecision(False, f"blocked command pattern: {blocked}")
    if not active.allow_network and any(token in normalized.split() for token in ("curl", "wget", "irm")):
        return CommandDecision(False, "network command is disabled")
    return CommandDecision(True)

