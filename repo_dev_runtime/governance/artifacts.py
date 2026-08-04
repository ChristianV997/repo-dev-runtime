"""Immutable run envelope and artifact checksum helpers."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .credentials import redact_json


# Bounds mirroring this codebase's other explicit external-facing caps
# (scheduler.TaskStateStore._MAX_STATE_BYTES, runtimes.aider's
# _MAX_INPUT_BYTES, tools.runner's max_output_bytes): events.jsonl is
# append-only and grows across every --resume of the same run, and a run
# directory's total artifact size has no other bound. Both are generous
# for realistic volume (5 roles, <=3 repair attempts) and exist only to
# fail closed rather than let either grow silently unbounded.
_MAX_EVENTS_BYTES = 5_000_000
_MAX_RUN_ARTIFACT_BYTES = 50_000_000


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
        self.root = Path(self.root).expanduser().resolve()
        event_path = self.root / "events.jsonl"
        if event_path.is_symlink():
            raise ValueError("run event log must not be a symlink")
        if not self.events and event_path.exists():
            for line in event_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.events.append(json.loads(line))
        self._validate_events()

    def _validate_events(self) -> None:
        for sequence, event in enumerate(self.events):
            if not isinstance(event, dict) or event.get("sequence") != sequence:
                raise ValueError("run event log sequence is invalid")
            if not isinstance(event.get("event"), str) or not isinstance(event.get("data"), dict):
                raise ValueError("run event log shape is invalid")
            expected_hash = _event_hash(sequence, event["event"], event["data"])
            if event.get("event_hash") != expected_hash:
                raise ValueError("run event log hash mismatch")

    def event(self, name: str, **data: Any) -> None:
        safe_data = redact_json(data)
        self.root.mkdir(parents=True, exist_ok=True)
        event_path = self.root / "events.jsonl"
        if event_path.is_symlink():
            raise ValueError("run event log must not be a symlink")
        sequence = len(self.events)
        event = {
            "sequence": sequence,
            "event": name,
            "data": safe_data,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        event["event_hash"] = _event_hash(sequence, name, safe_data)
        line = json.dumps(event, sort_keys=True, allow_nan=False) + "\n"
        existing_size = event_path.stat().st_size if event_path.exists() else 0
        if existing_size + len(line.encode("utf-8")) > _MAX_EVENTS_BYTES:
            raise ValueError(f"run event log exceeds maximum size of {_MAX_EVENTS_BYTES} bytes")
        self.events.append(event)
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(line)

    def write_json(self, name: str, payload: Any) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._artifact_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        return path

    def _artifact_path(self, name: str) -> Path:
        """Resolve an artifact name without permitting envelope escape."""
        if not isinstance(name, str) or not name or "\\" in name:
            raise ValueError("artifact name must be a non-empty relative POSIX path")
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts or str(relative) != name:
            raise ValueError("artifact name must be a canonical relative path")
        candidate = self.root / name
        current = candidate
        while current != self.root:
            if current.is_symlink():
                raise ValueError("run artifact path must not contain symlinks")
            current = current.parent
        path = candidate.resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("artifact path escapes run envelope")
        return path

    def finalize(self, payload: dict[str, Any]) -> Path:
        envelope = self.write_json("manifest.json", payload)
        checksums: dict[str, str] = {}
        total_bytes = 0
        for current, directories, filenames in os.walk(self.root, topdown=True, followlinks=False):
            current_path = Path(current)
            for directory in directories:
                if (current_path / directory).is_symlink():
                    raise ValueError("run envelope cannot contain symlinks")
            for filename in filenames:
                path = current_path / filename
                if path.is_symlink():
                    raise ValueError("run envelope cannot contain symlinks")
                if not path.is_file():
                    continue
                total_bytes += path.stat().st_size
                name = path.relative_to(self.root).as_posix()
                if name != "checksums.json":
                    checksums[name] = sha256_file(path)
        if total_bytes > _MAX_RUN_ARTIFACT_BYTES:
            raise ValueError(f"run envelope exceeds maximum size of {_MAX_RUN_ARTIFACT_BYTES} bytes")
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
        if checksum_path.is_symlink() or not checksum_path.exists():
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
            path = self._artifact_path(name)
            if not path.is_file() or sha256_file(path) != digest:
                raise ValueError(f"run envelope artifact checksum mismatch: {name}")


def _event_hash(sequence: int, name: str, data: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps({"sequence": sequence, "event": name, "data": data}, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()
