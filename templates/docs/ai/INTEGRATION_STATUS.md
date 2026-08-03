# Integration Status

Updated: {{DATE}}

This is a compact status ledger. Confirm current code before relying on it.

| Area | Status | Rule |
|---|---|---|
| <!-- e.g. your core execution loop --> | <!-- Implemented / In progress / Guarded --> | <!-- one-line rule --> |
| <!-- e.g. external API integration --> | <!-- status --> | <!-- one-line rule --> |
| Ollama | Optional, verify locally | `http://localhost:11434`; opt-in for low-risk tasks only; verify with `verify_local_integrations.py` |
| Obsidian handoff | Optional, verify locally | Writes only generated handoffs; verify with the same command |
| Serena | Configured via `.serena/project.yml` | Official `oraios/serena` via `uvx`; use symbol navigation by default |

Avoid adding a second client or loop without documenting migration and
ownership.
