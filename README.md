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
