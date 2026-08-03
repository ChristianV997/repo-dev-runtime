# Claude Code Handoff: {{REPO_NAME}} AI Development Stack

Updated: {{DATE}}

## Repository

- Path: `{{REPO_ROOT}}`
- Repository: {{REPO_NAME}}
- Read first: `docs/ai/README.md`, `docs/ai/CANONICAL_ARCHITECTURE.md`, `docs/ai/KNOWN_GAPS.md`, and `docs/ai/INTEGRATION_STATUS.md`.

## Verified tools

Fill in this table after you've actually checked each tool locally — do not
assume a tool is available just because it's listed here. Run
`python scripts/ai/check_dev_stack.py` first.

| Tool | Status | Use |
|---|---|---|
| Serena | *(verify)* | Default semantic navigation, symbol lookup, references, and bounded edits |
| Ollama | *(verify)* | Optional low-risk local worker inference |
| Obsidian | *(verify)* | One-way session-handoff capture through the local filesystem bridge |
| Repomix | *(verify)* | Bounded snapshots only, mainly for external repositories or selected subsystems |
| Semgrep | *(verify)* | Local and CI safety policy checks |
| uv | *(verify)* | Official Serena runner and isolated Python tools |

## Serena

This repository is registered at `.serena/project.yml` with the Python
language server (adjust `language_servers` in that file if this repo isn't
primarily Python). Register Serena with Claude Code using:

```bash
uvx --from git+https://github.com/oraios/serena serena start-mcp-server --context=claude-code --project-from-cwd
```

Health check:

```bash
uvx --from git+https://github.com/oraios/serena serena project health-check {{REPO_ROOT}}
```

(On Windows PowerShell, prefix with `$env:PYTHONIOENCODING='utf-8'`.)

Use Serena first for symbol-level discovery and edits. Do not read the
entire repository for ordinary tasks.

## Ollama

- API: `http://localhost:11434`
- Model: `{{OLLAMA_MODEL}}`
- Provider: `{{OLLAMA_PROVIDER_PATH}}`
- Use only for bounded, low-risk tasks such as summaries, classification
  drafts, and fixture drafts.
- Do not use it as final authority for security, payments, auth, tenant
  isolation, scientific claims, live ads, deployments, or architecture.

Verify:

```bash
python scripts/ai/verify_local_integrations.py
python scripts/ai/benchmark_ollama.py --model {{OLLAMA_MODEL}} --task summarize --json
```

Benchmark quality must clear your own bar before you rely on it for
anything beyond low-risk drafting.

## Obsidian

- Vault: `{{OBSIDIAN_VAULT_PATH}}`
- Destination: `AI Engineering/Session Handoffs/`
- Never copy source code, secrets, or full chat transcripts into the vault.

Capture a handoff:

```bash
python scripts/ai/generate_session_handoff.py --output docs/ai/SESSION_HANDOFF.md
python scripts/ai/push_handoff_to_obsidian.py --input docs/ai/SESSION_HANDOFF.md --dry-run
python scripts/ai/push_handoff_to_obsidian.py --input docs/ai/SESSION_HANDOFF.md
```

The repository Markdown handoff remains the source of truth.

## Repomix

Use only bounded paths. Example:

```bash
repomix scripts/ai docs/ai --compress --no-files --stdout
```

Do not pack the entire repository by default. Exclude `.env`, credentials,
state, logs, databases, caches, `node_modules`, virtual environments,
generated artifacts, and unrelated repositories.

## Semgrep

Run the compact local wrapper:

```bash
python scripts/ai/run_semgrep_policy.py
python scripts/ai/run_semgrep_policy.py --severity ERROR --fail-on-error
```

Policy: `semgrep/ai-safety.yml`. Current warning findings are review
prompts around external writes; ERROR findings are blocking.

## Claude workflow

1. Check `git status` and preserve unrelated user changes.
2. Read the canonical AI files and the affected subsystem only.
3. Use Serena for symbol/reference navigation.
4. Use Context7 or current official documentation only when changing an
   external API or version-sensitive library.
5. Implement the smallest coherent change; search for existing equivalents
   before adding modules.
6. Run targeted tests first.
7. Run Semgrep on security-sensitive or external-write changes.
8. Run the full suite only when the change warrants it.
9. Generate a compact session handoff and optionally mirror it to
   Obsidian.
10. Report files changed, tests, risks, and the exact next action.

## Do not duplicate

- Do not create another inference router, commerce loop, feature-flag
  system, client per provider, memory API, or handoff system.
- Do not replace this repository's orchestration with a second agent
  runtime.
- Do not install arbitrary MCP servers, skills, or plugins globally.
- Do not enable live ad, payment, publishing, fulfillment, or deployment
  actions without existing approval and risk gates.

## Known limitations

- Graphify and Context7 are not assumed installed or configured; verify
  before treating either as available.
- CodeQL, if you add it, is normally its own CI workflow (not folded into
  an existing shared-trigger workflow) scoped via `paths:` to
  {{SECURITY_SENSITIVE_PATHS}} — the same boundary
  `semgrep/ai-safety.yml`'s `devstack-external-write-call-review` rule
  targets. Treat it as CI-only, not a local dev-loop tool.
- Ollama is local and optional; small local models require frontier-model
  review for any generated artifact.
