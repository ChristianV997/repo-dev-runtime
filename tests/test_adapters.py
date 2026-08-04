import json
import os
import sys
import time

from repo_dev_runtime.contracts.models import DevTask, SensorRequest
from repo_dev_runtime.runtimes.ollama import OllamaRuntime
from repo_dev_runtime.runtimes.openai_compatible import OpenAICompatibleRuntime
from repo_dev_runtime.runtimes.openclaw import OpenClawRuntime
from repo_dev_runtime.runtimes.sidecars import DeerFlowRuntime, HermesRuntime
from repo_dev_runtime.sensors.agent_reach import AgentReachSensor
from repo_dev_runtime.tools.runner import CommandResult


def task():
    return DevTask(task_id="t1", repository="repo", base_ref="HEAD", role="planner", prompt="inspect")


def test_disabled_runtimes_fail_closed():
    assert OllamaRuntime(enabled=False).execute(task()).status == "skipped"
    assert OpenAICompatibleRuntime(enabled=False).execute(task()).status == "skipped"
    assert OpenClawRuntime(enabled=False).execute(task()).status == "skipped"
    assert HermesRuntime(enabled=False).execute(task()).status == "skipped"
    assert DeerFlowRuntime(enabled=False).execute(task()).status == "skipped"


def test_hermes_normalizes_openai_response():
    def fake_request(*args):
        return {"choices": [{"message": {"content": "plan"}}]}

    result = HermesRuntime(enabled=True, request=fake_request).execute(task())
    assert result.status == "succeeded"
    assert result.output == "plan"


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
    try:
        os.kill(int(child_pid_file.read_text(encoding="utf-8")), 0)
    except OSError:
        return
    raise AssertionError("timed-out Agent-Reach bridge left a child process running")
