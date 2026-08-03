# Repository Development Runtime

`repo-dev-runtime` is a repository-agnostic control plane for bounded coding
agents. It uses repository-local manifests, disposable Git worktrees, typed
task/result envelopes, and explicit runtime policies.

The runtime supports adapters for Ollama, OpenAI-compatible gateways such as
OmniRoute, Agent-Reach, Hermes, DeerFlow, and an optional OpenClaw sidecar.
All live adapters are disabled unless explicitly configured. Agents may edit
and test isolated worktrees, but this v1 runtime never merges or deploys.

## First use

```powershell
python -m repo_dev_runtime.cli probe .
python -m repo_dev_runtime.cli init-manifest .
python -m pytest -q
```

The package is intentionally stdlib-only. Provider SDKs and sidecars remain
external processes or HTTP services behind bounded adapters.

## Repository neutrality

`repo-dev-runtime` is the only implementation repository. MarketOS and
NeuroTopology-Sim are consumers, not runtime dependencies. Their manifests can
be checked without changing either checkout:

```powershell
python -m repo_dev_runtime.cli validate-consumers \
  C:\path\to\MarketOS C:\path\to\NeuroTopology-Sim
```

Reviewed reusable capabilities are tracked in
`provenance/source_inventory.json`. Raw data, generated artifacts, credentials,
domain-specific pipelines, cloud launchers, and repository-specific state are
intentionally excluded.

The runtime uses five bounded roles: planner, implementer, tester, reviewer,
and integrator. The integrator can prepare a handoff but cannot merge or push.
Scheduling is declarative and one-shot; no background daemon is implemented.

## Live workflow

Live orchestration is opt-in and provider health is checked before routing:

```powershell
python -m repo_dev_runtime.cli run C:\path\to\repository `
  --prompt "Inspect the failing test and propose a minimal fix" `
  --live --enable-ollama
```

Use `--resume --run-id <id>` to continue an interrupted inspection run. Live
`--apply-edits` runs are intentionally not resumable yet: their disposable
worktree is removed, and the runtime will not reuse cached results without a
durable patch-replay record. Artifacts default
to `%USERPROFILE%\.repo-dev-runtime\runs\<repository>` rather than modifying
the consumer checkout. Paid runtimes require `--approve-paid` and explicit
policy enablement. `--create-pr` additionally requires the consumer manifest to
allow PR creation and only publishes a generated `repo-dev/*` branch; merging
is never automated.

## Evaluating external coding-agent providers

`repo_dev_runtime/eval/` is a separate, controlled benchmark layer for
scoring external providers (coding agents, reviewer bridges,
repository-context tools) without weakening the governance model above:

```powershell
python -m repo_dev_runtime.cli benchmark --provider fake --fake-reviewer
python -m repo_dev_runtime.cli benchmark --provider ollama --live --enable-ollama
python -m repo_dev_runtime.cli benchmark --provider-module my_package.my_module:MyRuntime --live
```

Any provider implementing the `DevelopmentRuntime` protocol can be scored
through `--provider-module` without changing this package; see
`docs/provider-integration-guide.md`.

The default fake provider is deterministic and requires no network access
or credentials. Real providers require `--live`; the reviewer bridge
(`--enable-pr-agent`) and repository-context providers are opt-in and
disabled by default. This benchmark never pushes, merges, or creates a
pull request, and no provider evaluated here — including OpenHands and
mini-SWE-agent, which are recorded only as blocked evaluation specs — is
ever part of default runtime routing. See `docs/eval-layer-overview.md`.
