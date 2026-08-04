# Sandboxed Aider Adapter

`repo_dev_runtime.runtimes.aider.AiderRuntime` is an opt-in benchmark
provider, not part of `default_registry()` or `repo-dev-runtime run` routing.
It is for evaluating an operator-installed Aider without granting Aider direct
access to a governed worktree.

## Boundary

1. The runtime requires an explicit task scope and bound task/base/context
   identifiers.
2. It copies only bounded, non-hidden UTF-8 text files within that scope into
   a fresh temporary sandbox.
3. Aider runs with git mutation, analytics, browser, and GUI flags disabled.
4. Aider's sandbox diff is converted into `RepoDev.EditProposal.v1` whole-file
   edits with source hashes.
5. The normal `PatchApplier` validates and applies the proposal to the
   disposable governed worktree. Aider never receives that worktree path.
6. Aider runs in its own process group; a timeout terminates the process tree.

Windows UI dependencies may briefly retain files in the disposable sandbox;
cleanup is explicitly best-effort and never affects the source or governed
worktree.

## Isolated Installation

Install Aider outside this repository and pin its version and dependency lock.
For example, the controlled local evaluation used Python 3.12 and
`aider-chat==0.86.2` under `~/.repo-dev-runtime/tools/`.

## Controlled Invocation

Set only the provider-specific configuration for the shell that runs the
benchmark:

```powershell
$env:DEV_RUNTIME_AIDER = "true"
$env:DEV_RUNTIME_AIDER_COMMAND = "C:\\path\\to\\aider.exe"
$env:AIDER_MODEL = "ollama/llama3.2:latest"
$env:OLLAMA_API_BASE = "http://127.0.0.1:11434"

python -m repo_dev_runtime.cli benchmark `
  --provider-module repo_dev_runtime.runtimes.aider:AiderRuntime `
  --provider-name aider_local `
  --live --approve-external-provider-benchmark `
  --fixture one_file_bugfix --task-timeout-s 180
```

The approval is required even for a local model because an external tool is
being executed. The adapter remains benchmark-only unless a separately
reviewed promotion decision adds routing and policy support.

## Current Evidence

The controlled local `Aider 0.86.2 + ollama/llama3.2:latest` run completed
the one-file behavior-gated fixture through this adapter in about 110 seconds.
An expanded multi-fixture run exceeded a 15-minute outer bound, so this
configuration is not suitable for default automation or broad campaign use.

For an ordinary governed `run --live --apply-edits`, `--enable-aider` also
requires both a general core provider (`--enable-ollama` or
`--enable-omniroute`) for planner/tester/integrator roles and an independent
reviewer (`--enable-omniroute` or `--enable-pr-agent`). The workflow excludes
the implementer from final review and blocks promotion if no independent
verdict is available.
