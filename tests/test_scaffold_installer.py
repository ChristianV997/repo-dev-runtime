"""Tests for repo_dev_runtime.scaffold.installer.install."""
from __future__ import annotations

import re
import os
import pytest
from pathlib import Path

from repo_dev_runtime.scaffold.installer import DEFAULT_TEMPLATES_ROOT, install

EXPECTED_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/ai/README.md",
    "docs/ai/TOOL_ROUTING_POLICY.md",
    "docs/ai/SETUP.md",
    "docs/ai/CANONICAL_ARCHITECTURE.md",
    "docs/ai/CANONICAL_PATHS.md",
    "docs/ai/INTEGRATION_STATUS.md",
    "docs/ai/ACTIVE_MODULES.md",
    "docs/ai/SESSION_HANDOFF_TEMPLATE.md",
    "docs/ai/TOKEN_BUDGET_POLICY.md",
    "docs/ai/KNOWN_GAPS.md",
    "docs/ai/COMMANDS.md",
    "docs/ai/DEV_ENV_AUDIT.md",
    "docs/ai/OBSIDIAN_BRIDGE_STATUS.md",
    "docs/ai/OLLAMA_BENCHMARK.md",
    "scripts/ai/check_dev_stack.py",
    "scripts/ai/verify_local_integrations.py",
    "scripts/ai/generate_session_handoff.py",
    "scripts/ai/push_handoff_to_obsidian.py",
    "scripts/ai/run_semgrep_policy.py",
    "scripts/ai/filter_test_output.py",
    "scripts/ai/filter_semgrep_output.py",
    "scripts/ai/benchmark_ollama.py",
    ".serena/project.yml",
    ".serena/.gitignore",
    "semgrep/ai-safety.yml",
    "tests/test_ai_dev_stack.py",
    "tests/test_ollama_benchmark.py",
)

TOKEN_PATTERN = re.compile(r"\{\{[A-Z_]+\}\}")


def _make_target(tmp_path: Path, name: str = "my-target-repo") -> Path:
    target = tmp_path / name
    (target / ".git").mkdir(parents=True)
    return target


def test_install_into_empty_repo_creates_expected_files(tmp_path):
    target = _make_target(tmp_path)
    report = install(target)

    for rel in EXPECTED_FILES:
        assert (target / rel).exists(), f"missing {rel}"
    assert report.ok
    assert not report.skipped_conflict


def test_install_substitutes_repo_name(tmp_path):
    target = _make_target(tmp_path, name="acme-widgets")
    install(target, repo_name="acme-widgets")

    serena_config = (target / ".serena" / "project.yml").read_text(encoding="utf-8")
    assert 'project_name: "acme-widgets"' in serena_config

    for rel in EXPECTED_FILES:
        content = (target / rel).read_text(encoding="utf-8", errors="replace")
        assert not TOKEN_PATTERN.search(content), f"leftover token in {rel}: {TOKEN_PATTERN.search(content)}"


def test_install_defaults_repo_name_to_target_basename(tmp_path):
    target = _make_target(tmp_path, name="defaulted-name")
    install(target)
    serena_config = (target / ".serena" / "project.yml").read_text(encoding="utf-8")
    assert 'project_name: "defaulted-name"' in serena_config


def test_install_does_not_overwrite_existing_agents_md_without_confirmation(tmp_path):
    target = _make_target(tmp_path)
    target.mkdir(exist_ok=True)
    (target / "AGENTS.md").write_text("custom, repo-specific policy\n", encoding="utf-8")

    report = install(target, confirm_overwrite=lambda _path: False)

    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "custom, repo-specific policy\n"
    assert "AGENTS.md" in report.skipped_conflict
    assert not report.ok


def test_install_force_still_protects_agents_md_without_force_agents_md(tmp_path):
    target = _make_target(tmp_path)
    (target / "AGENTS.md").write_text("custom policy\n", encoding="utf-8")

    report = install(target, force=True)

    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "custom policy\n"
    assert "AGENTS.md" in report.skipped_conflict


def test_install_force_agents_md_overwrites_when_explicitly_requested(tmp_path):
    target = _make_target(tmp_path)
    (target / "AGENTS.md").write_text("custom policy\n", encoding="utf-8")

    report = install(target, force=True, force_agents_md=True)

    assert (target / "AGENTS.md").read_text(encoding="utf-8") != "custom policy\n"
    assert "AGENTS.md" in report.overwritten


def test_install_dry_run_writes_nothing(tmp_path):
    target = _make_target(tmp_path)
    report = install(target, dry_run=True)

    assert not any(target.rglob("*.md"))
    assert not any(target.rglob("*.py"))
    assert len(report.created) == len(EXPECTED_FILES)


def test_semgrep_template_has_no_marketos_paths():
    content = (DEFAULT_TEMPLATES_ROOT / "semgrep" / "ai-safety.yml").read_text(encoding="utf-8")
    active_lines = "\n".join(line for line in content.splitlines() if not line.strip().startswith("#"))
    # backend/connectors/orchestrator may still appear inside an explanatory
    # comment showing how to opt into path-scoping; they must never appear
    # in an active (uncommented) YAML key.
    for literal in ("backend/", "connectors/", "orchestrator/", "marketos-"):
        assert literal not in active_lines, f"found leftover MarketOS-specific literal in active config: {literal}"
    assert "devstack-" in content


def test_install_refuses_destination_symlink(tmp_path):
    target = _make_target(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = target / "docs"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink|escapes"):
        install(target)
    assert not list(outside.rglob("*"))
