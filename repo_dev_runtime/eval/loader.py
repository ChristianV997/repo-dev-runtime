"""Dynamic provider loading for the fixture benchmark.

Lets a provider implemented anywhere — including one landed by a parallel
workstream — be benchmarked through the existing harness, report, and
scorecard without editing the CLI's provider enum for each new provider.
Loading is explicit and opt-in: a caller must name the exact
``module:ClassName`` target. Nothing is auto-discovered, and loading a
provider here never registers it in ``runtimes.factory.default_registry``
or any ``RoutingPolicy``, so a benchmarked provider still cannot reach
default routing.
"""
from __future__ import annotations

import importlib
from typing import Any


class ProviderLoadError(ValueError):
    """Raised when a provider target cannot be imported, constructed, or
    does not structurally satisfy the DevelopmentRuntime protocol."""


def parse_target(target: str) -> tuple[str, str]:
    """Split a ``package.module:ClassName`` target string."""
    if not isinstance(target, str) or target.count(":") != 1:
        raise ProviderLoadError("provider target must be of the form 'package.module:ClassName'")
    module_name, attribute = (part.strip() for part in target.split(":"))
    if not module_name or not attribute:
        raise ProviderLoadError("provider target must be of the form 'package.module:ClassName'")
    return module_name, attribute


def implements_development_runtime(candidate: Any) -> bool:
    """Structural check against ``runtimes.base.DevelopmentRuntime``.

    ``DevelopmentRuntime`` is a non-runtime-checkable Protocol, so this
    verifies the required surface directly rather than via isinstance.
    """
    if not isinstance(getattr(candidate, "name", None), str) or not getattr(candidate, "name"):
        return False
    return callable(getattr(candidate, "health", None)) and callable(getattr(candidate, "execute", None))


def load_provider(target: str) -> Any:
    """Import and instantiate a provider from ``module:ClassName``.

    Construction convention, tried in order: a zero-argument
    ``create()`` classmethod if present, otherwise a zero-argument
    constructor. Anything requiring configuration should expose
    ``create()`` and read its own environment, matching how the existing
    runtime adapters configure themselves.
    """
    module_name, attribute = parse_target(target)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ProviderLoadError(f"cannot import provider module {module_name!r}: {exc}") from exc

    factory = getattr(module, attribute, None)
    if factory is None:
        raise ProviderLoadError(f"module {module_name!r} has no attribute {attribute!r}")

    creator = getattr(factory, "create", None)
    try:
        instance = creator() if callable(creator) else factory()
    except Exception as exc:
        raise ProviderLoadError(f"cannot construct provider {target!r}: {type(exc).__name__}: {exc}") from exc

    if not implements_development_runtime(instance):
        raise ProviderLoadError(f"provider {target!r} does not implement the DevelopmentRuntime protocol (needs name, health(), execute())")
    return instance
