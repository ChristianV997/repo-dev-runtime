"""Role-specific structured-output contracts for model runtimes.

The workflow validates every proposal and review independently.  These
schemas reduce avoidable malformed output at the provider boundary; they do
not grant a model authority to bypass those validators.
"""
from __future__ import annotations

from typing import Any


_EDIT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "format"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "format": {"type": "string", "enum": ["whole_file", "search_replace"]},
        "content": {"type": "string"},
        "search": {"type": "string"},
        "replace": {"type": "string"},
        "expected_file_hash": {"type": "string"},
    },
}

_PROPOSAL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "proposal_id", "task_id", "base_commit", "context_hash", "summary", "edits"],
    "properties": {
        "schema": {"const": "RepoDev.EditProposal.v1"},
        "proposal_id": {"type": "string", "minLength": 1},
        "task_id": {"type": "string", "minLength": 1},
        "base_commit": {"type": "string", "minLength": 1},
        "context_hash": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "edits": {"type": "array", "minItems": 1, "items": _EDIT},
        "created_at": {"type": "string"},
    },
}

_REVIEW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "approved", "summary", "findings"],
    "properties": {
        "schema": {"const": "RepoDev.ReviewVerdict.v1"},
        "approved": {"type": "boolean"},
        "summary": {"type": "string", "minLength": 1},
        "findings": {"type": "array", "items": {"type": "object"}},
    },
}


def schema_for_role(role: str) -> dict[str, Any] | None:
    """Return a JSON Schema only for roles that must emit a contract."""
    if role == "implementer":
        return _PROPOSAL
    if role == "reviewer":
        return _REVIEW
    return None


def ollama_format(role: str) -> dict[str, Any] | None:
    """Ollama accepts a JSON Schema directly in its ``format`` field."""
    return schema_for_role(role)


def openai_response_format(role: str) -> dict[str, Any] | None:
    """OpenAI-compatible JSON-schema response format for contract roles."""
    schema = schema_for_role(role)
    if schema is None:
        return None
    return {
        "type": "json_schema",
        "json_schema": {"name": f"repo_dev_{role}", "strict": True, "schema": schema},
    }
