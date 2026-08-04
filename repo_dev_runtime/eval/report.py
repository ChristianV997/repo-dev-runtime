"""JSON and Markdown report rendering for the fixture benchmark. Both are
deterministic given the same fixture results/scorecards, and both are
redacted defensively even though upstream results should already be
credential-free."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..contracts.models import canonical_json
from ..governance.credentials import redact_json
from ..governance.provider_admission import ProviderAdmissionDecision
from .models import BenchmarkProviderSpec, FixtureCaseResult, ProviderScorecard


def render_json_report(
    *,
    scorecards: Sequence[ProviderScorecard],
    fixture_results: Sequence[FixtureCaseResult],
    provider_specs: Sequence[BenchmarkProviderSpec] = (),
    admission_decisions: Sequence[ProviderAdmissionDecision] = (),
) -> dict[str, Any]:
    payload = {
        "schema": "RepoDev.BenchmarkReport.v1",
        "scorecards": [s.to_dict() for s in scorecards],
        "fixture_results": [r.to_dict() for r in fixture_results],
        "provider_specs": [s.to_dict() for s in provider_specs],
        "admission_decisions": [decision.to_dict() for decision in admission_decisions],
    }
    return redact_json(payload)


def render_json_report_text(**kwargs: Any) -> str:
    return canonical_json(render_json_report(**kwargs))


# (metric label, scorecard attribute) pairs for the comparison table.
# Deliberately no aggregate/ranking column: providers are compared metric
# by metric, never collapsed into one number.
_COMPARISON_METRICS: tuple[tuple[str, str], ...] = (
    ("health check", "health_check_success"),
    ("attempted", "tasks_attempted"),
    ("completed", "tasks_completed"),
    ("safely rejected", "tasks_safely_rejected"),
    ("provider failures", "tasks_failed_provider"),
    ("policy blocked", "tasks_blocked_by_policy"),
    ("structured output valid", "structured_output_valid_count"),
    ("structured output invalid", "structured_output_invalid_count"),
    ("path policy violations", "path_policy_violations"),
    ("worktree escapes", "worktree_escapes_detected"),
    ("tests passed", "test_pass_count"),
    ("tests failed", "test_fail_count"),
    ("repair attempts", "repair_loop_attempts"),
    ("repair successes", "repair_loop_successes"),
    ("reviewer agreement", "reviewer_agreement_count"),
    ("reviewer disagreement", "reviewer_disagreement_count"),
    ("timeouts", "timeout_count"),
    ("output size violations", "output_size_violations"),
    ("credential leak detected", "credential_leak_detected"),
    ("prompt injection resisted", "prompt_injection_resisted"),
)


def render_comparison_table(scorecards: Sequence[ProviderScorecard]) -> str:
    """One row per metric, one column per provider. No aggregate score and
    no ranking — an adoption decision reads the individual metrics."""
    if not scorecards:
        return ""
    providers = [s.provider for s in scorecards]
    lines = [
        "| metric | " + " | ".join(providers) + " |",
        "|---|" + "---|" * len(providers),
    ]
    for label, attribute in _COMPARISON_METRICS:
        cells = [str(getattr(s, attribute)) for s in scorecards]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_markdown_report(
    *,
    scorecards: Sequence[ProviderScorecard],
    fixture_results: Sequence[FixtureCaseResult],
    provider_specs: Sequence[BenchmarkProviderSpec] = (),
    admission_decisions: Sequence[ProviderAdmissionDecision] = (),
) -> str:
    lines = ["# Provider Benchmark Report", ""]
    if len(scorecards) > 1:
        lines.append("## Provider comparison")
        lines.append("")
        lines.append(render_comparison_table(scorecards))
    for scorecard in scorecards:
        lines.append(f"## {scorecard.provider}")
        lines.append("")
        lines.append(f"- tasks attempted: {scorecard.tasks_attempted}")
        lines.append(f"- completed: {scorecard.tasks_completed}, safely rejected: {scorecard.tasks_safely_rejected}, "
                      f"provider failures: {scorecard.tasks_failed_provider}, policy blocked: {scorecard.tasks_blocked_by_policy}")
        lines.append(f"- structured output valid/invalid: {scorecard.structured_output_valid_count}/{scorecard.structured_output_invalid_count}")
        lines.append(f"- test pass/fail: {scorecard.test_pass_count}/{scorecard.test_fail_count}")
        lines.append(f"- repair loop attempts/successes: {scorecard.repair_loop_attempts}/{scorecard.repair_loop_successes}")
        lines.append(f"- reviewer agreement/disagreement: {scorecard.reviewer_agreement_count}/{scorecard.reviewer_disagreement_count}")
        lines.append(f"- timeouts: {scorecard.timeout_count}, output size violations: {scorecard.output_size_violations}")
        lines.append(f"- credential leak detected: {scorecard.credential_leak_detected}")
        lines.append(f"- prompt injection resisted: {scorecard.prompt_injection_resisted}")
        if scorecard.failure_classification:
            classified = ", ".join(f"{k}={v}" for k, v in sorted(scorecard.failure_classification.items()))
            lines.append(f"- failure classification: {classified}")
        lines.append("")
        lines.append("| fixture | outcome | repair attempts | elapsed ms |")
        lines.append("|---|---|---|---|")
        for result in fixture_results:
            if result.provider != scorecard.provider:
                continue
            lines.append(f"| {result.fixture_id} | {result.outcome} | {result.repair_attempts} | {result.elapsed_ms:.1f} |")
        lines.append("")

    if provider_specs:
        lines.append("## Blocked / paper-only provider evaluations")
        lines.append("")
        for spec in provider_specs:
            lines.append(f"### {spec.provider}")
            lines.append(f"- status: {spec.evaluation_status}")
            if spec.blocked_reason:
                lines.append(f"- blocked reason: {spec.blocked_reason}")
            lines.append(f"- license: {spec.license}, source: {spec.source_url}")
            lines.append("")

    if admission_decisions:
        lines.append("## Provider admission decisions")
        lines.append("")
        for decision in admission_decisions:
            lines.append(f"- `{decision.provider}`: `{decision.status}`")
            if decision.reasons:
                lines.append(f"  reasons: {', '.join(decision.reasons)}")
        lines.append("")

    lines.append("## Governance guarantees")
    lines.append("")
    lines.append("- This benchmark never pushes, merges, or creates a pull request.")
    lines.append("- External providers are opt-in and credential-free by default.")
    lines.append("- OpenHands and mini-SWE-agent are evaluation records only and are never part of default routing.")
    lines.append("- A limited-pilot admission is not default routing and does not authorize edits, publication, or merge.")
    return "\n".join(lines) + "\n"


def default_history_path() -> Path:
    """Append-only benchmark history, kept outside any consumer repository —
    mirrors the existing ``~/.repo-dev-runtime/runs/`` convention."""
    return Path.home() / ".repo-dev-runtime" / "eval-history" / f"{date.today().isoformat()}.jsonl"


def append_history(report: Mapping[str, Any], *, path: str | Path | None = None) -> Path:
    """Append one benchmark report as a single JSONL line. Never rewrites
    or truncates prior runs."""
    destination = Path(path) if path else default_history_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(report) + "\n")
    return destination
