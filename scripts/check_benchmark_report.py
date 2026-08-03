#!/usr/bin/env python3
"""Assert a fake-provider benchmark report has not regressed.

The fake-provider benchmark is fully deterministic and dependency-free,
which makes it a fast CI signal: if a change silently alters how a
fixture is classified, this fails the build. Every fixture must have run,
and each one's recorded outcome must still match its declared
``FixtureCase.expected_outcome``.

Usage:
    python scripts/check_benchmark_report.py <report.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Direct ``python scripts/check_benchmark_report.py`` execution places only
# ``scripts/`` on sys.path. Make the repository package importable without
# requiring callers or CI to provide an ambient PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_dev_runtime.eval.fixtures import FIXTURE_CASES


def check(report: dict) -> list[str]:
    problems: list[str] = []
    results = {item["fixture_id"]: item for item in report.get("fixture_results", [])}

    expected_ids = {case.fixture_id for case in FIXTURE_CASES}
    missing = expected_ids - set(results)
    if missing:
        problems.append(f"fixtures missing from the report: {sorted(missing)}")

    unexpected = set(results) - expected_ids
    if unexpected:
        problems.append(f"report contains unknown fixtures: {sorted(unexpected)}")

    for case in FIXTURE_CASES:
        item = results.get(case.fixture_id)
        if item is None:
            continue
        actual = item.get("outcome")
        if actual != case.expected_outcome:
            problems.append(
                f"{case.fixture_id}: outcome regressed to {actual!r}, expected {case.expected_outcome!r}"
                f" (error: {item.get('error_message') or 'none'})"
            )

    if not report.get("scorecards"):
        problems.append("report contains no scorecard")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    problems = check(report)
    if problems:
        print("benchmark regression detected:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"benchmark ok: {len(FIXTURE_CASES)} fixtures matched their expected outcomes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
