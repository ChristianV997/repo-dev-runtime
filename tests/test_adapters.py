import json

from repo_dev_runtime.contracts.models import DevTask, SensorRequest
from repo_dev_runtime.runtimes.ollama import OllamaRuntime
from repo_dev_runtime.runtimes.openai_compatible import OpenAICompatibleRuntime
from repo_dev_runtime.runtimes.openclaw import OpenClawRuntime
from repo_dev_runtime.runtimes.sidecars import DeerFlowRuntime, HermesRuntime
from repo_dev_runtime.sensors.agent_reach import AgentReachSensor


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
    class Completed:
        returncode = 0
        stdout = json.dumps({"records": [{"url": "https://example.com"}]})

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: Completed())
    sensor = AgentReachSensor(command=["bridge"], enabled=True)
    result = sensor.collect(SensorRequest.create(query="x", objective="y"))
    assert result.status == "succeeded"
    assert result.records[0]["url"] == "https://example.com"
