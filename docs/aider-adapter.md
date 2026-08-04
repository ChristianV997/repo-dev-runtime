# Sandboxed Aider Adapter

`repo_dev_runtime.runtimes.aider.AiderRuntime` is registered in
`default_registry()` as a first-preference `implementer` candidate, but
disabled by default (`DEV_RUNTIME_AIDER`) and reachable from `run` only
via the explicit `--enable-aider` flag, which itself requires a distinct
core provider (`--enable-ollama`/`--enable-omniroute`) for the other
roles and an independent reviewer (`--enable-omniroute` or
`--enable-pr-agent`) — enforced before the run starts (see README.md's
"Live workflow" section). It can also be evaluated standalone through
`benchmark --provider-module`, without touching default routing. Either
way, it is for evaluating or using an operator-installed Aider without
ever granting Aider direct access to a governed worktree.

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

**Use a virtual environment, not `pip install --user`.** `AiderRuntime`
sandboxes the subprocess by overriding `HOME`/`USERPROFILE` to a
disposable temporary directory (see Boundary above) so Aider can never
resolve real user-profile paths. A `pip install --user` install resolves
its packages through `~/.local/lib/pythonX/site-packages`, which is
itself resolved via `HOME` — under the sandboxed subprocess environment
this silently becomes `ModuleNotFoundError: No module named 'aider'`. A
venv install (`python -m venv ~/.repo-dev-runtime/tools/aider-venv &&
~/.repo-dev-runtime/tools/aider-venv/bin/pip install aider-chat==0.86.2`,
with `DEV_RUNTIME_AIDER_COMMAND` pointing at that venv's `bin/aider`)
resolves its packages via the interpreter's own `sys.prefix`, independent
of `HOME`, and works correctly. This was confirmed directly in this
round's live verification below — this is why the isolated-install
recommendation above is a hard requirement for the sandboxed path to work
at all, not just a hygiene preference.

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

**Real end-to-end re-verification (this round):** a genuinely different
network path than the run above — real `aider-chat==0.86.2` installed in
a venv, invoked as a real subprocess by `AiderRuntime`, talking real HTTP
to a local server implementing the exact OpenAI-compatible
`/v1/chat/completions` wire protocol (mirroring the pattern
`docs/live-validation.md` already established for `OllamaRuntime` itself,
substituting only the remote model call, never the adapter or the
governed pipeline). The real CLI command from "Controlled Invocation"
above was run unmodified (`benchmark --provider-module
repo_dev_runtime.runtimes.aider:AiderRuntime --live
--approve-external-provider-benchmark --fixture one_file_bugfix`),
pointed at `openai/test-model` instead of `ollama/llama3.2:latest`. Result:
`outcome: "succeeded"`, a real `git`-tracked file changed, the fixture's
real `test_calc.py` subprocess run and passed, and a schema-valid
`RepoDev.EditProposal.v1` — the complete adapter boundary (sandbox
snapshot → real Aider subprocess → real HTTP round trip → sandbox diff →
proposal translation → `PatchApplier`) verified working end to end in
roughly 3.5 seconds against the local stub (a real remote model adds
network/inference latency but exercises the identical code path). This
run is also what surfaced the `pip install --user` HOME-sandboxing
interaction documented above — the first attempt against a `--user`
install failed with `ModuleNotFoundError` until switched to a venv.

For an ordinary governed `run --live --apply-edits`, `--enable-aider` also
requires both a general core provider (`--enable-ollama` or
`--enable-omniroute`) for planner/tester/integrator roles and an independent
reviewer (`--enable-omniroute` or `--enable-pr-agent`) — this pairing is
enforced by `cli.py`'s own gate before the workflow ever runs, so with
`--enable-aider` there is always a distinct provider available for final
review. The workflow's own independent-review requirement is conditional
on that: it blocks promotion only when a second, authorized provider
*was* structurally available and the review result still reports the
implementer's own provider (a misconfigured or misreporting adapter). A
setup with only one provider overall — not the case here, since
`--enable-aider` requires a second one — proceeds to
`ready_for_human_review` with a recorded `self_reviewed_warning` event
instead of blocking forever, since there would be no second party to
defer to and no security benefit to a permanent deadlock.
