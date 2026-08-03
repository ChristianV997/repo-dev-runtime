import json

import pytest

from repo_dev_runtime.contracts.models import DevResult, DevTask, RuntimeHealth
from repo_dev_runtime.governance.policy import RuntimePolicy
from repo_dev_runtime.integrations.github import GitHubPublisher
from repo_dev_runtime.manifest import RepoManifest
from repo_dev_runtime.runtimes.registry import RuntimeRegistry, RuntimeRouter
from repo_dev_runtime.workflow import DevelopmentWorkflow
from repo_dev_runtime.context import build_repository_context


class FakeRuntime:
    name = "ollama"

    def health(self):
        return RuntimeHealth("ollama", True, True, ("test",))

    def execute(self, task):
        return DevResult(task.task_id, self.name, "succeeded", output=f"{task.role}:ok")


def test_router_prefers_available_local_runtime():
    policy = RuntimePolicy(allow_ollama=True)
    router = RuntimeRouter(RuntimeRegistry({"ollama": FakeRuntime()}), policy=policy)
    task = DevTask(task_id="t", repository="r", base_ref="HEAD", role="planner", prompt="x")
    assert router.route(task) == "ollama"
    assert router.execute(task).status == "succeeded"


def test_router_requires_paid_approval():
    policy = RuntimePolicy(allow_omniroute=True, allow_paid_routing=True)
    router = RuntimeRouter(RuntimeRegistry({"openai_compatible": FakeRuntime()}), policy=policy)
    task = DevTask(task_id="t", repository="r", base_ref="HEAD", role="planner", prompt="x")
    with pytest.raises(PermissionError):
        router.route(task)
    assert router.route(task, approved=True) == "openai_compatible"


def test_workflow_resume_skips_completed_roles(tmp_path):
    manifest = RepoManifest(name="fixture", root=str(tmp_path), allowed_paths=(".",))
    run_root = tmp_path.parent / "run-artifacts"
    first = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=run_root).run(prompt="inspect")
    second = DevelopmentWorkflow(manifest=manifest, policy=RuntimePolicy(), runtime=FakeRuntime(), artifacts_root=run_root).run(prompt="inspect", run_id=first.run_id, resume=True)
    assert second.status == "ready_for_human_review"
    assert len(second.results) == 5
    events = (run_root / first.run_id / "events.jsonl").read_text(encoding="utf-8")
    assert "task_resumed" in events


def test_publisher_rejects_non_generated_branch(tmp_path):
    publisher = GitHubPublisher(policy=RuntimePolicy(allow_branch_publish=True))
    with pytest.raises(PermissionError):
        publisher.publish_branch(repository=tmp_path, branch="feature/manual")


def test_pr_creation_requires_branch_publish_permission():
    with pytest.raises(ValueError):
        RuntimePolicy(allow_pr_creation=True).validate()


def test_github_pr_request_is_bounded_and_secret_free(monkeypatch):
    captured = {}

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return json.dumps({"html_url": "https://github.com/example/repo/pull/1", "number": 1}).encode()

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("repo_dev_runtime.integrations.github.urlopen", fake_urlopen)
    publisher = GitHubPublisher(policy=RuntimePolicy(allow_pr_creation=True, allow_branch_publish=True), token="secret")
    monkeypatch.setattr(publisher, "_owner_repo", lambda _root: ("example", "repo"))
    result = publisher.create_pull_request(repository=".", branch="repo-dev/run", base="main", title="test", body="safe")
    assert result["number"] == 1
    assert captured["body"]["head"] == "repo-dev/run"
    assert "secret" not in json.dumps(captured["body"])


def test_repository_context_is_bounded_and_excludes_forbidden_paths(tmp_path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "token.txt").write_text("secret", encoding="utf-8")
    context = build_repository_context(tmp_path, allowed_paths=(".",), forbidden_paths=("secrets",), max_bytes=2_000)
    assert "hello" in context
    assert "secret" not in context


def test_manifest_context_bound_is_validated():
    with pytest.raises(ValueError):
        RepoManifest(name="x", root=".", context_max_bytes=100).validate()
