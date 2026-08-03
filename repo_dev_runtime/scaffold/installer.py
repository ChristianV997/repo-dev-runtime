"""repo_dev_runtime.scaffold.installer — copy + parameterize the AI
dev-tooling scaffold into a target repository.

Idempotent by design: a file that already exists with identical
post-substitution content is left untouched and reported as "unchanged" —
running install twice back-to-back with no target-repo edits in between
produces zero writes on the second run. Files that exist with *different*
content are never silently overwritten; `AGENTS.md`/`CLAUDE.md` get the
strictest protection since a target repo may have already customized them.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .config import DIR_RENAMES, FANOUT, PROTECTED_FILES, TEXT_EXTENSIONS, build_tokens

DEFAULT_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent.parent / "templates"


@dataclass
class InstallReport:
    dry_run: bool = False
    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    overwritten: list[str] = field(default_factory=list)
    skipped_conflict: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """False if any file was left in an unresolved conflict."""
        return not self.skipped_conflict

    def summary_lines(self) -> list[str]:
        verb = "would create" if self.dry_run else "created"
        lines = [f"{verb}: {len(self.created)}", f"unchanged: {len(self.unchanged)}"]
        if self.overwritten:
            lines.append(f"overwritten: {len(self.overwritten)}")
        if self.skipped_conflict:
            lines.append(f"needs manual attention (conflict, not overwritten): {len(self.skipped_conflict)}")
            lines.extend(f"  - {path}" for path in self.skipped_conflict)
        return lines


def discover_manifest(templates_root: Path, *, with_tests: bool = True) -> list[tuple[Path, str]]:
    """Return (source_file, dest_relpath) pairs. Mirrors templates/ 1:1
    except DIR_RENAMES (serena -> .serena) and FANOUT (one source file
    writes multiple dest files)."""
    manifest: list[tuple[Path, str]] = []
    for source in sorted(templates_root.rglob("*")):
        if not source.is_file():
            continue
        rel_parts = source.relative_to(templates_root).parts
        if rel_parts[0] == "tests" and not with_tests:
            continue
        top = rel_parts[0]
        renamed_top = DIR_RENAMES.get(top, top)
        dest_rel = "/".join((renamed_top, *rel_parts[1:])) if len(rel_parts) > 1 else renamed_top

        fanout_key = "/".join(rel_parts)
        if fanout_key in FANOUT:
            for dest_name in FANOUT[fanout_key]:
                manifest.append((source, dest_name))
        else:
            manifest.append((source, dest_rel))
    return manifest


def substitute(text: str, tokens: dict[str, str]) -> str:
    for token, value in tokens.items():
        text = text.replace(token, value)
    return text


def _default_confirm_overwrite(_path: Path) -> bool:
    """Safe default for non-interactive callers: never overwrite."""
    return False


def install(
    target: Path,
    *,
    repo_name: str | None = None,
    templates_root: Path | None = None,
    force: bool = False,
    force_agents_md: bool = False,
    dry_run: bool = False,
    security_sensitive_paths: str | None = None,
    with_tests: bool = True,
    confirm_overwrite: Callable[[Path], bool] | None = None,
) -> InstallReport:
    target = Path(target).resolve()
    templates_root = Path(templates_root) if templates_root else DEFAULT_TEMPLATES_ROOT
    confirm_overwrite = confirm_overwrite or _default_confirm_overwrite
    tokens = build_tokens(
        repo_name=repo_name or target.name,
        repo_root=target,
        date_str=date.today().isoformat(),
        security_sensitive_paths=security_sensitive_paths,
    )

    report = InstallReport(dry_run=dry_run)
    for source, dest_rel in discover_manifest(templates_root, with_tests=with_tests):
        dest_path = target / dest_rel
        raw = source.read_bytes()
        if source.suffix in TEXT_EXTENSIONS:
            content = substitute(raw.decode("utf-8"), tokens).encode("utf-8")
        else:
            content = raw

        if not dest_path.exists():
            report.created.append(dest_rel)
            if not dry_run:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(content)
            continue

        existing = dest_path.read_bytes()
        if existing == content:
            report.unchanged.append(dest_rel)
            continue

        # Existing file differs from the (substituted) template.
        is_protected = dest_rel in PROTECTED_FILES
        can_force = force and not (is_protected and not force_agents_md)
        if can_force or confirm_overwrite(dest_path):
            report.overwritten.append(dest_rel)
            if not dry_run:
                dest_path.write_bytes(content)
        else:
            report.skipped_conflict.append(dest_rel)

    return report


def _interactive_confirm(path: Path) -> bool:
    if not sys.stdin.isatty():
        print(f"skipping (non-interactive, differs from template): {path}", file=sys.stderr)
        return False
    answer = input(f"{path} already exists and differs from the template. Overwrite? [y/N] ")
    return answer.strip().lower() == "y"


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the AI coding-assistant development scaffold into a target repo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", type=Path, help="target repository root")
    parser.add_argument("--repo-name", default=None, help="defaults to the target directory's basename")
    parser.add_argument("--force", action="store_true", help="overwrite differing files without prompting (except AGENTS.md/CLAUDE.md)")
    parser.add_argument("--force-agents-md", action="store_true", help="also allow overwriting a differing AGENTS.md/CLAUDE.md")
    parser.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    parser.add_argument("--no-tests", action="store_true", help="skip installing templates/tests/* into the target's tests/")
    parser.add_argument("--security-sensitive-paths", default=None, help="comma-separated paths to fill into docs/policy placeholders")
    args = parser.parse_args(argv)

    if not args.target.exists() and not args.dry_run:
        args.target.mkdir(parents=True)
    if not (args.target / ".git").exists():
        print(f"warning: {args.target} does not look like a git repository root (no .git)", file=sys.stderr)

    report = install(
        args.target,
        repo_name=args.repo_name,
        force=args.force,
        force_agents_md=args.force_agents_md,
        dry_run=args.dry_run,
        security_sensitive_paths=args.security_sensitive_paths,
        with_tests=not args.no_tests,
        confirm_overwrite=_interactive_confirm,
    )

    for line in report.summary_lines():
        print(line)
    if not report.dry_run and report.ok:
        print("\nNext steps:")
        print("  - Fill in docs/ai/CANONICAL_ARCHITECTURE.md, CANONICAL_PATHS.md, INTEGRATION_STATUS.md, ACTIVE_MODULES.md")
        print("  - Review semgrep/ai-safety.yml's paths.include comment for your repo's layout")
        print("  - Set AI_DEVSTACK_KNOWN_REPOS if you want the multi-repo workspace overview")
        print("  - Verify Ollama/Obsidian integrations are opt-in, not assumed present")
    return 0 if report.ok else 1


def _console_entry_point() -> None:
    raise SystemExit(cli_main())
