"""Bounded, read-only repository context for model prompts."""
from __future__ import annotations

import os
from pathlib import Path

from .path_policy import path_allowed
from .repository_map import MAX_MAP_FILE_BYTES, SKIP_DIRS, build_repository_map, rank_entries, render_repository_map


_TEXT_SUFFIXES = {".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".ps1", ".css", ".html"}


def _read_text_bounded(path: Path, max_bytes: int) -> str:
    """Read at most ``max_bytes`` while avoiding unbounded prompt input."""
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    return data[:max_bytes].decode("utf-8", errors="ignore")


def build_repository_context(root: str | Path, *, allowed_paths: tuple[str, ...], forbidden_paths: tuple[str, ...], max_bytes: int = 80_000) -> str:
    """Return a deterministic bounded snapshot; never executes repository code."""
    if max_bytes < 1_024:
        raise ValueError("context limit is too small")
    base = Path(root).resolve()
    files: list[Path] = []
    for current, directories, filenames in os.walk(base, topdown=True, followlinks=False):
        directories[:] = sorted(
            directory for directory in directories
            if directory.casefold() not in SKIP_DIRS
            and not (Path(current) / directory).is_symlink()
        )
        for filename in sorted(filenames):
            candidate = Path(current) / filename
            if candidate.is_symlink() or candidate.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            relative = candidate.relative_to(base).as_posix()
            if not path_allowed(relative, allowed_paths, forbidden_paths):
                continue
            try:
                if candidate.stat().st_size > MAX_MAP_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(candidate)
    sections = [f"Repository root: {base}", "Files:"]
    used = sum(len(item.encode("utf-8")) for item in sections) + 1
    for candidate in files:
        relative = candidate.relative_to(base).as_posix()
        try:
            remaining = max_bytes - used
            if remaining <= 0:
                break
            content = _read_text_bounded(candidate, min(MAX_MAP_FILE_BYTES, remaining))
        except OSError:
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
            remaining = max_bytes - used
            if remaining <= 0:
                break
            content = _read_text_bounded(candidate, min(MAX_MAP_FILE_BYTES, remaining))
        except OSError:
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
