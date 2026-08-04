"""Shared, conservative relative-path policy checks."""
from __future__ import annotations

from pathlib import PurePosixPath


def path_allowed(
    relative: str,
    allowed_paths: tuple[str, ...],
    forbidden_paths: tuple[str, ...],
    *,
    empty_allowed: bool = True,
) -> bool:
    """Return whether a canonical relative path is inside the policy scope.

    Path comparisons are case-insensitive so policy behavior is stable across
    Windows and POSIX filesystems. Callers that require an explicit allowlist
    can set ``empty_allowed=False``.
    """
    normalized = relative.replace("\\", "/").casefold()
    parsed = PurePosixPath(normalized)
    if not normalized or parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
        return False
    parts = set(parsed.parts)
    if any(
        item.casefold() in parts or normalized.startswith(item.casefold().rstrip("/") + "/")
        for item in forbidden_paths
    ):
        return False
    if not allowed_paths:
        return empty_allowed
    if any(item.casefold() == "." for item in allowed_paths):
        return True
    return any(
        normalized == item.casefold().rstrip("/")
        or normalized.startswith(item.casefold().rstrip("/") + "/")
        for item in allowed_paths
    )
