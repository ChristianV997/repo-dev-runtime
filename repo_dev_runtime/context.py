"""Bounded, read-only repository context for model prompts."""
from __future__ import annotations

from pathlib import Path

from .repository_map import build_repository_map, rank_entries, render_repository_map


_TEXT_SUFFIXES = {".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".ps1", ".css", ".html"}


def build_repository_context(root: str | Path, *, allowed_paths: tuple[str, ...], forbidden_paths: tuple[str, ...], max_bytes: int = 80_000) -> str:
    """Return a deterministic bounded snapshot; never executes repository code."""
    if max_bytes < 1_024:
        raise ValueError("context limit is too small")
    base = Path(root).resolve()
    files: list[Path] = []
    for candidate in sorted(base.rglob("*")):
        if not candidate.is_file() or candidate.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        relative = candidate.relative_to(base).as_posix()
        parts = set(Path(relative).parts)
        if any(item in parts or relative.startswith(item.rstrip("/") + "/") for item in forbidden_paths):
            continue
        if "." not in allowed_paths and not any(relative == item or relative.startswith(item.rstrip("/") + "/") for item in allowed_paths):
            continue
        files.append(candidate)
    sections = [f"Repository root: {base}", "Files:"]
    used = sum(len(item.encode("utf-8")) for item in sections) + 1
    for candidate in files:
        relative = candidate.relative_to(base).as_posix()
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        section = f"\n--- {relative} ---\n{content}"
        encoded = section.encode("utf-8")
        if used + len(encoded) > max_bytes:
            break
        sections.append(section)
        used += len(encoded)
    return "\n".join(sections)[:max_bytes]


def build_adaptive_context(root: str | Path, *, objective: str, allowed_paths: tuple[str, ...], forbidden_paths: tuple[str, ...], max_bytes: int = 80_000) -> tuple[str, str]:
    """Return a stable map plus objective-ranked file excerpts within one budget."""
    if max_bytes < 4_096:
        raise ValueError("context limit is too small")
    entries = build_repository_map(root, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths)
    map_text = render_repository_map(entries, max_bytes=max(1_024, max_bytes // 3))
    base = Path(root).resolve()
    sections = [map_text, "\nObjective-focused excerpts:"]
    used = len("\n".join(sections).encode("utf-8"))
    for entry in rank_entries(entries, objective):
        candidate = base / entry.path
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        section = f"\n--- {entry.path} ---\n{content}"
        remaining = max_bytes - used
        if remaining <= 0:
            break
        encoded = section.encode("utf-8")
        if len(encoded) > remaining:
            section = encoded[:remaining].decode("utf-8", errors="ignore")
        sections.append(section)
        used += len(section.encode("utf-8"))
    return "\n".join(sections)[:max_bytes], map_text
