"""Structured, non-authoritative reviewer verdicts for promotion gates."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


SCHEMA = "RepoDev.ReviewVerdict.v1"


class ReviewValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    path: str
    message: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReviewFinding":
        if set(raw) - {"severity", "path", "message"}:
            raise ReviewValidationError("invalid finding fields")
        item = cls(str(raw.get("severity", "")), str(raw.get("path", "")), str(raw.get("message", "")))
        if item.severity not in {"critical", "high", "medium", "low"} or not item.message:
            raise ReviewValidationError("invalid review finding")
        return item


@dataclass(frozen=True)
class ReviewVerdict:
    approved: bool
    summary: str
    findings: tuple[ReviewFinding, ...] = ()
    schema: str = SCHEMA

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReviewVerdict":
        if not isinstance(raw, Mapping) or set(raw) - {"schema", "approved", "summary", "findings"}:
            raise ReviewValidationError("invalid review fields")
        if raw.get("schema", SCHEMA) != SCHEMA or not isinstance(raw.get("approved"), bool) or not isinstance(raw.get("summary"), str):
            raise ReviewValidationError("invalid review verdict")
        findings = raw.get("findings", [])
        if not isinstance(findings, list):
            raise ReviewValidationError("findings must be a list")
        return cls(raw["approved"], raw["summary"], tuple(ReviewFinding.from_dict(x) for x in findings))

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"findings": [asdict(x) for x in self.findings]}


def parse_review_verdict(output: str) -> ReviewVerdict:
    text = output.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
    try:
        return ReviewVerdict.from_dict(json.loads(text))
    except json.JSONDecodeError as exc:
        raise ReviewValidationError("reviewer output is not valid JSON") from exc
