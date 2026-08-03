"""JSON and Markdown report rendering for the fixture benchmark. Both are
deterministic given the same fixture results/scorecards, and both are
redacted defensively even though upstream results should already be
credential-free."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..contracts.models import canonical_json
from ..governance.credentials import redact_json
from .models import BenchmarkProviderSpec, FixtureCaseResult, ProviderScorecard


def render_json_report(
    *,
    scorecards: Sequence[ProviderScorecard],
    fixture_results: Sequence[FixtureCaseResult],
    provider_specs: Sequence[BenchmarkProviderSpec] = (),
) -> dict[str, Any]:
    payload = {
        "schema": "RepoDev.BenchmarkReport.v1",
        "scorecards": [s.to_dict() for s in scorecards],
        "fixture_results": [r.to_dict() for r in fixture_results],
        "provider_specs": [s.to_dict() for s in provider_specs],
    }
    return redact_json(payload)


def render_json_report_text(**kwargs: Any) -> str:
    return canonical_json(render_json_report(**kwargs))


def render_markdown_report(
    *,
    scorecards: Sequence[ProviderScorecard],
    fixture_results: Sequence[FixtureCaseResult],
    provider_specs: Sequence[BenchmarkProviderSpec] = (),
) -> str:
    lines = ["# Provider Benchmark Report", ""]
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

    lines.append("## Governance guarantees")
    lines.append("")
    lines.append("- This benchmark never pushes, merges, or creates a pull request.")
    lines.append("- External providers are opt-in and credential-free by default.")
    lines.append("- OpenHands and mini-SWE-agent are evaluation records only and are never part of default routing.")
    return "\n".join(lines) + "\n"
