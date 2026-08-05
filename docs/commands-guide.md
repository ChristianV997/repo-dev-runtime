# Commands Guide: Getting the Most Useful Results

A curated, practical sequencing of `repo-dev-runtime`'s CLI surface. This
doc doesn't introduce new commands — every invocation below is an exact
shape already verified in `README.md` and
`docs/quickstart-consumer-onboarding.md`; this doc's job is picking the
right one for the situation you're in and ordering them sensibly.

On PowerShell, see `docs/windows-powershell-setup.md` for the mechanical
bash-to-PowerShell translation applied to every command below.

## 1. Sanity-check a repository before spending anything

Always start here — it costs nothing (no credentials, no network, no
worktree) and catches manifest problems early:

```bash
python -m repo_dev_runtime.cli probe .
python -m repo_dev_runtime.cli init-manifest .
python -m repo_dev_runtime.cli validate-consumers .
python -m repo_dev_runtime.cli run . --prompt "Summarize what this repository does"
```

The final dry run exercises all five roles (planner/implementer/tester/
reviewer/integrator) against static context and returns
`ready_for_human_review` — confirms the manifest itself is valid before
anything live is attempted.

## 2. A real live run: OpenRouter primary, Ollama backup

The routing default now prefers `openai_compatible` (OpenRouter or any
OpenAI-compatible gateway) over `ollama` for every role. Because
`openai_compatible` is a paid runtime, **`--approve-paid` is required on
every invocation** that enables it — omitting it silently falls through
to Ollama (or fails closed if Ollama isn't also enabled):

```bash
export DEV_OMNIROUTE_URL=https://openrouter.ai/api/v1
export DEV_OMNIROUTE_TOKEN=$OPENROUTER_API_KEY
export DEV_OMNIROUTE_MODEL=anthropic/claude-3.5-sonnet
export DEV_OMNIROUTE_ENABLED=true
export OLLAMA_URL=http://127.0.0.1:11434
export DEV_RUNTIME_OLLAMA=true

python -m repo_dev_runtime.cli run . \
  --prompt "Fix the failing test" --base-ref main \
  --live --enable-omniroute --enable-ollama --approve-paid \
  --apply-edits --artifacts-root ~/.repo-dev-runtime/runs
```

If you only want to prove the backend connection without editing
anything, drop `--apply-edits` (see `docs/quickstart-consumer-onboarding.md`
step 4 for the exact no-op-safe version of this).

## 3. Routing Aider as the implementer

`--enable-aider` requires a distinct core provider for the other roles
and an independent reviewer — this is enforced before the run starts, not
a suggestion:

```bash
export DEV_RUNTIME_AIDER=true
export DEV_RUNTIME_AIDER_COMMAND='["aider"]'
export AIDER_MODEL=ollama/llama3.2:latest   # or openrouter/<model> with OPENROUTER_API_KEY

python -m repo_dev_runtime.cli run . \
  --prompt "Fix the failing test" --base-ref main \
  --live --enable-aider --enable-omniroute --enable-ollama --approve-paid \
  --apply-edits --artifacts-root ~/.repo-dev-runtime/runs
```

See `docs/aider-adapter.md` for the full gating rules and installation
steps.

## 4. Publishing a PR

Requires `pull_request_creation: true` in the manifest and a real
`GITHUB_TOKEN`/`GH_TOKEN` with `repo` scope:

```bash
export GITHUB_TOKEN=ghp_your_token_with_repo_scope
python -m repo_dev_runtime.cli run . \
  --prompt "Fix the failing test" --base-ref main \
  --live --enable-omniroute --enable-ollama --approve-paid \
  --apply-edits --create-pr --artifacts-root ~/.repo-dev-runtime/runs
```

## 5. Resuming an interrupted or already-completed run

```bash
python -m repo_dev_runtime.cli run . \
  --prompt "Fix the failing test" --base-ref main \
  --live --enable-omniroute --enable-ollama --approve-paid \
  --apply-edits --resume --run-id <run_id> \
  --artifacts-root ~/.repo-dev-runtime/runs
```

## 6. Unattended / scheduled invocations

Add `--scheduler-state-file`/`--schedule-key` to any of the live shapes
above so an external cron/systemd timer can invoke the exact same command
repeatedly without redoing already-completed work:

```bash
python -m repo_dev_runtime.cli run . \
  --prompt "Nightly review pass" --base-ref main \
  --live --enable-omniroute --enable-ollama --approve-paid \
  --apply-edits --create-pr \
  --scheduler-state-file /var/lib/repo-dev-runtime/state.json \
  --schedule-key nightly-review \
  --artifacts-root /var/lib/repo-dev-runtime/runs
```

Add `--rerun-completed` only when you deliberately want an
already-succeeded key to run again. See
`docs/aws-autonomous-deployment.md` for a full unattended deployment
built on this shape.

## 7. Evaluating a provider before trusting it in routing

```bash
python -m repo_dev_runtime.cli benchmark --provider fake --fake-reviewer
python -m repo_dev_runtime.cli benchmark --provider ollama --live --approve-external-provider-benchmark
python -m repo_dev_runtime.cli benchmark \
  --provider-module repo_dev_runtime.runtimes.aider:AiderRuntime \
  --live --approve-external-provider-benchmark \
  --fixture one_file_bugfix --task-timeout-s 300
```

A good scorecard here is evidence for promoting a provider into
`RoutingPolicy`, not a decision made automatically — see
`docs/provider-integration-guide.md`.
