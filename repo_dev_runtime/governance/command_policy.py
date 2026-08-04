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
        "aws s3 cp", "python -c", "python -m pip install",
    )
    # Package-manager install/fetch invocations that reach the network.
    # This is a denylist, not a parser, so it is necessarily incomplete
    # (the same accepted-limitation pattern documented for credential
    # redaction in docs/credential-policy.md) — a manifest that genuinely
    # needs a network-capable command must set network_access=True
    # explicitly rather than relying on every possible network invocation
    # being enumerated here.
    network_substrings: tuple[str, ...] = (
        "pip install", "pip3 install", "npm install", "npm ci", "npm i ",
        "yarn add", "yarn install", "pnpm add", "pnpm install",
        "poetry install", "poetry add", "gem install", "cargo install",
        "go install", "go get",
    )
    allow_network: bool = False
    allow_branch_publish: bool = False


def evaluate_command(command: str, policy: CommandPolicy | None = None) -> CommandDecision:
    if not isinstance(command, str) or not command.strip():
        return CommandDecision(False, "empty command")
    active = policy or CommandPolicy()
    try:
        normalized = " ".join(shlex.split(command)).lower()
    except ValueError:
        # Unbalanced quoting. A fail-closed policy must *deny* input it
        # cannot parse, not raise out of the decision API and leave the
        # caller to handle an exception it does not expect.
        return CommandDecision(False, "command could not be parsed")
    for blocked in active.blocked_substrings:
        if blocked == "git push" and active.allow_branch_publish and _is_generated_push(normalized):
            continue
        if blocked.lower() in normalized:
            return CommandDecision(False, f"blocked command pattern: {blocked}")
    if not active.allow_network:
        if any(token in normalized.split() for token in ("curl", "wget", "invoke-webrequest", "irm", "iwr")):
            return CommandDecision(False, "network command is disabled")
        for blocked in active.network_substrings:
            if blocked in normalized:
                return CommandDecision(False, f"network command is disabled: {blocked.strip()}")
    return CommandDecision(True)


def _is_generated_push(normalized: str) -> bool:
    parts = normalized.split()
    return len(parts) == 4 and parts[:3] == ["git", "push", "origin"] and parts[3].startswith("repo-dev/")
