import json

from repo_dev_runtime.contracts.models import DevTask, RuntimeHealth
from repo_dev_runtime.eval.models import EvalResult
from repo_dev_runtime.runtimes.pr_agent_reviewer import PRAgentReviewerRuntime


class FakeReviewerAdapter:
    def __init__(self):
        self.request = None

    def health(self):
        return RuntimeHealth("pr_agent", True, True, ("reviewer_only",))

    def review(self, request):
        self.request = request
        return EvalResult(
            request_id=request.request_id,
            provider="pr_agent",
            status="succeeded",
            normalized={"schema": "RepoDev.ReviewVerdict.v1", "approved": True, "summary": "safe", "findings": []},
        )


def test_pr_agent_workflow_runtime_is_reviewer_only():
    runtime = PRAgentReviewerRuntime(FakeReviewerAdapter())
    task = DevTask.create(repository=".", base_ref="HEAD", role="planner", prompt="plan")

    result = runtime.execute(task)

    assert result.status == "blocked"
    assert result.error_type == "reviewer_only"


def test_pr_agent_workflow_runtime_passes_only_final_diff_and_evidence():
    adapter = FakeReviewerAdapter()
    runtime = PRAgentReviewerRuntime(adapter)
    task = DevTask.create(
        repository=".",
        base_ref="HEAD",
        role="reviewer",
        dry_run=False,
        prompt="Role: reviewer\nQuality: passed\n\nFinal diff:\n--- a/src/app.py\n+++ b/src/app.py\n+value = 2\n",
    )

    result = runtime.execute(task)

    assert result.status == "succeeded"
    assert json.loads(result.output)["approved"] is True
    assert adapter.request is not None
    assert "Quality: passed" in adapter.request.objective
    assert "value = 2" in adapter.request.diff
