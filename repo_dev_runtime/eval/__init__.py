"""Controlled evaluation layer for external coding-agent providers.

This subpackage is deliberately separate from the rest of
``repo_dev_runtime`` (the live multi-agent control plane): it exists to
benchmark and score external providers (coding agents, reviewer bridges,
repository-context tools) against a deterministic fixture suite, reusing
the existing governance primitives (worktree isolation, structured edit
proposals, reviewer verdicts, fail-closed command policy) without
duplicating or bypassing them. Nothing in this package can push, merge,
or create a pull request, and no provider evaluated here is added to
default runtime routing.
"""
