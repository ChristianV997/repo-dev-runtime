"""Tests for scripts/check_benchmark_report.py — the CI regression gate."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo_dev_runtime.eval.fixtures import FIXTURE_CASES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_benchmark_report.py"


def _subprocess_env() -> dict[str, str]:
    """`python -m repo_dev_runtime.cli` resolves imports because `-m`
    prepends the *subprocess's own cwd* to sys.path — inherited for free
    here since subprocess.run defaults to the parent's cwd (the repo
    root). Invoking scripts/check_benchmark_report.py directly does not
    get that treatment: a bare `python script.py` only adds the script's
    *own* directory (scripts/) to sys.path, never the repo root, so this
    must not rely on an ambient PYTHONPATH some outer shell happened to
    export — CI's `python -m pytest -q` step does not set one."""
    return {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_benchmark_report", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_report() -> dict:
    return {
        "schema": "RepoDev.BenchmarkReport.v1",
        "scorecards": [{"provider": "fake"}],
        "fixture_results": [
            {"fixture_id": case.fixture_id, "outcome": case.expected_outcome} for case in FIXTURE_CASES
        ],
    }


def test_clean_report_passes():
    assert _load_checker().check(_clean_report()) == []


def test_regressed_outcome_is_detected():
    report = _clean_report()
    report["fixture_results"][0]["outcome"] = "provider_failure"

    problems = _load_checker().check(report)
    assert any("regressed" in problem for problem in problems)


def test_missing_fixture_is_detected():
    report = _clean_report()
    dropped = report["fixture_results"].pop()

    problems = _load_checker().check(report)
    assert any(dropped["fixture_id"] in problem for problem in problems)


def test_unknown_fixture_is_detected():
    report = _clean_report()
    report["fixture_results"].append({"fixture_id": "not_a_real_fixture", "outcome": "succeeded"})

    problems = _load_checker().check(report)
    assert any("unknown fixtures" in problem for problem in problems)


def test_missing_scorecard_is_detected():
    report = _clean_report()
    report["scorecards"] = []

    assert any("no scorecard" in problem for problem in _load_checker().check(report))


def test_script_runs_end_to_end_against_a_real_benchmark(tmp_path):
    report_path = tmp_path / "benchmark.json"
    benchmark = subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "benchmark",
            "--provider", "fake", "--fake-reviewer",
            "--fixtures-root", str(tmp_path / "fixtures"),
            "--json-out", str(report_path),
        ],
        capture_output=True, text=True, check=False,
    )
    assert benchmark.returncode == 0, benchmark.stderr

    check = subprocess.run(
        [sys.executable, str(_SCRIPT), str(report_path)],
        capture_output=True, text=True, check=False, env=_subprocess_env(),
    )
    assert check.returncode == 0, check.stderr
    assert "7 fixtures matched" in check.stdout


def test_report_records_synthetic_and_reviewer_kind(tmp_path):
    report_path = tmp_path / "benchmark.json"
    subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "benchmark",
            "--provider", "fake", "--fake-reviewer",
            "--fixtures-root", str(tmp_path / "fixtures"),
            "--json-out", str(report_path),
        ],
        capture_output=True, text=True, check=True,
    )
    metadata = json.loads(report_path.read_text(encoding="utf-8"))["scorecards"][0]["provider_metadata"]

    # A synthetic run must never be mistakable for real-provider evidence.
    assert metadata["benchmark_kind"] == "synthetic"
    assert metadata["reviewer_kind"] == "fake"
