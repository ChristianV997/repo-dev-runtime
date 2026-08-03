"""Deterministic fake providers for the evaluation layer's own test suite
and default benchmark run. None of these shell out, call a network, or
depend on any external tool — they exist so the fixture benchmark and its
tests are fully reproducible without installing or trusting third-party
software.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..contracts.models import DevResult, DevTask, RuntimeHealth


def _git_head(root: str | Path) -> str:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip()


@dataclass(frozen=True)
class ProviderTurn:
    """One canned response for :class:`FakeCodingProvider`. ``kind`` is one
    of ``edit`` (returns a valid EditProposal built from ``edits``),
    ``malformed`` (returns ``raw`` verbatim, simulating bad/unparseable
    output), or ``give_up`` (simulates the provider declining an
    under-specified task)."""

    kind: str
    edits: tuple[Mapping[str, Any], ...] = ()
    raw: str = ""
    summary: str = "evaluation turn"


class FakeCodingProvider:
    """Implements the ``DevelopmentRuntime`` protocol with a fixed,
    ordered sequence of canned turns — one per ``execute()`` call. The
    last turn repeats once its sequence is exhausted, so a harness can
    call it more times than turns were configured (e.g. extra repair
    attempts) without raising.
    """

    name = "fake_coding_provider"

    def __init__(self, turns: Sequence[ProviderTurn]) -> None:
        if not turns:
            raise ValueError("at least one ProviderTurn is required")
        self._turns = list(turns)
        self._index = 0

    def health(self) -> RuntimeHealth:
        return RuntimeHealth(self.name, True, True, ("fake", "deterministic"), "")

    def execute(self, task: DevTask) -> DevResult:
        turn = self._turns[min(self._index, len(self._turns) - 1)]
        self._index += 1
        if turn.kind == "give_up":
            return DevResult(
                task_id=task.task_id,
                runtime=self.name,
                status="failed",
                error_type="provider_declined",
                error_message="task is under-specified; declining rather than guessing",
            )
        if turn.kind == "malformed":
            return DevResult(task_id=task.task_id, runtime=self.name, status="succeeded", output=turn.raw)
        if turn.kind != "edit":
            raise ValueError(f"unsupported ProviderTurn.kind: {turn.kind!r}")
        proposal = {
            "schema": "RepoDev.EditProposal.v1",
            "proposal_id": str(uuid.uuid4()),
            "task_id": task.task_id,
            "base_commit": _git_head(task.repository),
            "context_hash": "eval",
            "summary": turn.summary,
            "edits": [dict(edit) for edit in turn.edits],
        }
        return DevResult(task_id=task.task_id, runtime=self.name, status="succeeded", output=json.dumps(proposal))


def default_fake_provider_factory(case: Any) -> FakeCodingProvider:
    """Builds a fresh :class:`FakeCodingProvider` preloaded with the given
    fixture case's canned turns. Used as the default, deterministic
    ``make_provider`` callable for ``eval.harness.run_fixture_benchmark``.
    """
    return FakeCodingProvider(case.provider_turns)
