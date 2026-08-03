"""Tests for repo_dev_runtime.eval.provider_specs."""
from __future__ import annotations

from repo_dev_runtime.eval.provider_specs import MINI_SWE_AGENT_SPEC, OPENHANDS_SPEC, default_provider_specs
from repo_dev_runtime.runtimes.factory import default_registry
from repo_dev_runtime.runtimes.registry import RoutingPolicy


def test_openhands_and_mini_swe_agent_specs_are_valid_and_blocked():
    for spec in (OPENHANDS_SPEC, MINI_SWE_AGENT_SPEC):
        spec.validate()
        assert spec.evaluation_status == "blocked"
        assert spec.blocked_reason.strip()
        assert spec.source_url.startswith("https://github.com/")


def test_default_provider_specs_returns_both():
    specs = default_provider_specs()
    assert {spec.provider for spec in specs} == {"openhands", "mini_swe_agent"}


def test_openhands_and_mini_swe_agent_excluded_from_default_registry():
    registry = default_registry()
    assert "openhands" not in registry._runtimes
    assert "mini_swe_agent" not in registry._runtimes


def test_openhands_and_mini_swe_agent_excluded_from_routing_policy():
    policy = RoutingPolicy()
    for preferred in policy.preferred_by_role.values():
        assert "openhands" not in preferred
        assert "mini_swe_agent" not in preferred
