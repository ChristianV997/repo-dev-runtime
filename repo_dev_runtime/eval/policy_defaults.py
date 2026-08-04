"""Shared default policy for the evaluation layer's optional entry points.

Both ``eval.loader`` and ``eval.pr_agent`` previously defined their own
byte-identical copy of this constant. It is security-relevant, so a single
definition is preferable to two that can drift apart.

Permissive by design: this is a defense-in-depth hook, not the primary
enforcement point. ``repo_dev_runtime.cli`` always constructs and passes
its own real, gated policy (requiring ``--live`` plus an explicit
per-run approval flag) at every call site. A caller who imports these
modules directly and does not pass a policy gets an ungated default, as
an explicit, documented choice rather than a silent gap.

Note what this constant does *not* decide: whether a capability was
*approved for this run*. ``RuntimePolicy.authorize`` takes ``approved``
as a per-call argument, so a callee that hard-codes ``approved=True``
makes the check unconditionally succeed and leaves a caller unable to
express "enabled, but not approved for this run" even when passing a
strict policy. Callees here therefore thread an ``approved`` value
through from their own caller instead of hard-coding it.
"""
from __future__ import annotations

from ..governance.policy import RuntimePolicy

PERMISSIVE_DEFAULT_POLICY = RuntimePolicy(network_access=True, allow_external_provider_benchmark=True)
