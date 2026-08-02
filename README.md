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
