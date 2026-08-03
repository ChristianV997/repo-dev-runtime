"""Immutable run envelope and artifact checksum helpers."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class RunEnvelope:
    run_id: str
    root: Path
    events: list[dict[str, Any]] = field(default_factory=list)

    def event(self, name: str, **data: Any) -> None:
        sequence = len(self.events)
        event = {
            "sequence": sequence,
            "event": name,
            "data": data,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        event["event_hash"] = hashlib.sha256(
            json.dumps({"sequence": sequence, "event": name, "data": data}, sort_keys=True, allow_nan=False).encode("utf-8")
        ).hexdigest()
        self.events.append(event)
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")

    def write_json(self, name: str, payload: Any) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        return path

    def finalize(self, payload: dict[str, Any]) -> Path:
        envelope = self.write_json("manifest.json", payload)
        checksums = {p.name: sha256_file(p) for p in self.root.iterdir() if p.is_file() and p.name != "checksums.json"}
        self.write_json("checksums.json", checksums)
        return envelope
