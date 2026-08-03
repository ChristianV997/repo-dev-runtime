#!/usr/bin/env python3
"""Install the AI coding-assistant development scaffold into a target repo.

Usage:
    python install.py TARGET_DIR [--repo-name NAME] [--force]
        [--force-agents-md] [--dry-run] [--no-tests]
        [--security-sensitive-paths "a,b,c"]

Safe to re-run: files that already match the (substituted) template are
left untouched, and files that differ are never silently overwritten
unless --force is passed (AGENTS.md/CLAUDE.md additionally require
--force-agents-md, since a target repo may have already customized them).

Thin wrapper: all real logic lives in repo_dev_runtime.scaffold.installer,
shared with the `repo-dev-runtime-install` console-script entry point. This
is a separate concern from the rest of repo_dev_runtime (the live
multi-agent control plane): it stamps a baseline dev-tooling scaffold into
an arbitrary target repo rather than orchestrating agents against one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_dev_runtime.scaffold.installer import cli_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(cli_main())
