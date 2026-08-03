# Credential Policy

## Default: credential-free

The `benchmark` CLI subcommand's default provider (`--provider fake`)
never reads, needs, or forwards any credential. Every external-provider
adapter added by the evaluation layer (`PRAgentReviewAdapter`) is
disabled unless explicitly enabled.

## Allowlist, not denylist

`repo_dev_runtime/governance/credentials.py` provides
`CredentialAllowlist` + `build_subprocess_env()`: a provider's subprocess
environment contains **only** variables matching an explicit prefix (e.g.
`PR_AGENT_`) plus a small fixed set of universally-safe names (`PATH`,
`HOME`, `USERPROFILE`, `TEMP`, `TMP`). The full host environment is never
copied into a provider subprocess. This is opt-in and provider-specific —
adding a new provider means declaring its own allowlist, not inheriting
one.

(`tools/runner.run_command`, used for local test/lint/security commands
declared in a repository's own manifest, keeps its existing denylist
behavior — those commands legitimately need broad build-tool environment
variables. Its captured stdout/stderr is now redacted regardless.)

## Redaction

`redact_text`/`redact_json` scrub credential-shaped key names, `Bearer`
tokens, and plain `label: value` patterns from any string or JSON-like
structure before it is stored in a `FixtureCaseResult`, an `EvalResult`,
or a benchmark report. Tests in `tests/test_eval_credentials.py` prove a
deliberately-leaked secret sentinel does not survive into captured
subprocess output.

## Missing credentials

A provider adapter that requires a credential which is not set returns an
explicit `status="blocked"`, `error_type="credential_missing"` result
(`governance.credentials.missing_credential_result`) — never an
exception, and never a silent no-op that looks like success.

## No automatic discovery

Nothing in this layer reads credentials from arbitrary files, config
directories, or shell history. Every credential must arrive through the
process environment, under a name the specific provider's allowlist
explicitly names.

## Redaction is heuristic, not content-based — a known limitation

`redact_text`/`redact_json` catch secrets that appear next to a
credential-shaped key name, in a `Bearer ...` header, or in a `label:
value`/`label=value` line. A raw secret value with no such label — e.g.
pasted into unrelated prose, or held in a field with an innocuous name —
will **not** be redacted. This is a real, accepted limitation, not an
oversight: `tests/test_eval_conformance.py::test_no_credential_leak_detects_an_unredactable_sentinel`
exercises exactly this negative case so it stays documented rather than
silently assumed. Callers that need certainty a specific secret never
appears anywhere in output should scrub it at the source (e.g. never
pass it into a provider's stdout-visible arguments) rather than relying
on this redaction as a backstop.
