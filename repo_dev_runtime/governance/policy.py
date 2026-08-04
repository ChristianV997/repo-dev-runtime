"""Fail-closed policy for agent capabilities and provider routing."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimePolicy:
    allow_ollama: bool = False
    allow_aider: bool = False
    allow_omniroute: bool = False
    allow_openclaw: bool = False
    allow_agent_reach: bool = False
    allow_paid_routing: bool = False
    allow_pr_creation: bool = False
    allow_branch_publish: bool = False
    allow_merge: bool = False
    allow_external_provider_benchmark: bool = False
    network_access: bool = False

    def validate(self) -> None:
        if self.allow_merge:
            raise ValueError("automatic merge is not supported by the v1 policy")
        if self.allow_paid_routing and not self.allow_omniroute:
            raise ValueError("paid routing requires OmniRoute to be enabled")
        if self.allow_pr_creation and not self.allow_branch_publish:
            raise ValueError("PR creation requires generated branch publishing")
        if self.allow_external_provider_benchmark and not self.network_access:
            raise ValueError("external provider benchmarking requires network_access")

    def authorize(self, capability: str, *, approved: bool = False) -> None:
        self.validate()
        if capability == "paid_routing" and not (self.allow_paid_routing and approved):
            raise PermissionError("paid routing requires explicit per-task approval")
        if capability == "pr_creation" and not self.allow_pr_creation:
            raise PermissionError("PR creation is disabled")
        if capability == "branch_publish" and not self.allow_branch_publish:
            raise PermissionError("branch publishing is disabled")
        if capability == "merge":
            raise PermissionError("automatic merge is permanently disabled")
        if capability == "network" and not self.network_access:
            raise PermissionError("network access is disabled")
        if capability == "aider" and not self.allow_aider:
            raise PermissionError("Aider is disabled")
        if capability == "external_provider_benchmark" and not (self.allow_external_provider_benchmark and approved):
            raise PermissionError("external provider benchmarking requires explicit approval")
        if capability == "pr_agent_review" and not (self.allow_external_provider_benchmark and approved):
            raise PermissionError("PR-Agent reviewer bridge requires explicit approval")
        if capability not in {
            "paid_routing",
            "pr_creation",
            "branch_publish",
            "merge",
            "network",
            "aider",
            "external_provider_benchmark",
            "pr_agent_review",
        }:
            raise PermissionError(f"unsupported capability: {capability}")
