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

## `GitHubPublisher` (`--create-pr`) credential requirements

`repo_dev_runtime/integrations/github.py`'s `GitHubPublisher` is the only
component in this package that reads a real, live-consequential
credential. Concretely, an operator must provide:

- **`GITHUB_TOKEN`** (or `GH_TOKEN` as a fallback) in the process
  environment — a GitHub personal access token (classic, or fine-grained
  with the equivalent scope) with **`repo`** scope, since `create_pr`
  both pushes a generated branch and calls the Pull Requests API.
  Without one set, `create_pull_request` raises `PermissionError`
  (`GITHUB_TOKEN or GH_TOKEN is required for PR creation`) — it never
  silently no-ops.
- The consumer repository's **`origin` remote must be a `github.com`
  URL** (`git@github.com:owner/repo.git` or
  `https://github.com/owner/repo`) — `_owner_repo` parses it with a
  `github.com` regex and raises `ValueError("origin is not a GitHub
  remote")` for anything else (e.g. GitHub Enterprise, GitLab, a local
  bare remote).
- The branch pushed is always runtime-generated, matching
  `repo-dev/[A-Za-z0-9._/-]+` (`publish_branch` rejects anything else) —
  an operator never needs to (and cannot) name the branch themselves.

See `docs/quickstart-consumer-onboarding.md` for these requirements in
the context of a full worked `--create-pr` example.

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
