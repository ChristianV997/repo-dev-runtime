"""Optional repository-context provider interface. The dependency-free
static repository map (``repo_dev_runtime.context.build_adaptive_context``)
remains the fallback used everywhere by default; nothing here is vendored,
nothing here is mandatory, and nothing here is wired into the live
workflow's context path. This module exists so richer context providers
(RepoAgent, Tree-sitter) can be evaluated behind a normalized interface
without adding a hard dependency.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol

from ..context import build_adaptive_context


class ContextProvider(Protocol):
    name: str

    def capabilities(self) -> Mapping[str, Any]: ...

    def build(
        self, root: str | Path, *, objective: str, allowed_paths: tuple[str, ...], forbidden_paths: tuple[str, ...], max_bytes: int
    ) -> tuple[str, str]: ...


class StaticMapContextProvider:
    """Thin wrapper around the existing, dependency-free static repository
    map. This is the always-available fallback; it is never removed or
    modified by this module."""

    name = "static_map"

    def capabilities(self) -> Mapping[str, Any]:
        return {"license": "", "source_url": "", "vendored": False, "dependency_free": True}

    def build(self, root, *, objective, allowed_paths, forbidden_paths, max_bytes):
        return build_adaptive_context(root, objective=objective, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths, max_bytes=max_bytes)


def resolve_context_provider(
    preferred: ContextProvider,
    *,
    root: str | Path,
    objective: str,
    allowed_paths: tuple[str, ...],
    forbidden_paths: tuple[str, ...],
    max_bytes: int,
) -> tuple[str, str, str]:
    """Try ``preferred``; on any failure, fall back to the static map.
    Returns ``(full_context_text, map_text, provider_name_used)`` so
    callers can record which provider actually produced the context.
    """
    try:
        text, map_text = preferred.build(root, objective=objective, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths, max_bytes=max_bytes)
        return text, map_text, preferred.name
    except Exception:
        fallback = StaticMapContextProvider()
        text, map_text = fallback.build(root, objective=objective, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths, max_bytes=max_bytes)
        return text, map_text, fallback.name
