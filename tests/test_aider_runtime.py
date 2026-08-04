"""Contract tests for the sandboxed Aider adapter.

The tests use a tiny local Python program in place of Aider itself. This
proves the adapter's sandbox/diff/proposal boundary without requiring Aider,
credentials, or a model in CI.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from repo_dev_runtime.contracts.models import DevTask
from repo_dev_runtime.edits import PatchApplier, parse_edit_proposal
from repo_dev_runtime.eval.conformance import assert_disabled_runtime_contract, assert_no_forbidden_capabilities
from repo_dev_runtime.runtimes.aider import AiderRuntime, _aider_objective, _snapshot


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _git(root, "add", "calc.py")
    _git(root, "commit", "-qm", "baseline")
    return root, _git(root, "rev-parse", "HEAD")


def _task(root: Path, head: str, *, allowed_paths: tuple[str, ...] = (".",), timeout_s: float = 30.0) -> DevTask:
    task_id = "a" * 32
    context_hash = "b" * 64
    return DevTask(
        task_id=task_id,
        repository=str(root),
        base_ref="HEAD",
        role="implementer",
        allowed_paths=allowed_paths,
        timeout_s=timeout_s,
        prompt=f"task_id={task_id} base_commit={head} context_hash={context_hash}\nFix calc.py.",
        dry_run=False,
    )


def _writer(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "aider_stub.py"
    script.write_text(body, encoding="utf-8")
    return script


def test_disabled_aider_never_starts_a_subprocess(tmp_path):
    root, head = _repository(tmp_path)
    result = AiderRuntime(command=["missing-aider"], model="test", enabled=False).execute(_task(root, head))

    assert result.status == "skipped"
    assert result.error_type == "runtime_disabled"


def test_disabled_aider_satisfies_the_shared_runtime_and_capability_boundaries(tmp_path):
    root, _ = _repository(tmp_path)
    assert_disabled_runtime_contract(
        lambda: AiderRuntime(command=["missing-aider"], model="test", enabled=False),
        repository=root,
        label="aider",
    )
    assert_no_forbidden_capabilities(AiderRuntime(command=["missing-aider"], model="test", enabled=False), label="aider")


def test_aider_edits_only_sandbox_then_returns_a_valid_proposal(tmp_path):
    root, head = _repository(tmp_path)
    script = _writer(
        tmp_path,
        "from pathlib import Path\nPath('calc.py').write_text('def add(a, b):\\n    return a + b\\n', encoding='utf-8')\n",
    )
    task = _task(root, head)
    runtime = AiderRuntime(command=[sys.executable, str(script)], model="test", enabled=True)

    result = runtime.execute(task)

    assert result.status == "succeeded"
    assert (root / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    proposal = parse_edit_proposal(result.output)
    applied = PatchApplier(root, allowed_paths=(".",)).apply(proposal, context_hash="b" * 64)
    assert applied.changed_files == ("calc.py",)
    assert (root / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_aider_forwards_openrouter_credentials_to_the_subprocess(tmp_path, monkeypatch):
    """Aider natively supports OpenRouter-backed models via
    OPENROUTER_API_KEY; the CredentialAllowlist must forward it into the
    sandboxed subprocess like the existing OLLAMA_/OPENAI_/ANTHROPIC_
    prefixes, or a user configuring Aider against OpenRouter would see it
    silently fail to authenticate."""
    root, head = _repository(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-secret")
    script = _writer(
        tmp_path,
        "import os\nfrom pathlib import Path\n"
        "Path('calc.py').write_text('KEY=' + os.environ.get('OPENROUTER_API_KEY', 'MISSING') + '\\n', encoding='utf-8')\n",
    )
    task = _task(root, head)
    runtime = AiderRuntime(command=[sys.executable, str(script)], model="openrouter/test", enabled=True)

    result = runtime.execute(task)

    assert result.status == "succeeded"
    proposal = parse_edit_proposal(result.output)
    assert proposal.edits[0].content == "KEY=sk-or-test-secret\n"


def test_aider_requires_a_bound_edit_contract_and_explicit_scope(tmp_path):
    root, head = _repository(tmp_path)
    task = _task(root, head, allowed_paths=())
    runtime = AiderRuntime(command=[sys.executable], model="test", enabled=True)

    result = runtime.execute(task)

    assert result.status == "blocked"
    assert result.error_type == "allowed_paths_required"


def test_aider_ignores_hidden_artifacts_created_in_its_sandbox(tmp_path):
    root, head = _repository(tmp_path)
    script = _writer(
        tmp_path,
        "from pathlib import Path\nPath('.aider.chat.history.md').write_text('ephemeral')\n",
    )
    result = AiderRuntime(command=[sys.executable, str(script)], model="test", enabled=True).execute(_task(root, head))

    assert result.status == "failed"
    assert result.error_type == "no_edits"
    assert not (root / ".aider.chat.history.md").exists()


def test_aider_snapshot_prunes_generated_trees_and_bounds_file_reads(tmp_path):
    root, _ = _repository(tmp_path)
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "generated.py").write_text("generated", encoding="utf-8")
    (root / "dist").mkdir()
    (root / "dist" / "bundle.py").write_text("bundle", encoding="utf-8")
    (root / "large.py").write_bytes(b"x" * 1_000_001)

    snapshot = _snapshot(root, (".",), max_files=100, max_bytes=2_000_000)

    assert "calc.py" in snapshot
    assert "node_modules/pkg/generated.py" not in snapshot
    assert "dist/bundle.py" not in snapshot
    assert "large.py" not in snapshot


def test_aider_snapshot_uses_case_insensitive_scope(tmp_path):
    root, _ = _repository(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    snapshot = _snapshot(root, ("SRC",), max_files=100, max_bytes=2_000_000)
    assert "src/app.py" in snapshot
    assert "calc.py" not in snapshot


def test_aider_timeout_terminates_the_provider_process_tree(tmp_path):
    root, head = _repository(tmp_path)
    script = _writer(
        tmp_path,
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(30)\n",
    )
    result = AiderRuntime(command=[sys.executable, str(script)], model="test", enabled=True).execute(
        _task(root, head, timeout_s=0.2)
    )

    assert result.status == "failed"
    assert result.error_type == "timeout"


def test_aider_translates_only_json_safe_proposal_content(tmp_path):
    root, head = _repository(tmp_path)
    script = _writer(
        tmp_path,
        "from pathlib import Path\nPath('calc.py').write_text('def add(a, b):\\n    return a + b\\n', encoding='utf-8')\n",
    )
    result = AiderRuntime(command=[sys.executable, str(script)], model="test", enabled=True).execute(_task(root, head))

    payload = json.loads(result.output)
    assert payload["schema"] == "RepoDev.EditProposal.v1"
    assert payload["edits"][0]["expected_file_hash"]


def test_aider_receives_only_the_governed_task_objective():
    prompt = (
        "Role: implementer\nReturn only JSON.\n\n"
        "Objective:\nFix calc.py.\n\nRepository context:\ncalc.py\n"
    )

    assert _aider_objective(prompt) == "Fix calc.py."
