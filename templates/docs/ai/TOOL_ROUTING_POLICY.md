# Tool Routing Policy

Updated: {{DATE}}

| Task | Route |
|---|---|
| Small local change | targeted search/navigation → targeted tests |
| Medium feature | targeted navigation → current external docs if needed → tests → filtered Semgrep |
| Architecture/refactor | dependency/caller map → targeted implementation → full tests → filtered Semgrep |
| External OSS evaluation | bounded snapshot → license/security review → adapter design; do not copy blindly |
| Security, payments, auth, webhooks | targeted implementation → tests → Semgrep; CodeQL (if configured) runs automatically in CI for {{SECURITY_SENSITIVE_PATHS}} |
| Session memory | generate handoff → update only relevant `docs/ai` files |

Repository tools should remain optional and local-first. Do not install broad MCP or skill collections without review.
