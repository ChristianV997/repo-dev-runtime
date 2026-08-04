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

    def __post_init__(self) -> None:
        event_path = self.root / "events.jsonl"
        if not self.events and event_path.exists():
            for line in event_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.events.append(json.loads(line))

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

    def verify_checksums(self, *, required: tuple[str, ...] = ()) -> None:
        """Fail closed when a finalized envelope or required artifact changed.

        Resumption consumes prior artifacts as executable evidence, so merely
        finding a JSON file is insufficient. Files created after a previous
        finalize are allowed; callers may verify only the artifacts they will
        execute, because resumption itself appends to the event log.
        """
        checksum_path = self.root / "checksums.json"
        if not checksum_path.exists():
            raise ValueError("run envelope checksums are unavailable")
        payload = json.loads(checksum_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not all(isinstance(name, str) and isinstance(digest, str) for name, digest in payload.items()):
            raise ValueError("run envelope checksums are invalid")
        names = required or tuple(payload)
        for name in names:
            if name not in payload:
                raise ValueError(f"required artifact is not checksum-covered: {name}")
        for name in names:
            digest = payload[name]
            path = self.root / name
            if not path.is_file() or sha256_file(path) != digest:
                raise ValueError(f"run envelope artifact checksum mismatch: {name}")
