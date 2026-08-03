import json
import subprocess
import sys


def test_cli_dry_run_is_provider_independent(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "run", str(tmp_path), "--prompt", "inspect", "--artifacts-root", str(tmp_path / "artifacts")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "ready_for_human_review"


def test_cli_benchmark_defaults_to_fake_provider(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--fixtures-root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["schema"] == "RepoDev.BenchmarkReport.v1"
    assert report["scorecards"][0]["provider"] == "fake_coding_provider"
    assert len(report["fixture_results"]) == 7


def test_cli_benchmark_real_provider_requires_live(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--provider", "ollama", "--fixtures-root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "real_provider_requires_live"

