# Provider Conformance Kit

`repo_dev_runtime/eval/conformance.py` is an **importable library**, not a
test module. When you build a real provider adapter — a coding runtime, a
reviewer bridge, a repository-context provider — call the relevant
`assert_*_contract` from your own test file rather than re-deriving the
same containment, fail-closed, and redaction checks by hand.

One implementation of these rules means tightening a governance rule here
tightens it everywhere at once, and it keeps a second, subtly-different
copy of the checks from drifting into the codebase.

## Available assertions

| Function | Verifies |
|---|---|
| `assert_development_runtime_contract(make_provider, repository=...)` | `name`/`health()`/`execute()` present, `RuntimeHealth` shape, `execute()` returns a valid `DevResult` echoing the task id, and **never raises** |
| `assert_disabled_runtime_contract(make_provider, repository=...)` | Disabled execution returns `skipped`/`blocked` and leaves the Git checkout byte-for-byte unchanged |
| `assert_reviewer_contract(review, ...)` | Returns a valid `EvalResult`, echoes the request id, normalizes into a boolean `approved` on success, and **fails closed** (no normalized verdict on a non-succeeded result) |
| `assert_context_provider_contract(provider, root=...)` | `capabilities()` declares `vendored`, `build()` returns the `(full_context_text, map_text)` 2-tuple matching `context.build_adaptive_context`, and respects the `max_bytes` budget |
| `assert_forbidden_path_respected(provider, root=..., forbidden_segment=...)` | A context provider does not surface content from a forbidden path |
| `assert_no_forbidden_capabilities(subject)` | The subject exposes no `apply`/`commit`/`merge`/`push`/`publish_branch`/`create_pull_request`-style attribute |
| `assert_no_credential_leak(payload, secret=...)` | A known secret sentinel does not survive redaction, for a string or any JSON-like structure |

Each raises `AssertionError` with a specific message on violation and
returns `None` on success, so they compose inside any test framework.

## Example

```python
from repo_dev_runtime.eval.conformance import (
    assert_development_runtime_contract,
    assert_no_forbidden_capabilities,
)
from my_package.my_provider import MyRuntime


def test_my_provider_satisfies_the_runtime_contract(disposable_repo):
    assert_development_runtime_contract(MyRuntime, repository=disposable_repo)
    assert_no_forbidden_capabilities(MyRuntime(), label="my_provider")
```

## Extending the kit

If a real provider surfaces a governance issue the kit does not catch,
add the check **here** and let every provider's tests inherit it — that is
the point of the kit. Do not add a one-off assertion in a single
provider's test file that other providers will silently not benefit from.

The kit's own tests (`tests/test_eval_conformance.py`) include negative
cases for every assertion — a deliberately non-conforming provider that
must be rejected — so the kit is verified to actually catch violations
rather than passing vacuously.
