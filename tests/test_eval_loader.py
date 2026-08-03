"""Tests for repo_dev_runtime.eval.loader — the --provider-module hook that
lets an externally-defined provider be benchmarked through the existing
harness without editing the CLI's provider enum."""
from __future__ import annotations

import pytest

from repo_dev_runtime.eval.fixtures import FIXTURE_CASES
from repo_dev_runtime.eval.harness import aggregate_scorecard, run_fixture_case
from repo_dev_runtime.eval.loader import (
    ProviderLoadError,
    implements_development_runtime,
    load_provider,
    parse_target,
)

_MODULE = "tests.sample_external_provider"


def test_parse_target_accepts_module_colon_class():
    assert parse_target("a.b.c:Klass") == ("a.b.c", "Klass")


@pytest.mark.parametrize("bad", ["no_colon", "too:many:colons", ":Klass", "module:", "", 5])
def test_parse_target_rejects_malformed_targets(bad):
    with pytest.raises(ProviderLoadError):
        parse_target(bad)


def test_load_provider_imports_and_constructs():
    provider = load_provider(f"{_MODULE}:SampleExternalProvider")

    assert provider.name == "sample_external_provider"
    assert implements_development_runtime(provider)
    assert provider.health().configured is True


def test_load_provider_prefers_create_classmethod():
    provider = load_provider(f"{_MODULE}:ProviderWithCreateClassmethod")

    assert provider.marker == "constructed-via-create"
    assert provider.name == "provider_with_create"


def test_load_provider_rejects_non_conforming_class():
    with pytest.raises(ProviderLoadError, match="does not implement the DevelopmentRuntime protocol"):
        load_provider(f"{_MODULE}:NotARuntime")


def test_load_provider_rejects_class_that_cannot_be_constructed():
    with pytest.raises(ProviderLoadError, match="cannot construct provider"):
        load_provider(f"{_MODULE}:RequiresArguments")


def test_load_provider_rejects_missing_module():
    with pytest.raises(ProviderLoadError, match="cannot import provider module"):
        load_provider("repo_dev_runtime.eval.does_not_exist:Whatever")


def test_load_provider_rejects_missing_attribute():
    with pytest.raises(ProviderLoadError, match="has no attribute"):
        load_provider(f"{_MODULE}:NoSuchClass")


def test_externally_loaded_provider_runs_through_the_existing_harness(tmp_path):
    """The whole point of the hook: a provider defined outside the eval
    package produces a normal FixtureCaseResult and ProviderScorecard."""
    provider = load_provider(f"{_MODULE}:SampleExternalProvider")
    case = next(c for c in FIXTURE_CASES if c.fixture_id == "one_file_bugfix")

    result = run_fixture_case(
        case, make_provider=lambda c: provider, provider_name=provider.name, tmp_root=tmp_path, max_fix_attempts=1
    )

    assert result.outcome == "succeeded"
    assert result.provider == "sample_external_provider"
    assert result.changed_files == ("calc.py",)

    scorecard = aggregate_scorecard(provider.name, [result])
    assert scorecard.provider == "sample_external_provider"
    assert scorecard.tasks_completed == 1


def test_loading_a_provider_does_not_register_it_in_default_routing():
    from repo_dev_runtime.runtimes.factory import default_registry
    from repo_dev_runtime.runtimes.registry import RoutingPolicy

    provider = load_provider(f"{_MODULE}:SampleExternalProvider")

    assert provider.name not in default_registry()._runtimes
    for preferred in RoutingPolicy().preferred_by_role.values():
        assert provider.name not in preferred
