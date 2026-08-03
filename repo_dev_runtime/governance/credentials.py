"""Credential-safe subprocess environments and output redaction.

Provider-neutral: no credential is ever hardcoded for a specific project,
and nothing here discovers credentials from arbitrary files. Callers pass
an explicit :class:`CredentialAllowlist` naming exactly which env-var
prefixes/names a given external-provider subprocess may see; everything
else in the parent process environment is withheld by default. This is
the same allow-by-prefix shape already used by
``sensors/agent_reach.py``, generalized so new external-provider adapters
(PR-Agent bridge, future eval-only providers) don't each reinvent it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

# Env vars always safe to forward: none of these can carry a secret.
_BASE_SAFE_NAMES = ("PATH", "HOME", "USERPROFILE", "TEMP", "TMP")

# JSON "SomeKeyName": "value" where the key name looks credential-shaped —
# redacts the VALUE (the actual secret), keeping the key name so a reader
# can still see *what* was redacted.
_REDACT_JSON_KV_PATTERN = re.compile(
    r'(?i)("[A-Za-z0-9_]*(?:key|token|secret|password|credential)[A-Za-z0-9_]*"\s*:\s*)"[^"]*"'
)
_REDACT_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s\"']+")
# Plain-text "label: value" / "label=value" lines (log lines, printed env
# vars) whose label names a credential-shaped concept, e.g. "token: abc123".
_REDACT_PLAIN_PATTERN = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:key|token|secret|password|credential)[A-Za-z0-9_]*\s*[:=]\s*)(\S+)"
)
_SENSITIVE_KEY_NAME = re.compile(r"(?i)(key|token|secret|password|credential)")


@dataclass(frozen=True)
class CredentialAllowlist:
    """Declares which environment variables a single provider's subprocess
    may see. Opt-in and provider-specific: an empty ``env_prefixes`` means
    the provider gets no credential-shaped env vars at all, only the
    universally-safe base names.
    """

    provider: str
    env_prefixes: tuple[str, ...] = ()
    extra_names: tuple[str, ...] = _BASE_SAFE_NAMES

    def __post_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("provider is required")


def build_subprocess_env(allowlist: CredentialAllowlist, *, source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a subprocess environment containing only variables the given
    provider is explicitly allowed to see. Never copies the full host
    environment: every key must match one of ``extra_names`` exactly or
    start with one of ``env_prefixes``.
    """
    host = source if source is not None else os.environ
    allowed_prefixes = tuple(allowlist.env_prefixes)
    allowed_names = set(allowlist.extra_names)
    return {
        key: value
        for key, value in host.items()
        if key in allowed_names or (allowed_prefixes and key.startswith(allowed_prefixes))
    }


def redact_text(value: str) -> str:
    """Scrub credential-shaped substrings from free-text output (subprocess
    stdout/stderr, error messages, telemetry strings)."""
    text = _REDACT_JSON_KV_PATTERN.sub(r'\1"[REDACTED]"', value)
    text = _REDACT_BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _REDACT_PLAIN_PATTERN.sub(r"\1[REDACTED]", text)
    return text


def redact_json(value: Any) -> Any:
    """Recursively scrub credential-shaped keys/values from a JSON-like
    structure (dict/list/str), for use on artifacts, telemetry mappings,
    and parsed provider output before it is persisted or reported."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY_NAME.search(key) and isinstance(item, str):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_json(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def missing_credential_result(*, provider: str, name: str) -> dict[str, Any]:
    """Canonical shape for a provider call blocked by a missing credential.
    Never raises; callers fold this into their own result contract."""
    return {
        "status": "blocked",
        "error_type": "credential_missing",
        "error_message": f"{provider}: required credential '{name}' is not set",
    }
