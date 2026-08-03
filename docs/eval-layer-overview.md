# Provider Evaluation Layer: Overview

`repo_dev_runtime/eval/` is a controlled benchmark and scoring layer for
external coding-agent providers (coding agents, reviewer bridges,
repository-context tools). It is separate from the rest of
`repo_dev_runtime` — the live, five-role multi-agent control plane — and
reuses that control plane's governance primitives rather than duplicating
or bypassing them.

## Build vs. adopt decisions

| Provider | Decision | Why |
|---|---|---|
| Aider | Reference only | Inspired the static repository map; no Aider code adapted into the eval layer (owned separately). |
| PR-Agent | Bounded adapter, disabled by default | Reviewer-only bridge (`eval/pr_agent.py`); exact live CLI/API not assumed, so only the interface plus a fake are implemented. |
| RepoAgent | Optional interface, not adopted | `ContextProvider` protocol defined; the dependency-free static map stays the default. |
| Tree-sitter | Optional interface, not adopted | Same `ContextProvider` protocol; no grammars/bindings vendored. |
| OpenHands | Evaluation record only | `BenchmarkProviderSpec`, `evaluation_status="blocked"`; not installed, not routed. |
| mini-SWE-agent | Evaluation record only | Same as OpenHands. |

## What "adopted" would require

Any of the paper-only providers above becoming a live, routable adapter
would require: a `DevelopmentRuntime`-protocol implementation, explicit
wiring into `runtimes/factory.py`/`runtimes/registry.py`, a new
`RuntimePolicy` capability flag gating it, and — for anything that can
edit files or run shell commands — verification that it cannot escape a
disposable worktree, bypass the command policy, or push/merge/create a
PR on its own. None of that has been done here; this layer only measures
and records.

## Ownership

This layer owns the *generic* evaluation machinery; it deliberately does
not own any particular provider's environment, installation, or adoption
decision. Keeping that line clear is what prevents two parallel
implementations of the same responsibility.

**Owned here** (change these in `repo_dev_runtime/eval/`):

- Evaluation contracts: `ProviderScorecard`, `FixtureCaseResult`,
  `BenchmarkProviderSpec`, `EvalRequest`/`EvalResult`.
- The fixture suite and harness, and the outcome vocabulary.
- Report rendering, the comparison table, and benchmark history.
- The `--provider-module` loading hook and the conformance kit.
- The shared credential allowlist/redaction utility
  (`governance/credentials.py`).

**Not owned here** — belongs to whoever integrates a specific provider:

- Provisioning and pinning a provider's runtime environment
  (interpreter, package versions, dependency locks).
- Running live benchmarks against real providers, and provisioning the
  credentials those runs need.
- The adoption decision for any given provider, and any promotion to
  default routing (which additionally requires registry wiring and a
  policy capability flag — see `docs/provider-integration-guide.md`).
- Provider-specific adapter implementations beyond the bounded reviewer
  bridge interface already here.

If you are adding a real provider, start at
`docs/provider-integration-guide.md` — you should not need to modify this
layer at all.

See also: `docs/provider-isolation-model.md`, `docs/benchmark-methodology.md`,
`docs/credential-policy.md`, `docs/reviewer-independence.md`,
`docs/provider-conformance-kit.md`, `docs/provider-integration-guide.md`.
