from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_dev_runtime.edits import EditProposal, FileEdit, PatchApplier, PatchValidationError


def git_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return root, head


def proposal(root: Path, head: str, edit: FileEdit, *, context: str = "ctx") -> EditProposal:
    return EditProposal.from_dict({
        "proposal_id": "p1", "task_id": "t1", "base_commit": head,
        "context_hash": context, "summary": "update app", "edits": [edit.to_dict()],
    })


def test_search_replace_and_rollback(tmp_path: Path) -> None:
    root, head = git_repo(tmp_path)
    target = root / "src" / "app.py"
    before = target.read_bytes()
    edit = FileEdit("src/app.py", "search_replace", search="value = 1", replace="value = 2")
    applier = PatchApplier(root, allowed_paths=("src",), forbidden_paths=(".git", "secrets"))
    result = applier.apply(proposal(root, head, edit), context_hash="ctx")
    assert result.changed_files == ("src/app.py",)
    assert target.read_text() == "value = 2\n"
    applier.rollback_checkpoint(result.checkpoint_id)
    assert target.read_bytes() == before


def test_new_whole_file_and_hash_guard(tmp_path: Path) -> None:
    root, head = git_repo(tmp_path)
    edit = FileEdit("src/new.py", "whole_file", content="print('ok')\n")
    PatchApplier(root, allowed_paths=("src",)).apply(proposal(root, head, edit))
    assert (root / "src/new.py").read_text() == "print('ok')\n"
    stale = FileEdit("src/app.py", "search_replace", search="value = 1", replace="value = 3", expected_file_hash="0" * 64)
    with pytest.raises(PatchValidationError, match="file hash"):
        PatchApplier(root, allowed_paths=("src",)).apply(proposal(root, head, stale))


def test_repository_root_allowed_path_permits_in_worktree_edit(tmp_path: Path) -> None:
    root, head = git_repo(tmp_path)
    edit = FileEdit("src/app.py", "search_replace", search="value = 1", replace="value = 2")

    PatchApplier(root, allowed_paths=(".",)).apply(proposal(root, head, edit))

    assert (root / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"


def test_path_policy_is_case_insensitive_for_allowed_and_forbidden_paths(tmp_path: Path) -> None:
    root, head = git_repo(tmp_path)
    edit = FileEdit("src/app.py", "search_replace", search="value = 1", replace="value = 2")
    PatchApplier(root, allowed_paths=("SRC",), forbidden_paths=("SECRETS",)).apply(proposal(root, head, edit))

    secret_edit = FileEdit("src/secrets/token.py", "whole_file", content="token = 'blocked'\n")
    with pytest.raises(PatchValidationError, match="forbidden path"):
        PatchApplier(root, allowed_paths=(".",), forbidden_paths=("secrets",)).apply(proposal(root, head, secret_edit))


@pytest.mark.parametrize("edit", [
    FileEdit("../escape.py", "whole_file", content="x"),
    FileEdit("/absolute.py", "whole_file", content="x"),
    FileEdit("src/app.py", "search_replace", search="missing", replace="x"),
])
def test_invalid_edits_fail_closed(tmp_path: Path, edit: FileEdit) -> None:
    root, head = git_repo(tmp_path)
    with pytest.raises(PatchValidationError):
        PatchApplier(root, allowed_paths=("src",)).apply(proposal(root, head, edit))


def test_duplicate_and_unknown_proposals_rejected() -> None:
    edit = {"path": "a.py", "format": "whole_file", "content": "x"}
    with pytest.raises(PatchValidationError, match="duplicate"):
        EditProposal.from_dict({"proposal_id": "p", "task_id": "t", "base_commit": "h", "context_hash": "c",
                                "summary": "s", "edits": [edit, edit]})
    with pytest.raises(PatchValidationError, match="invalid proposal"):
        EditProposal.from_dict({"proposal_id": "p", "task_id": "t", "base_commit": "h", "context_hash": "c",
                                "summary": "s", "edits": [edit], "unexpected": True})


def test_context_and_base_are_bound(tmp_path: Path) -> None:
    root, head = git_repo(tmp_path)
    edit = FileEdit("src/app.py", "search_replace", search="value = 1", replace="value = 2")
    applier = PatchApplier(root, allowed_paths=("src",))
    with pytest.raises(PatchValidationError, match="context"):
        applier.apply(proposal(root, head, edit, context="expected"), context_hash="other")
    with pytest.raises(PatchValidationError, match="base"):
        applier.apply(EditProposal.from_dict({"proposal_id": "p", "task_id": "t", "base_commit": "bad",
            "context_hash": "ctx", "summary": "s", "edits": [edit.to_dict()]}))
