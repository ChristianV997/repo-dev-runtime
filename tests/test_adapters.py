import json
import sys
import time
from urllib.error import URLError

from repo_dev_runtime.contracts.models import DevTask, SensorRequest
from repo_dev_runtime.runtimes.ollama import OllamaRuntime
from repo_dev_runtime.runtimes.aider import AiderRuntime
from repo_dev_runtime.runtimes.openai_compatible import OpenAICompatibleRuntime
from repo_dev_runtime.runtimes.openclaw import OpenClawRuntime
from repo_dev_runtime.runtimes.sidecars import DeerFlowRuntime, HermesRuntime
from repo_dev_runtime.sensors.agent_reach import AgentReachSensor
from repo_dev_runtime.tools.runner import CommandResult
from tests.support.processes import pid_is_running


def task():
    return DevTask(task_id="t1", repository="repo", base_ref="HEAD", role="planner", prompt="inspect")


def test_disabled_runtimes_fail_closed():
    assert OllamaRuntime(enabled=False).execute(task()).status == "skipped"
    assert OpenAICompatibleRuntime(enabled=False).execute(task()).status == "skipped"
    assert OpenClawRuntime(enabled=False).execute(task()).status == "skipped"
    assert HermesRuntime(enabled=False).execute(task()).status == "skipped"
    assert DeerFlowRuntime(enabled=False).execute(task()).status == "skipped"


def test_openclaw_is_reachable_through_the_default_registry_and_health_output():
    """Regression test: README.md advertises OpenClaw as an optional
    sidecar alongside Hermes/DeerFlow, but default_registry() never
    imported or registered OpenClawRuntime and cli.py had no
    --enable-openclaw flag - health/run --live could never even see it.
    OpenClaw still defaults to disabled and fails closed either way; this
    only proves it is now reachable from the entry points a user actually
    uses, not that it is enabled by default."""
    from repo_dev_runtime.runtimes.factory import default_registry

    registry = default_registry()
    assert "openclaw" in registry._runtimes
    assert isinstance(registry.get("openclaw"), OpenClawRuntime)

    health = registry.health()
    assert "openclaw" in health
    assert health["openclaw"].configured is False
    assert health["openclaw"].reachable is False


def test_aider_is_registered_but_disabled_by_default():
    """Aider can be selected only by explicit policy and CLI opt-in."""
    from repo_dev_runtime.runtimes.factory import default_registry

    registry = default_registry()
    assert isinstance(registry.get("aider"), AiderRuntime)
    health = registry.health()["aider"]
    assert health.configured is False
    assert health.reachable is False


def test_hermes_normalizes_openai_response():
    def fake_request(*args):
        return {"choices": [{"message": {"content": "plan"}}]}

    result = HermesRuntime(enabled=True, request=fake_request).execute(task())
    assert result.status == "succeeded"
    assert result.output == "plan"


def test_http_and_bridge_adapter_errors_redact_credential_shaped_text(monkeypatch):
    def raise_error(*args, **kwargs):
        raise URLError("api_key=adapter-secret")

    monkeypatch.setattr("repo_dev_runtime.runtimes.ollama.urlopen", raise_error)
    ollama_result = OllamaRuntime(enabled=True).execute(task())
    assert "adapter-secret" not in ollama_result.error_message
    assert "[REDACTED]" in ollama_result.error_message

    monkeypatch.setattr("repo_dev_runtime.runtimes.openai_compatible.urlopen", raise_error)
    openai_result = OpenAICompatibleRuntime(enabled=True).execute(task())
    assert "adapter-secret" not in openai_result.error_message
    assert "[REDACTED]" in openai_result.error_message

    hermes_result = HermesRuntime(enabled=True, request=raise_error).execute(task())
    assert "adapter-secret" not in hermes_result.error_message
    assert "[REDACTED]" in hermes_result.error_message

    monkeypatch.setattr("repo_dev_runtime.sensors.agent_reach.run_command", raise_error)
    sensor_result = AgentReachSensor(command=["bridge"], enabled=True).collect(SensorRequest.create(query="x", objective="y"))
    assert "adapter-secret" not in sensor_result.error_message
    assert "[REDACTED]" in sensor_result.error_message


def test_agent_reach_disabled_without_subprocess():
    sensor = AgentReachSensor(command=["definitely-missing"], enabled=False)
    result = sensor.collect(SensorRequest.create(query="x", objective="y"))
    assert result.status == "skipped"


def test_agent_reach_json_bridge(monkeypatch):
    monkeypatch.setattr(
        "repo_dev_runtime.sensors.agent_reach.run_command",
        lambda *args, **kwargs: CommandResult(
            command=("bridge",),
            returncode=0,
            stdout=json.dumps({"records": [{"url": "https://example.com"}]}),
            stderr="",
        ),
    )
    sensor = AgentReachSensor(command=["bridge"], enabled=True)
    result = sensor.collect(SensorRequest.create(query="x", objective="y"))
    assert result.status == "succeeded"
    assert result.records[0]["url"] == "https://example.com"


def test_agent_reach_timeout_terminates_bridge_children(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    sensor = AgentReachSensor(command=[sys.executable, str(bridge)], enabled=True)

    result = sensor.collect(SensorRequest.create(query="x", objective="y", timeout_s=0.2))

    assert result.status == "failed"
    assert result.error_type == "timeout"
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not child_pid_file.exists():
        time.sleep(0.02)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert not pid_is_running(child_pid), "timed-out Agent-Reach bridge left a child process running"
