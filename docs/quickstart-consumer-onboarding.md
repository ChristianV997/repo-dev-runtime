# Quickstart: Onboarding a Consumer Repository

## Why this doc exists

README.md documents `--live`, `--apply-edits`, and `--create-pr` in
separate paragraphs, and `validate-consumers` separately again — a new
operator has to piece the full path together themselves, and no doc
shows the manifest edits a real multi-project consumer repo (like
MarketOS's `backend/`/`frontend/`/`scripts/` layout) needs before
`--create-pr` does anything useful. This doc is the single, concrete,
runnable path from "fresh consumer repo" to "a real live PR," reusing the
protocol-accurate local stub server already validated in
`docs/live-validation.md` so every command below is something you can
actually run and check, not just prose.

## 1. Probe and initialize the manifest

From the consumer repository's root:

```bash
python -m repo_dev_runtime.cli probe .
python -m repo_dev_runtime.cli init-manifest .
```

`init-manifest` auto-detects `test_command` (npm/pytest/`git status
--short`, in that order) and writes `.dev-runtime/repository.json` with
`allowed_paths: ["."]` and `pull_request_creation: false` — safe
defaults for a first look, but two things almost always need tuning
before a live run does anything useful for a real project:

- **`allowed_paths`/`source_paths`**: for a monorepo, scope these to the
  subproject you actually want the agent editing (e.g. `["backend"]`),
  not the whole repository. See `docs/examples/monorepo-repository.json`
  for a worked example matching a `backend/`/`frontend/`/`scripts/`
  layout — copy it into `.dev-runtime/repository.json` and adjust the
  `test_command`/`lint_command` to your project's real commands.
- **`pull_request_creation`**: defaults to `false`. Flip it to `true`
  once you're ready for `--create-pr` to be reachable at all — the CLI
  refuses `--create-pr` with `reason: "manifest_disables_pull_request_creation"`
  otherwise.

## 2. Validate the manifest (read-only, no credentials needed)

```bash
python -m repo_dev_runtime.cli validate-consumers .
```

This only reads `.dev-runtime/repository.json` and the repository's git
state — nothing is modified, and no live backend or GitHub token is
required for this step.

## 3. A dry run (no worktree, no edits, no credentials)

```bash
python -m repo_dev_runtime.cli run . --prompt "Summarize what this repository does"
```

Every dry run exercises all five roles (planner, implementer, tester,
reviewer, integrator) against the static repository context and returns
`status: "ready_for_human_review"` — this works with zero external
infrastructure and is the cheapest way to confirm the manifest itself is
valid before going live.

## 4. A real live run without editing (proves the backend connection)

A live run needs a reachable coding-agent backend — this is the one
genuine external prerequisite the tool cannot bundle for you. For a
real deployment that's a running Ollama instance or an OmniRoute/Hermes/
DeerFlow-compatible gateway (`OLLAMA_URL`/`DEV_RUNTIME_OLLAMA`, etc. — see
`docs/provider-integration-guide.md`). To reproduce this step yourself
right now without standing up real infrastructure,
`docs/live-validation.md`'s protocol-accurate local stub server works
identically from the adapter's point of view — this is exactly that
doc's own worked example:

```bash
# terminal 1
PYTHONPATH=. python3 -m tests.support.live_servers 21434

# terminal 2
OLLAMA_URL=http://127.0.0.1:21434 DEV_RUNTIME_OLLAMA=true \
  python -m repo_dev_runtime.cli run . \
  --prompt "Summarize what this repository does" --base-ref main \
  --live --enable-ollama --max-fix-attempts 0 \
  --artifacts-root /tmp/example-runs
```

This is a genuine HTTP round trip through the real `OllamaRuntime`
adapter code, returning `status: "ready_for_human_review"` with 5 real
results. It intentionally omits `--apply-edits` here: that flag requires
the implementer role's response to be schema-valid `RepoDev.EditProposal.v1`
JSON (not just any text), which this generic wire-protocol stub does not
produce — a real model configured against your actual repository would.
`tests/test_cli_lifecycle.py`'s `FakeOllamaRuntime` is the reproducible,
CI-checked stand-in for that contract; it drives the exact
`--live --enable-ollama --apply-edits` path below end to end (worktree
creation, proposal validation and application, quality checks) against a
scripted implementer that does emit valid proposal JSON — read it for
the fully worked, executable version of the next two steps.

## 5. `--apply-edits` and resuming a run

Once your real backend is configured to emit `EditProposal`/`ReviewVerdict`
JSON per `docs/provider-integration-guide.md`'s contract, the same
command from step 4 with `--apply-edits` added builds a real disposable
Git worktree, applies the validated proposal, runs your manifest's real
`test_command`/`lint_command`, and returns a `run_id` you can resume:

```bash
python -m repo_dev_runtime.cli run . \
  --prompt "Fix the failing test" --base-ref main \
  --live --enable-ollama --apply-edits \
  --artifacts-root /tmp/example-runs
```

The consumer checkout itself is never modified directly — only the
disposable worktree is, and it is discarded (or kept as a branch)
depending on the outcome. To resume an interrupted or already-completed
run:

```bash
python -m repo_dev_runtime.cli run . \
  --prompt "Fix the failing test" --base-ref main \
  --live --enable-ollama --apply-edits --resume \
  --run-id <run_id from above> --artifacts-root /tmp/example-runs
```

Resume replays cached role results and, for `--apply-edits` runs, the
checksum-verified patch-replay ledger — it never re-publishes a PR or
rebuilds a worktree for a run that already reached
`ready_for_human_review`/`pr_created`.

## 6. `--create-pr`: what it actually needs

Once `pull_request_creation: true` is set in the manifest (step 1), a
live `--create-pr` run additionally needs:

- **`GITHUB_TOKEN`** (or `GH_TOKEN`) in the environment, with **`repo`**
  scope — see `docs/credential-policy.md`'s `GitHubPublisher` section for
  exactly what's required and why.
- The consumer repository's `origin` remote must be a real `github.com`
  URL (`_owner_repo` rejects anything else, including GitHub Enterprise
  or GitLab remotes).

```bash
export GITHUB_TOKEN=ghp_your_token_with_repo_scope
python -m repo_dev_runtime.cli run . \
  --prompt "Fix the failing test" --base-ref main \
  --live --enable-ollama --apply-edits --create-pr \
  --artifacts-root /tmp/example-runs
```

On success, `status: "ready_for_human_review"` is returned and
`promotion.json` in the run's artifact directory records
`"status": "pr_created"` with the real PR URL/number — never merged or
pushed further automatically; a human reviews and merges it.

## End-to-end proof this works

`tests/test_cli_lifecycle.py::test_full_consumer_lifecycle_through_the_real_cli_entry_points`
drives every step above — `probe` → `init-manifest` → `validate-consumers`
→ dry-run → `--live --apply-edits` → `--resume` → `--create-pr` — through
the real `cli.main` entry points against one real throwaway git repo (with
only the coding-agent backend and GitHub API calls faked, matching this
doc's own local-stub-server substitution), asserting each stage's real
output/artifacts are consumed correctly by the next.
