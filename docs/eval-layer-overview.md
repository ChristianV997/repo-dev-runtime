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

See also: `docs/provider-isolation-model.md`, `docs/benchmark-methodology.md`,
`docs/credential-policy.md`, `docs/reviewer-independence.md`.
