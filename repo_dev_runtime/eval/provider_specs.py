"""Benchmark-provider specifications for OpenHands and mini-SWE-agent.
Neither is adopted as a production backend, installed, or vendored here.
These are paper-only evaluation records (see eval.models.BenchmarkProviderSpec):
producing one never creates a runtime adapter, never touches
runtimes/factory.py or runtimes/registry.py, and therefore cannot enter
default routing. A live benchmark of either provider would require
installing third-party software this environment cannot vet; both specs
are marked blocked with an explicit reason instead.
"""
from __future__ import annotations

from .models import BenchmarkProviderSpec

OPENHANDS_SPEC = BenchmarkProviderSpec(
    provider="openhands",
    installation_method="pip install openhands-ai, or a published Docker image (All-Hands-AI/OpenHands)",
    license="MIT",
    execution_interface="CLI / Docker container with a headless runtime; also exposes a Python SDK",
    isolation_requirements="Recommended to run inside its own Docker sandbox; direct host execution can invoke arbitrary shell commands the agent chooses",
    network_requirements="Requires outbound access to an LLM provider API and, for many workflows, general internet access from within its sandbox",
    output_format="Structured event/action stream (its own internal event schema), not RepoDev.EditProposal.v1 or RepoDev.ReviewVerdict.v1",
    edits_files_directly=True,
    auto_commit_can_be_disabled=None,
    known_policy_risks=(
        "agent can execute arbitrary shell commands inside its sandbox unless explicitly restricted",
        "no built-in equivalent to this runtime's fail-closed command policy or forbidden-path enforcement",
        "default workflows may attempt git operations (commit/branch) without an external gate",
    ),
    expected_integration_effort="medium-to-high: would need a bounded subprocess/Docker adapter translating its event stream into DevResult/EditProposal, plus verification that its sandbox cannot reach the host worktree's git remote",
    evaluation_status="blocked",
    blocked_reason="cannot be verified or live-benchmarked in this environment without installing and running third-party, network-capable software that has not undergone a security review here",
    source_url="https://github.com/All-Hands-AI/OpenHands",
)

MINI_SWE_AGENT_SPEC = BenchmarkProviderSpec(
    provider="mini_swe_agent",
    installation_method="pip install mini-swe-agent",
    license="MIT",
    execution_interface="Minimal CLI loop: prompts an LLM, executes the returned shell command, feeds output back",
    isolation_requirements="No built-in sandboxing; typically run inside a container or disposable VM by its own documentation",
    network_requirements="Requires outbound access to an LLM provider API; the agent's shell commands may themselves attempt further network access unless externally blocked",
    output_format="Raw shell command + output transcript, not a structured proposal/review schema",
    edits_files_directly=True,
    auto_commit_can_be_disabled=None,
    known_policy_risks=(
        "executes whatever shell command the model proposes, with no built-in command policy",
        "no notion of allowed/forbidden paths; relies entirely on the surrounding sandbox for containment",
    ),
    expected_integration_effort="medium: its transcript-based interface is simpler than OpenHands', but still needs a bounded subprocess wrapper plus translation into the RepoDev.EditProposal.v1 contract before it could participate in the governed workflow",
    evaluation_status="blocked",
    blocked_reason="cannot be verified or live-benchmarked in this environment without installing and running third-party, network-capable software that has not undergone a security review here",
    source_url="https://github.com/SWE-agent/mini-swe-agent",
)


def default_provider_specs() -> tuple[BenchmarkProviderSpec, ...]:
    """Feeds only the benchmark report generator — no runtime adapter, no
    factory/registry wiring, so neither provider can ever enter default
    routing."""
    return (OPENHANDS_SPEC, MINI_SWE_AGENT_SPEC)
