from pathlib import Path

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
