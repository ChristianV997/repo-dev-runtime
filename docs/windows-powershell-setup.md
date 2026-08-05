# Windows / PowerShell Setup: All Integrations in One Place

`repo-dev-runtime` is intentionally stdlib-only and cross-platform — the
Windows-specific code paths (`scheduler.py`'s `msvcrt` file locking,
`runtimes/aider.py`'s/`sensors/agent_reach.py`'s process-tree kill via
`taskkill /T /F`, the `shlex.split(..., posix=os.name != "nt")` guards
that keep backslash paths from being corrupted) are real, deliberate
code, not an afterthought — and are now also verified on a real
`windows-latest` GitHub Actions runner, not just unit-tested via mocking.
This doc is the single place that walks a PowerShell user through every
integration this repository has, translating each command already
verified in `README.md`/`docs/commands-guide.md` rather than inventing
new ones.

## Prerequisites

- Python 3.11+ (`python --version`)
- Git for Windows
- PowerShell 7+ recommended (Windows PowerShell 5.1 also works — the
  `$env:` syntax used throughout this doc is identical in both)

## Install

```powershell
git clone https://github.com/<you>/repo-dev-runtime.git
cd repo-dev-runtime
python -m pip install -e .
python -m repo_dev_runtime.cli probe .
```

`python -m pytest` (not bare `pytest`) adds the current directory to
`sys.path` itself, so no `PYTHONPATH`/install step is required just to
run the test suite from a checkout.

## Translating bash commands to PowerShell

Every command in `README.md` and `docs/commands-guide.md` is written in
bash; the translation to PowerShell is mechanical:

| bash | PowerShell |
|---|---|
| `export FOO=bar` | `$env:FOO = "bar"` |
| line continuation `\` | `` ` `` (backtick) |
| `~/.repo-dev-runtime/runs` | `$env:USERPROFILE\.repo-dev-runtime\runs` (already noted in README.md) |

Worked example — `docs/commands-guide.md` section 2 (OpenRouter primary,
Ollama backup) translated in full:

```powershell
$env:DEV_OMNIROUTE_URL = "https://openrouter.ai/api/v1"
$env:DEV_OMNIROUTE_TOKEN = $env:OPENROUTER_API_KEY
$env:DEV_OMNIROUTE_MODEL = "anthropic/claude-3.5-sonnet"
$env:DEV_OMNIROUTE_ENABLED = "true"
$env:OLLAMA_URL = "http://127.0.0.1:11434"
$env:DEV_RUNTIME_OLLAMA = "true"

python -m repo_dev_runtime.cli run . `
  --prompt "Fix the failing test" --base-ref main `
  --live --enable-omniroute --enable-ollama --approve-paid `
  --apply-edits --artifacts-root "$env:USERPROFILE\.repo-dev-runtime\runs"
```

Every other command in `docs/commands-guide.md` (Aider routing,
publishing a PR, resuming, scheduled invocations, benchmarking) follows
this exact same substitution — copy the bash version and apply the table
above.

## Integrations

### GitHub

```powershell
$env:GITHUB_TOKEN = "ghp_your_token_with_repo_scope"
```

Needs `repo` scope; the target repository's `origin` remote must be a
real `github.com` URL for `--create-pr` to work (see
`docs/credential-policy.md`).

### OpenRouter / any OpenAI-compatible gateway (OmniRoute)

```powershell
$env:DEV_OMNIROUTE_URL = "https://openrouter.ai/api/v1"
$env:DEV_OMNIROUTE_TOKEN = "sk-or-your-real-key"
$env:DEV_OMNIROUTE_MODEL = "anthropic/claude-3.5-sonnet"
$env:DEV_OMNIROUTE_ENABLED = "true"
```

This is now the first routing preference for every role (see README.md's
"OpenRouter as primary, Ollama as backup"), so every live invocation that
enables it also needs `--approve-paid` — omitting it silently falls
through to Ollama or fails closed.

### Ollama (the backup)

Install Ollama for Windows from ollama.com, then:

```powershell
$env:OLLAMA_URL = "http://127.0.0.1:11434"
$env:DEV_RUNTIME_OLLAMA = "true"
```

### Aider

Install `aider-chat` **in a dedicated virtual environment, not
`pip install --user`**: `AiderRuntime` overrides `USERPROFILE`/`APPDATA`/
`LOCALAPPDATA` for its sandboxed subprocess (`repo_dev_runtime/runtimes/aider.py`),
so a `--user` install — which resolves packages through the real user
profile — breaks under that sandboxing the same way it did on Linux (see
`docs/aider-adapter.md`'s "Isolated Installation" section for the full
explanation of this gotcha). A venv sidesteps it entirely, since a
venv's packages resolve via the interpreter's own `sys.prefix`:

```powershell
python -m venv "$env:USERPROFILE\.repo-dev-runtime\tools\aider-venv"
& "$env:USERPROFILE\.repo-dev-runtime\tools\aider-venv\Scripts\pip.exe" install aider-chat==0.86.2

$env:DEV_RUNTIME_AIDER = "true"
$env:DEV_RUNTIME_AIDER_COMMAND = "$env:USERPROFILE\.repo-dev-runtime\tools\aider-venv\Scripts\aider.exe"
$env:AIDER_MODEL = "ollama/llama3.2:latest"   # or openrouter/<model> with OPENROUTER_API_KEY
$env:OLLAMA_API_BASE = "http://127.0.0.1:11434"
```

`--enable-aider` requires a distinct core provider for the other roles
and an independent reviewer, enforced before the run starts — see
`docs/aider-adapter.md` for the full gating rules and
`docs/commands-guide.md` section 3 for the exact `run` invocation shape.

### PR-Agent bridge (optional, independent reviewer)

```powershell
$env:DEV_RUNTIME_PR_AGENT = "true"
$env:PR_AGENT_COMMAND = '["C:\\path\\to\\pr-agent.exe"]'
$env:PR_AGENT_REQUIRED_CREDENTIAL = "PR_AGENT_OPENAI_API_KEY"
$env:PR_AGENT_OPENAI_API_KEY = "sk-your-key"
```

`PR_AGENT_REQUIRED_CREDENTIAL` names another env var that must be set —
the bridge blocks with a clear error if it isn't. See
`docs/pr-agent-real-integration.md` for the bridge's own setup.

### `infra/aws` (the AWS autonomous-deployment Terraform module)

This is standalone Terraform, entirely separate from the Python
runtime's own env vars above — it needs its own Windows-side install:

```powershell
winget install Hashicorp.Terraform
winget install Amazon.AWSCLI
aws configure
```

Then follow `infra/aws/README.md` exactly as written — every `terraform`/
`aws` command in it is already OS-agnostic.

## Persisting environment variables across sessions

PowerShell has no native `.env` loader; the equivalent is appending
`$env:` lines to your PowerShell profile script so they're set every
time a new shell opens:

```powershell
notepad $PROFILE
# add the $env: lines from the integrations above, then save and
# restart PowerShell (or run `. $PROFILE` to reload the current session)
```

Keep real credentials out of any file you might commit — `$PROFILE`
lives outside this repository by default, which is the point.
