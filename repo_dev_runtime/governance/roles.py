"""Repository-neutral role permissions for the development workflow."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRole:
    role_id: str
    description: str
    safe_to_auto_run: bool
    requires_human_review: bool
    allowed_commands: tuple[str, ...] = ()
    blocked_commands: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.role_id.strip() or not self.description.strip():
            raise ValueError("role identity is required")
        if self.safe_to_auto_run and self.requires_human_review:
            raise ValueError("a role cannot be both auto-runnable and review-required")


DEFAULT_ROLES = (
    AgentRole("planner", "Inspect the repository and produce an implementation plan", True, False, ("git status", "git diff", "git log")),
    AgentRole("implementer", "Implement approved changes in an isolated worktree", True, False),
    AgentRole("tester", "Run bounded tests and quality checks", True, False),
    AgentRole("reviewer", "Review changes and identify risks", False, True),
    AgentRole("integrator", "Prepare a human-review handoff without merging", False, True, blocked_commands=("git merge", "git push")),
)


def role_map() -> dict[str, AgentRole]:
    for role in DEFAULT_ROLES:
        role.validate()
    return {role.role_id: role for role in DEFAULT_ROLES}
