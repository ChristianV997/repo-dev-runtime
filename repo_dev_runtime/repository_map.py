"""Bounded repository maps and prompt-focused context selection.

The map is deliberately static and read-only: it extracts only path, imports, and
top-level symbols, then ranks files by objective keywords and declared source scope.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path


TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".md", ".toml", ".json", ".yaml", ".yml"}
_SYMBOL = re.compile(r"^\s*(?:class|def|async\s+def|function|export\s+(?:class|function|const))\s+([A-Za-z_][\w]*)", re.MULTILINE)
_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+)|(?:import|from)\s+.*?\s+from\s+['\"]([^'\"]+))", re.MULTILINE)
_WORDS = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass(frozen=True)
class MapEntry:
    path: str
    bytes: int
    symbols: tuple[str, ...]
    imports: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"symbols": list(self.symbols), "imports": list(self.imports)}


def _allowed(relative: str, allowed_paths: tuple[str, ...], forbidden_paths: tuple[str, ...]) -> bool:
    parts = set(Path(relative).parts)
    if any(item in parts or relative.startswith(item.rstrip("/") + "/") for item in forbidden_paths):
        return False
    return "." in allowed_paths or not allowed_paths or any(relative == item or relative.startswith(item.rstrip("/") + "/") for item in allowed_paths)


def build_repository_map(root: str | Path, *, allowed_paths: tuple[str, ...], forbidden_paths: tuple[str, ...], max_files: int = 2_000) -> tuple[MapEntry, ...]:
    base = Path(root).resolve()
    entries: list[MapEntry] = []
    for candidate in sorted(base.rglob("*")):
        if len(entries) >= max_files:
            break
        if not candidate.is_file() or candidate.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = candidate.relative_to(base).as_posix()
        if not _allowed(relative, allowed_paths, forbidden_paths):
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        imports = tuple(sorted({item for groups in _IMPORT.findall(content) for item in groups if item}))
        entries.append(MapEntry(relative, candidate.stat().st_size, tuple(_SYMBOL.findall(content)[:80]), imports[:80]))
    return tuple(entries)


def render_repository_map(entries: tuple[MapEntry, ...], *, max_bytes: int = 16_000) -> str:
    lines = ["Repository map (static, incomplete by design):"]
    for entry in entries:
        symbols = ", ".join(entry.symbols[:12]) or "-"
        imports = ", ".join(entry.imports[:8]) or "-"
        line = f"- {entry.path} ({entry.bytes} bytes) symbols: {symbols}; imports: {imports}"
        if len(("\n".join(lines + [line])).encode("utf-8")) > max_bytes:
            break
        lines.append(line)
    return "\n".join(lines)


def rank_entries(entries: tuple[MapEntry, ...], objective: str) -> tuple[MapEntry, ...]:
    tokens = {item.lower() for item in _WORDS.findall(objective)}
    def score(entry: MapEntry) -> tuple[int, str]:
        haystack = " ".join((entry.path, *entry.symbols, *entry.imports)).lower()
        return (sum(token in haystack for token in tokens), entry.path)
    return tuple(sorted(entries, key=lambda item: (-score(item)[0], score(item)[1])))
