"""Idempotency-specific tests for repo_dev_runtime.scaffold.installer.install."""
from __future__ import annotations

from pathlib import Path

from repo_dev_runtime.scaffold.installer import install


def _make_target(tmp_path: Path) -> Path:
    target = tmp_path / "idempotent-repo"
    (target / ".git").mkdir(parents=True)
    return target


def test_second_install_is_a_no_op(tmp_path):
    target = _make_target(tmp_path)

    first = install(target, repo_name="idempotent-repo")
    assert first.created
    assert not first.unchanged

    second = install(target, repo_name="idempotent-repo")
    assert not second.created
    assert not second.overwritten
    assert not second.skipped_conflict
    assert second.unchanged == first.created


def test_reinstall_after_target_edit_prompts_instead_of_silently_overwriting(tmp_path):
    target = _make_target(tmp_path)
    install(target, repo_name="idempotent-repo")

    known_gaps = target / "docs" / "ai" / "KNOWN_GAPS.md"
    known_gaps.write_text("- a repo-specific gap we added by hand\n", encoding="utf-8")

    report = install(target, repo_name="idempotent-repo", confirm_overwrite=lambda _p: False)

    assert known_gaps.read_text(encoding="utf-8") == "- a repo-specific gap we added by hand\n"
    assert "docs/ai/KNOWN_GAPS.md" in report.skipped_conflict
    assert not report.ok


def test_reinstall_with_force_overwrites_non_protected_edited_file(tmp_path):
    target = _make_target(tmp_path)
    install(target, repo_name="idempotent-repo")

    known_gaps = target / "docs" / "ai" / "KNOWN_GAPS.md"
    known_gaps.write_text("- a repo-specific gap we added by hand\n", encoding="utf-8")

    report = install(target, repo_name="idempotent-repo", force=True)

    assert known_gaps.read_text(encoding="utf-8") != "- a repo-specific gap we added by hand\n"
    assert "docs/ai/KNOWN_GAPS.md" in report.overwritten
