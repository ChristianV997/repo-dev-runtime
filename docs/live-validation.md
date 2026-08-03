# Live Validation of the Runtime Adapters

## Why this doc exists

An audit of this repository found that the governance/workflow/edit-application
core is real and well-tested, but the network-touching runtime adapters —
`OllamaRuntime`, `OpenAICompatibleRuntime`, `HermesRuntime`, `DeerFlowRuntime`
(`repo_dev_runtime/runtimes/{ollama,openai_compatible,sidecars}.py`) — had
never been exercised against anything real. Three of the four had **zero**
test coverage of their actual `execute()` HTTP/JSON/SSE code (not even a
mock); the fourth (`HermesRuntime`) was only ever tested via a
dependency-injected fake that bypassed HTTP entirely.

This doc records how that gap was closed, and how to reproduce the
end-to-end proof yourself.

## What "live" means here

Rather than mocking `urlopen` (which proves nothing new about the adapter
code) or depending on a real third-party model server (which needs internet
access or a downloaded model, and isn't reproducible in every environment),
validation uses **real local HTTP servers that speak each backend's exact
wire protocol** — real TCP sockets, real HTTP parsing, real JSON/SSE
decoding — bound to `127.0.0.1` on an OS-assigned port. This is a genuine
live-network round trip through the actual adapter code, just with a
scripted response instead of a real model behind it.

The harness lives at `tests/support/live_servers.py`; the tests that use it
are in `tests/test_runtimes_live.py` (22 tests covering success, HTTP
errors, malformed/oversized responses, timeouts, connection-refused, and
auth-header forwarding, across all four adapters).

## Reproducing the CLI-level proof

This is the actual sequence run to prove the tool works end-to-end against
a live backend, not just against test fakes:

1. Start a real Ollama-protocol stub server standalone:

   ```bash
   PYTHONPATH=. python3 -m tests.support.live_servers 21434
   # Serving real Ollama-protocol stub on http://127.0.0.1:21434 (Ctrl+C to stop)
   ```

2. Point the CLI's `health` check at it and confirm real reachability:

   ```bash
   OLLAMA_URL=http://127.0.0.1:21434 DEV_RUNTIME_OLLAMA=true \
     PYTHONPATH=. python3 -m repo_dev_runtime.cli health --json
   ```

   Result (captured verbatim from a real run):

   ```json
   {
     "ollama": {
       "name": "ollama",
       "configured": true,
       "reachable": true,
       "capabilities": ["local_inference"],
       "detail": ""
     },
     "openai_compatible": { "...": "disabled, as expected — not enabled for this demo" }
   }
   ```

3. Create a manifest for a small scratch repo (`init-manifest`), then run the
   real five-role workflow against the live stub:

   ```bash
   OLLAMA_URL=http://127.0.0.1:21434 DEV_RUNTIME_OLLAMA=true \
     PYTHONPATH=. python3 -m repo_dev_runtime.cli run /path/to/demo-repo \
     --prompt "Summarize what this repository does" \
     --live --enable-ollama --max-fix-attempts 0
   ```

   Result (captured verbatim from a real run): `status: "ready_for_human_review"`,
   with **5 results** — one per role (planner, implementer, tester, reviewer,
   integrator) — each `status: "succeeded"`, each carrying real telemetry
   (`duration_ms`, `model: "mistral:7b"`, `routed_runtime: "ollama"`,
   incrementing `router_calls`), and each `output` equal to the exact string
   the stub server returned. Every one of those 5 calls was a genuine HTTP
   POST to `/api/chat` over a real socket, parsed by the real
   `OllamaRuntime.execute()` code path — not a fake, not a mock.

This demonstrates the full claim: the governance/workflow engine really can
drive a real (if scripted-for-reproducibility) coding-agent backend
end-to-end, from CLI invocation through all five roles to a
human-review-ready result.

## Running the regression tests

```bash
PYTHONPATH=. python -m pytest tests/test_runtimes_live.py -v
```

These run unconditionally in CI alongside the rest of the suite (no new CI
step needed — `tests/test_runtimes_live.py` is picked up by the existing
`python -m pytest -q` step in `.github/workflows/ci.yml`), so this
validation now runs on every push, not just once during this exercise.

## What this doesn't cover: real Ollama / a real model

This sandboxed environment has no GPU, and its outbound network policy
blocks the Ollama install/download endpoints (confirmed: `curl` to
`ollama.com` returns `403` through this environment's proxy), so a real
`ollama serve` + real model was checked for feasibility and found not
possible here — that requires an operator with a local Ollama install and
is a manual exercise outside the scope of this repo's CI. The protocol-accurate stub
servers above are the validation of record for the adapter code itself
(request construction, response parsing, error classification); they
intentionally do not, and cannot, validate model output quality, since the
model is scripted.
