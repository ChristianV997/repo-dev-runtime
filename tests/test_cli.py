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


def test_cli_provider_module_live_requires_explicit_approval(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "benchmark",
            "--provider-module", "tests.sample_external_provider:SampleExternalProvider",
            "--live", "--fixtures-root", str(tmp_path),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "explicit approval" in json.loads(result.stdout)["reason"]


def test_cli_provider_module_live_with_approval_runs(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "repo_dev_runtime.cli", "benchmark",
            "--provider-module", "tests.sample_external_provider:SampleExternalProvider",
            "--live", "--approve-external-provider-benchmark", "--fixtures-root", str(tmp_path),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["scorecards"][0]["provider"] == "sample_external_provider"


def test_cli_enable_pr_agent_requires_live_and_approval(tmp_path):
    without_live = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--enable-pr-agent", "--fixtures-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert without_live.returncode == 1
    assert json.loads(without_live.stdout)["reason"] == "real_provider_requires_live"

    without_approval = subprocess.run(
        [sys.executable, "-m", "repo_dev_runtime.cli", "benchmark", "--enable-pr-agent", "--live", "--fixtures-root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert without_approval.returncode == 1
    assert "explicit approval" in json.loads(without_approval.stdout)["reason"]

