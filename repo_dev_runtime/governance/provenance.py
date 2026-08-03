"""Machine-readable provenance for reviewed capability extraction."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceComponent:
    source_repository: str
    capability: str
    adaptation: str
    source_commit: str = ""
    source_path: str = ""
    source_url: str = ""
    license: str = ""
    status: str = "reviewed"
    excluded_reason: str = ""

    def validate(self) -> None:
        required = (self.source_repository, self.capability, self.adaptation)
        if any(not value.strip() for value in required):
            raise ValueError("provenance component fields are required")
        if self.status not in {"reviewed", "excluded", "reference"}:
            raise ValueError("invalid provenance status")
        if self.status == "excluded" and not self.excluded_reason.strip():
            raise ValueError("excluded components require a reason")
        if self.status == "reference" and not self.source_url.strip():
            raise ValueError("reference components require a source_url")

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return asdict(self)


def load_provenance(path: str | Path) -> list[SourceComponent]:
    payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "RepoDev.Provenance.v1":
        raise ValueError("unsupported provenance schema")
    components = [SourceComponent(**item) for item in payload.get("components", [])]
    for component in components:
        component.validate()
    return components
