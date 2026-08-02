"""One-way, allowlisted Obsidian handoff destination."""
from __future__ import annotations

from pathlib import Path


class ObsidianHandoff:
    def __init__(self, vault_root: str | Path, relative_destination: str = "AI Engineering/Session Handoffs") -> None:
        self.vault_root = Path(vault_root).expanduser().resolve()
        self.destination = (self.vault_root / relative_destination).resolve()
        if self.vault_root not in self.destination.parents:
            raise ValueError("Obsidian destination must remain inside the configured vault")

    def write(self, filename: str, content: str, *, dry_run: bool = True) -> Path:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("invalid handoff filename")
        path = self.destination / safe_name
        if not dry_run:
            self.destination.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return path
