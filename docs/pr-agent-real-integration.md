# Real PR-Agent Integration

## What this is

`scripts/pr_agent_bridge.py` is a real, working translation layer between
this repo's reviewer-bridge contract (`repo_dev_runtime/eval/pr_agent.py`'s
`PRAgentReviewAdapter`) and the actual
[`The-PRAgent/pr-agent`](https://github.com/The-PRAgent/pr-agent) package.
It is an operator-installed script, **not** a `repo_dev_runtime`
dependency and **not** a vendored copy of PR-Agent — `pr-agent` pulls in a
heavy third-party tree (`dynaconf`, `litellm`, `PyGithub`, `GitPython`,
etc.) that stays entirely optional.

## Install

```bash
pip install pr-agent GitPython "httpx<0.28"
```

The `httpx<0.28` pin is required, not optional — see "Bugs found" below.

## Configuring `PRAgentReviewAdapter` to use it

```bash
export DEV_RUNTIME_PR_AGENT=true
export PR_AGENT_COMMAND='["python3", "/path/to/scripts/pr_agent_bridge.py"]'
export PR_AGENT_OPENAI_API_BASE=http://127.0.0.1:PORT   # your OpenAI-compatible endpoint, bare host root
```

`PRAgentReviewAdapter` already builds the subprocess's environment from an
allowlist keyed on the `PR_AGENT_` prefix (`governance/credentials.py`), so
`PR_AGENT_OPENAI_API_BASE` and `PR_AGENT_OPENAI_API_KEY`/`PR_AGENT_MODEL`
reach the bridge script automatically — no changes to the adapter or the
allowlist were needed.

## What backend the bridge talks to

PR-Agent needs a real LLM behind it to produce anything — there is no
"offline" review mode. This repo's default validation path points it at a
**local OpenAI-compatible stub server** (`tests/support/live_servers.py`,
the same harness built for the runtime-adapter live validation in
`docs/live-validation.md`), so the round trip is real (real subprocess,
real `pr-agent` code, real HTTP call, real YAML parsing) without any real
credential or live external call. Pointing `PR_AGENT_OPENAI_API_BASE` /
`PR_AGENT_OPENAI_API_KEY` at a real provider instead is a manual, deliberate,
credential-bearing operator choice — nothing in this repo does that by
default or assumes you want to.

## The approve/reject mapping is a documented heuristic

PR-Agent's `/review` command has **no native approve/reject signal**. Its
real output (parsed via PR-Agent's own `pr_agent.algo.utils.load_yaml`,
matching exactly what `PRReviewer._prepare_pr_review` does internally) is:

- `estimated_effort_to_review_1-5` (int)
- `security_concerns` (str, `"No"` if none)
- `relevant_tests` (str, informational)
- `key_issues_to_review` (list of `{relevant_file, issue_header, issue_content, start_line, end_line}`)

`scripts/pr_agent_bridge.py::_to_verdict` maps this into
`RepoDev.ReviewVerdict.v1` as follows — stated plainly as a heuristic, not
a hidden assumption (mirroring `docs/credential-policy.md`'s "heuristic,
not content-based" section):

- `approved = security_concerns == "No" AND estimated_effort <= 3 AND no key_issues_to_review entries`
- Each `key_issues_to_review` entry becomes one `finding`; `severity` is
  `"high"` if `security_concerns != "No"`, else `"medium"` (PR-Agent gives
  no per-issue severity of its own).
- `summary` synthesizes effort/security/issue-count into one sentence.

Anyone relying on this mapping for real promotion gating should read this
section, not just trust the boolean.

## Known limitation: diff must be new-file-only (usually)

The bridge receives only a diff string (`EvalRequest.diff`), never the
original repository — that's a deliberate reviewer-isolation boundary
(see `docs/reviewer-independence.md`). It materializes a throwaway git
repo with one placeholder `README.md`, then `git apply`s the diff. Diffs
that only **add new files** apply cleanly. Diffs that **modify existing
files** need matching base content this bridge doesn't have, and `git
apply` fails — surfaced as a clear non-zero exit and stderr message
(`"diff could not be applied against this bridge's minimal scaffold
repo"`), never a fabricated review. `tests/test_pr_agent_real_bridge.py`
exercises this exact failure path.

## Bugs found while building this (all reproduced against the real package)

Getting a genuine round trip working surfaced four real problems — worth
recording so nobody rediscovers them from scratch:

1. **`LocalGitProvider` is abstract in the shipped `pr-agent==0.2.4`
   wheel.** It's missing implementations of `add_eyes_reaction`,
   `get_commit_messages`, `get_repo_settings`, `remove_reaction` from the
   `GitProvider` ABC — instantiating it raises `TypeError: Can't
   instantiate abstract class LocalGitProvider`. The bridge script defines
   `_WorkingLocalGitProvider(LocalGitProvider)` supplying harmless
   no-op/default implementations (same "not applicable to the local git
   provider" pattern the class already uses elsewhere) and patches
   `pr_agent.git_providers._GIT_PROVIDERS["local"]` to point at it. This
   does not modify the installed package.

2. **`httpx>=0.28` breaks litellm's OpenAI client.** `httpx` 0.28 removed
   the `proxies` kwarg from `Client`/`AsyncClient`; litellm's bundled
   OpenAI call path (as of the version pulled in by `pr-agent==0.2.4`)
   still passes it, raising `TypeError: AsyncClient.__init__() got an
   unexpected keyword argument 'proxies'` on every single call — no
   request ever reaches the network. Fix: pin `httpx<0.28`.

3. **litellm's OpenAI-compatible client does not assume `api_base` ends
   in `/v1`** — it appends `/chat/completions` directly. A bare host-root
   `api_base` (matching this repo's `OLLAMA_URL`/`DEV_OMNIROUTE_URL`
   convention) makes litellm request `/chat/completions`, not
   `/v1/chat/completions`, and gets a 404. Confirmed by instrumenting a
   logging stub server and watching the actual request path. The bridge
   normalizes this itself (`api_base.rstrip('/') + '/v1'`) so
   `PR_AGENT_OPENAI_API_BASE` can stay a bare host root like every other
   `*_URL`/`*_API_BASE` variable in this codebase.

4. **A failed AI call can leave a stale, misleadingly "complete-looking"
   `review.md`.** With `config.publish_output=True`,
   `LocalGitProvider.publish_comment(..., is_temporary=True)` writes
   `"Preparing review..."` to `review.md` *before* the AI call — if that
   call then fails, `review.md` is left containing only that placeholder,
   permanently. Reading `review.md` naively and treating "no recognized
   review fields" as "no findings" silently produced a false
   `approved=True`. This is why the bridge does not read `review.md` at
   all: it captures the raw LLM prediction directly via a
   `PRReviewer` subclass (`_CapturingPRReviewer`, patched into
   `pr_agent.agent.pr_agent.command2class`) and parses it with PR-Agent's
   own `load_yaml`, the same call `_prepare_pr_review` uses internally —
   and fails closed with a clear error whenever no prediction was
   captured at all, rather than guessing from rendered markdown.

## Tests

`tests/test_pr_agent_real_bridge.py` — guarded with
`pytest.importorskip("pr_agent")`, so it's a clean **skip** (not a
failure) in the default CI environment, which does not install pr-agent
(matching the existing "no third-party agent installed or executed in
CI" precedent for the fixture benchmark gate). Anyone with pr-agent
installed gets real, non-skipped assertions covering: a clean approval, a
rejection with findings, fail-closed on malformed LLM output, fail-closed
on an inapplicable diff, and `PRAgentReviewAdapter` itself (unmodified)
consuming the real bridge's real output end to end.
