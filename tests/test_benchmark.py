from repo_dev_runtime.benchmark import benchmark_runtimes
from repo_dev_runtime.contracts.models import DevResult, DevTask


class Runtime:
    def execute(self, task):
        return DevResult(task.task_id, "fake", "succeeded", output="ok")


def test_benchmark_does_not_persist():
    task = DevTask(task_id="t1", repository="repo", base_ref="HEAD", role="planner", prompt="inspect")
    report = benchmark_runtimes(task, {"fake": Runtime()})
    assert report["persisted"] is False
    assert report["results"]["fake"]["status"] == "succeeded"
