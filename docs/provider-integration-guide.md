# Adding a Real Provider

How to take a provider from "not integrated" to "has a scorecard," using
the existing hooks. Nothing here is provider-specific — the same three
steps apply to any coding agent, reviewer, or context tool.

## 1. Implement the relevant contract

| Provider kind | Implement | Reference implementation |
|---|---|---|
| Coding agent | `runtimes.base.DevelopmentRuntime` — `name`, `health()`, `execute(task) -> DevResult` | `runtimes/ollama.py` |
| Reviewer | a callable `EvalRequest -> EvalResult` normalizing into `RepoDev.ReviewVerdict.v1` | `eval/pr_agent.py` |
| Repository context | `eval.context_providers.ContextProvider` | `eval/context_providers.py` |

House rules every adapter follows, taken from the existing ones:

- Disabled by default behind an explicit env flag; `health()` returns
  `configured=False` immediately when disabled, with no network call.
- `execute()`/`review()` **never raise** — every failure becomes a
  `failed`/`blocked` result with an `error_type`.
- Enforce `max_output_bytes`; a missing credential yields an explicit
  `blocked` result (`governance.credentials.missing_credential_result`).
- Build subprocess environments with
  `governance.credentials.build_subprocess_env` and a provider-specific
  `CredentialAllowlist` — never inherit the host environment. See
  `docs/credential-policy.md`.
- Expose no `apply`/`commit`/`merge`/`push`/`create_pull_request` method.
  Capability the adapter does not have cannot be misused.

## 2. Assert the contract with the shared kit

Call the relevant `assert_*_contract` from `eval/conformance.py` in your
adapter's test file rather than re-deriving containment, fail-closed, and
redaction checks. See `docs/provider-conformance-kit.md`.

## 3. Benchmark it

No CLI change is needed. Point `--provider-module` at the class:

```bash
python -m repo_dev_runtime.cli benchmark \
  --provider-module my_package.my_module:MyRuntime --live \
  --provider-metadata-json '{"version": "...", "lock_hash": "...", "python": "...", "model": "..."}' \
  --markdown-out report.md
```

The provider must be zero-arg constructible, or expose a zero-arg
`create()` classmethod (preferred when it needs configuration — read it
from the environment there, as the built-in adapters do). It gets a
`ProviderScorecard` from the same 7 fixtures, outcome vocabulary, and
report as every other provider. Run more than one provider and
`render_comparison_table` produces the side-by-side metric table.

## What benchmarking does *not* do

Loading and benchmarking a provider never registers it in
`runtimes.factory.default_registry()` or any `RoutingPolicy` — a
benchmarked provider still cannot be reached by `repo-dev-runtime run`.
Promotion to default routing is a separate, deliberate decision requiring
registry wiring and its own policy capability flag; a good scorecard is
evidence for that decision, not the decision itself.

Report `provider_metadata.benchmark_kind` distinguishes a `synthetic`
(fake-provider) run from a `live_provider` one, and `reviewer_kind`
distinguishes `none`/`fake`/`real`. A synthetic run validates the
contract; it is never evidence that a real provider works.
