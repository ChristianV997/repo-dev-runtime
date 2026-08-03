"""repo_dev_runtime.scaffold — bootstrap installer for the AI dev-tooling
scaffold (Serena config, Semgrep safety rules, session-handoff scripts,
canonical-docs templates, AGENTS.md/CLAUDE.md policy).

Kept as a separate subpackage from the rest of repo_dev_runtime (the live
multi-agent control plane) since it solves a different problem: stamping a
baseline AI-dev scaffold into an arbitrary target repository, rather than
orchestrating bounded agents against one.
"""
from .installer import InstallReport, install

__all__ = ["InstallReport", "install"]
