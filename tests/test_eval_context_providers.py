"""Tests for repo_dev_runtime.eval.context_providers."""
from __future__ import annotations

from repo_dev_runtime.context import build_adaptive_context
from repo_dev_runtime.eval.conformance import assert_context_provider_contract
from repo_dev_runtime.eval.context_providers import StaticMapContextProvider, resolve_context_provider
from repo_dev_runtime.eval.fakes import FakeRepoAgentContextProvider, FakeTreeSitterContextProvider


def test_static_map_provider_matches_build_adaptive_context_shape(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    provider = StaticMapContextProvider()

    text, map_text = provider.build(tmp_path, objective="find f", allowed_paths=(), forbidden_paths=(), max_bytes=8_192)
    expected_text, expected_map = build_adaptive_context(tmp_path, objective="find f", allowed_paths=(), forbidden_paths=(), max_bytes=8_192)

    assert text == expected_text
    assert map_text == expected_map


def test_static_map_provider_capability_metadata_is_honest():
    provider = StaticMapContextProvider()
    caps = provider.capabilities()
    assert caps["vendored"] is False
    assert caps["dependency_free"] is True


def test_resolve_context_provider_uses_preferred_when_it_succeeds(tmp_path):
    fake = FakeRepoAgentContextProvider(should_fail=False)
    text, map_text, used = resolve_context_provider(fake, root=tmp_path, objective="obj", allowed_paths=(), forbidden_paths=(), max_bytes=8_192)

    assert used == "fake_repo_agent"
    assert "fake_repo_agent" in map_text


def test_resolve_context_provider_falls_back_to_static_map_on_failure(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    fake = FakeTreeSitterContextProvider(should_fail=True)

    text, map_text, used = resolve_context_provider(fake, root=tmp_path, objective="obj", allowed_paths=(), forbidden_paths=(), max_bytes=8_192)

    assert used == "static_map"


def test_all_context_providers_satisfy_the_shared_contract(tmp_path):
    # Delegates the shape/metadata/budget rules to the shared conformance
    # kit, so a real Tree-sitter or RepoAgent provider reuses the same checks.
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    for provider in (StaticMapContextProvider(), FakeRepoAgentContextProvider(), FakeTreeSitterContextProvider()):
        assert_context_provider_contract(provider, root=tmp_path, label=provider.name)


def test_fake_provider_capability_metadata_declares_no_vendoring():
    repo_agent = FakeRepoAgentContextProvider()
    tree_sitter = FakeTreeSitterContextProvider()

    assert repo_agent.capabilities()["vendored"] is False
    assert tree_sitter.capabilities()["vendored"] is False
    assert repo_agent.capabilities()["source_url"].startswith("https://github.com/OpenBMB/RepoAgent")
    assert tree_sitter.capabilities()["source_url"].startswith("https://github.com/tree-sitter/tree-sitter")
