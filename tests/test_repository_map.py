from pathlib import Path

import pytest

from repo_dev_runtime.context import build_adaptive_context
from repo_dev_runtime.repository_map import build_repository_map, rank_entries


def test_map_and_adaptive_context_are_bounded_and_ranked(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "payments.py").write_text("def calculate_invoice():\n    return 1\n")
    (tmp_path / "src" / "other.py").write_text("def unrelated():\n    return 1\n")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "key.py").write_text("never include")
    entries = build_repository_map(tmp_path, allowed_paths=("src",), forbidden_paths=("secrets",))
    assert [entry.path for entry in entries] == ["src/other.py", "src/payments.py"]
    assert rank_entries(entries, "fix payments invoice")[0].path == "src/payments.py"
    context, map_text = build_adaptive_context(tmp_path, objective="fix payments invoice", allowed_paths=("src",), forbidden_paths=("secrets",), max_bytes=4096)
    assert "calculate_invoice" in context
    assert "never include" not in context
    assert "Repository map" in map_text


def test_context_and_map_apply_case_insensitive_forbidden_paths_and_prune_generated_trees(tmp_path: Path) -> None:
    (tmp_path / "Secrets").mkdir()
    (tmp_path / "Secrets" / "Leak.py").write_text("bare-secret", encoding="utf-8")
    (tmp_path / "node_modules" / "package").mkdir(parents=True)
    (tmp_path / "node_modules" / "package" / "generated.py").write_text("generated", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "safe.py").write_text("safe", encoding="utf-8")

    entries = build_repository_map(tmp_path, allowed_paths=(".",), forbidden_paths=("secrets",))
    assert [entry.path for entry in entries] == ["src/safe.py"]
    context = build_adaptive_context(
        tmp_path,
        objective="safe",
        allowed_paths=(".",),
        forbidden_paths=("secrets",),
        max_bytes=4_096,
    )[0]
    assert "bare-secret" not in context
    assert "generated" not in context


def test_context_and_map_skip_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "large.py").write_bytes(b"x" * 1_000_001)
    (tmp_path / "small.py").write_text("small", encoding="utf-8")

    entries = build_repository_map(tmp_path, allowed_paths=(".",), forbidden_paths=())
    assert [entry.path for entry in entries] == ["small.py"]
    context = build_adaptive_context(
        tmp_path,
        objective="small",
        allowed_paths=(".",),
        forbidden_paths=(),
        max_bytes=4_096,
    )[0]
    assert "small" in context
    assert "large.py" not in context


def test_context_and_map_skip_file_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "outside.py"
    target.write_text("outside-secret", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this environment")

    entries = build_repository_map(tmp_path, allowed_paths=(".",), forbidden_paths=())
    assert [entry.path for entry in entries] == ["outside.py"]
    context = build_adaptive_context(
        tmp_path,
        objective="outside",
        allowed_paths=(".",),
        forbidden_paths=(),
        max_bytes=4_096,
    )[0]
    assert "link.py" not in context
