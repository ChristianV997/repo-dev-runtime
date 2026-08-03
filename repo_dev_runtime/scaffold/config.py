"""repo_dev_runtime.config — token definitions and template->target mapping.

Deliberately stdlib-only: plain `str.replace` substitution over a small,
fixed token set, no templating dependency. Tokens are `{{...}}`-delimited
so they never collide with Markdown/YAML/Python syntax.
"""
from __future__ import annotations

from pathlib import Path

# File extensions eligible for token substitution; everything else is
# copied byte-for-byte unchanged.
TEXT_EXTENSIONS = {".md", ".py", ".yml", ".yaml"}

# Top-level directories under templates/ that are renamed on install.
# Everything not listed here maps 1:1 by relative path.
DIR_RENAMES = {
    "serena": ".serena",
}

# templates/AGENTS.md fans out to two identical installed files, since the
# source scaffold treats them as one policy document under two filenames
# some tools look for.
FANOUT = {
    "AGENTS.md": ("AGENTS.md", "CLAUDE.md"),
}

# Files that get the strictest overwrite protection: never auto-overwritten
# even with --force, since a target repo may have already customized its
# own copy. A separate --force-agents-md flag is required to override these.
PROTECTED_FILES = {"AGENTS.md", "CLAUDE.md"}

# Placeholder tokens with a safe, honest default ("fill this in") for
# anything the installer can't infer from the target repo alone.
DEFAULT_TOKEN_VALUES = {
    "{{SECURITY_SENSITIVE_PATHS}}": "fill in this repo's security-sensitive path list (see semgrep/ai-safety.yml)",
    "{{OLLAMA_MODEL}}": "fill in your local model name",
    "{{OBSIDIAN_VAULT_PATH}}": "fill in your local vault path",
    "{{OLLAMA_PROVIDER_PATH}}": "fill in if this repo has an LLM provider abstraction",
}


def build_tokens(*, repo_name: str, repo_root: Path, date_str: str,
                  security_sensitive_paths: str | None = None) -> dict[str, str]:
    """Assemble the full substitution token dict for one install run."""
    tokens = dict(DEFAULT_TOKEN_VALUES)
    tokens["{{REPO_NAME}}"] = repo_name
    tokens["{{REPO_ROOT}}"] = str(repo_root)
    tokens["{{DATE}}"] = date_str
    if security_sensitive_paths:
        tokens["{{SECURITY_SENSITIVE_PATHS}}"] = security_sensitive_paths
    return tokens
