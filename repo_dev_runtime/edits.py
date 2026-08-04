"""Validated, atomic edit proposals for disposable repository worktrees."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .contracts.models import now_iso, sha256_json
from .path_policy import path_allowed

SCHEMA = "RepoDev.EditProposal.v1"
FORMATS = {"search_replace", "whole_file"}
MAX_FILE_BYTES = 1_000_000
_HASH = re.compile(r"^[0-9a-f]{64}$")


class PatchValidationError(ValueError):
    pass


def _text(value: Any, name: str, *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise PatchValidationError(f"{name} is required")
    return value


def _path(value: Any) -> str:
    value = _text(value, "path")
    if "\\" in value or "\x00" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise PatchValidationError("path must be a relative POSIX path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts or value != str(parsed):
        raise PatchValidationError("path traversal or non-canonical path")
    return value


@dataclass(frozen=True)
class FileEdit:
    path: str
    format: str
    search: str = ""
    replace: str = ""
    content: str = ""
    expected_file_hash: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FileEdit":
        if not isinstance(raw, Mapping) or set(raw) - {"path", "format", "search", "replace", "content", "expected_file_hash"}:
            raise PatchValidationError("invalid file edit fields")
        item = cls(path=_path(raw.get("path")), format=_text(raw.get("format"), "format"),
                   search=raw.get("search", ""), replace=raw.get("replace", ""),
                   content=raw.get("content", ""), expected_file_hash=raw.get("expected_file_hash", ""))
        if item.format not in FORMATS:
            raise PatchValidationError("unsupported edit format")
        if not all(isinstance(x, str) for x in (item.search, item.replace, item.content, item.expected_file_hash)):
            raise PatchValidationError("edit values must be strings")
        if item.format == "search_replace" and not item.search:
            raise PatchValidationError("search_replace requires search")
        if item.format == "whole_file" and not item.content:
            raise PatchValidationError("whole_file requires content")
        if item.expected_file_hash and not _HASH.fullmatch(item.expected_file_hash):
            raise PatchValidationError("expected_file_hash must be lowercase SHA-256")
        if len(item.content.encode()) > MAX_FILE_BYTES or len(item.replace.encode()) > MAX_FILE_BYTES:
            raise PatchValidationError("replacement exceeds size limit")
        return item

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "format": self.format, "search": self.search,
                "replace": self.replace, "content": self.content,
                "expected_file_hash": self.expected_file_hash}


@dataclass(frozen=True)
class EditProposal:
    proposal_id: str
    task_id: str
    base_commit: str
    context_hash: str
    summary: str
    edits: tuple[FileEdit, ...]
    schema: str = SCHEMA
    created_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EditProposal":
        allowed = {"schema", "proposal_id", "task_id", "base_commit", "context_hash", "summary", "edits", "created_at"}
        if not isinstance(raw, Mapping) or set(raw) - allowed:
            raise PatchValidationError("invalid proposal fields")
        if raw.get("schema", SCHEMA) != SCHEMA:
            raise PatchValidationError("unsupported proposal schema")
        edits = raw.get("edits")
        if not isinstance(edits, list) or not edits:
            raise PatchValidationError("edits must be a non-empty list")
        parsed = tuple(FileEdit.from_dict(x) for x in edits)
        if len({x.path for x in parsed}) != len(parsed):
            raise PatchValidationError("duplicate edit paths")
        proposal = cls(proposal_id=_text(raw.get("proposal_id"), "proposal_id"),
                       task_id=_text(raw.get("task_id"), "task_id"),
                       base_commit=_text(raw.get("base_commit"), "base_commit"),
                       context_hash=_text(raw.get("context_hash"), "context_hash"),
                       summary=_text(raw.get("summary"), "summary"), edits=parsed,
                       created_at=raw.get("created_at", now_iso()))
        if not isinstance(proposal.created_at, str):
            raise PatchValidationError("created_at must be a string")
        return proposal

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "proposal_id": self.proposal_id, "task_id": self.task_id,
                "base_commit": self.base_commit, "context_hash": self.context_hash,
                "summary": self.summary, "edits": [x.to_dict() for x in self.edits],
                "created_at": self.created_at}

    @property
    def proposal_hash(self) -> str:
        return sha256_json(self.to_dict())


def parse_edit_proposal(output: str) -> EditProposal:
    """Parse a model response only when it is one complete proposal object."""
    text = output.strip()
    # A fenced block must actually have a body: bare "```"/"``````" satisfies
    # both startswith and endswith, and split("\n", 1)[1] would then raise
    # IndexError - escaping this parser's declared PatchValidationError
    # contract for malformed provider output.
    if text.startswith("```") and text.endswith("```") and "\n" in text:
        text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PatchValidationError("implementer output is not valid proposal JSON") from exc
    return EditProposal.from_dict(payload)


def _git_head(root: Path) -> str:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    if result.returncode:
        raise PatchValidationError("not a git worktree")
    return result.stdout.strip()


@dataclass(frozen=True)
class PatchCheckpoint:
    checkpoint_id: str
    root: str
    files: Mapping[str, bytes | None]
    modes: Mapping[str, int]


@dataclass(frozen=True)
class AppliedPatch:
    proposal_hash: str
    checkpoint_id: str
    changed_files: tuple[str, ...]
    before_hashes: Mapping[str, str]
    after_hashes: Mapping[str, str]


# Shared with eval/harness.py's worktree_escapes_detected/_FORBIDDEN_MARKERS
# classification, so the two modules can't silently drift apart on wording.
WORKTREE_ESCAPE_MESSAGE = "edit escapes worktree"


class PatchApplier:
    def __init__(self, root: str | Path, *, allowed_paths: tuple[str, ...] = (), forbidden_paths: tuple[str, ...] = ()) -> None:
        self.root = Path(root).resolve()
        self.allowed_paths = allowed_paths
        self.forbidden_paths = tuple(x.lower() for x in forbidden_paths)
        self._checkpoints: dict[str, PatchCheckpoint] = {}

    def _resolve(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if self.root not in path.parents:
            raise PatchValidationError(WORKTREE_ESCAPE_MESSAGE)
        # Enforce policy on the *resolved* location, not the literal string
        # the proposal asked for. `.resolve()` follows symlinks, so checking
        # `relative` lets a symlink inside the worktree redirect a write
        # past allowed_paths/forbidden_paths: with `pub -> secrets` present,
        # a proposal editing "pub/creds.txt" passes a check against "pub/..."
        # while actually writing "secrets/creds.txt". The escape guard above
        # only catches redirection *out of* the worktree, not within it.
        effective = path.relative_to(self.root).as_posix()
        shown = relative if effective == relative else f"{relative} -> {effective}"
        # A manifest's "." scope means the repository root. Auto-detected
        # manifests intentionally use it to permit normal in-worktree edits.
        if self.allowed_paths and not path_allowed(effective, self.allowed_paths, ()):
            raise PatchValidationError(f"path is outside allowed_paths: {shown}")
        if not path_allowed(effective, (), self.forbidden_paths):
            raise PatchValidationError(f"forbidden path: {shown}")
        return path

    def validate(self, proposal: EditProposal, *, context_hash: str = "") -> None:
        if _git_head(self.root) != proposal.base_commit:
            raise PatchValidationError("base commit mismatch")
        if context_hash and context_hash != proposal.context_hash:
            raise PatchValidationError("context hash mismatch")
        for edit in proposal.edits:
            target = self._resolve(edit.path)
            current = target.read_bytes() if target.exists() else None
            if edit.expected_file_hash:
                if current is None or hashlib.sha256(current).hexdigest() != edit.expected_file_hash:
                    raise PatchValidationError(f"file hash mismatch: {edit.path}")
            if current is None and edit.format == "search_replace":
                raise PatchValidationError(f"file missing: {edit.path}")
            if current is not None and len(current) > MAX_FILE_BYTES:
                raise PatchValidationError(f"file exceeds size limit: {edit.path}")

    def apply(self, proposal: EditProposal, *, context_hash: str = "") -> AppliedPatch:
        self.validate(proposal, context_hash=context_hash)
        originals: dict[str, bytes | None] = {}
        modes: dict[str, int] = {}
        rendered: dict[str, bytes] = {}
        for edit in proposal.edits:
            target = self._resolve(edit.path)
            originals[edit.path] = target.read_bytes() if target.exists() else None
            modes[edit.path] = target.stat().st_mode if target.exists() else 0o644
            old = originals[edit.path].decode("utf-8") if originals[edit.path] is not None else ""
            if edit.format == "search_replace":
                if old.count(edit.search) != 1:
                    raise PatchValidationError(f"search must match exactly once: {edit.path}")
                new = old.replace(edit.search, edit.replace, 1)
            else:
                new = edit.content
            rendered[edit.path] = new.encode("utf-8")
        checkpoint = PatchCheckpoint(proposal.proposal_id, str(self.root), originals, modes)
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        try:
            for relative, data in rendered.items():
                target = self._resolve(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                    handle.write(data)
                    temporary = Path(handle.name)
                os.chmod(temporary, modes[relative])
                os.replace(temporary, target)
        except Exception:
            self.rollback(checkpoint)
            raise
        return AppliedPatch(proposal.proposal_hash, checkpoint.checkpoint_id, tuple(rendered),
                            {p: _digest(originals[p]) for p in rendered},
                            {p: _digest(rendered[p]) for p in rendered})

    def rollback_checkpoint(self, checkpoint_id: str) -> None:
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise PatchValidationError("unknown checkpoint")
        self.rollback(checkpoint)

    def rollback(self, checkpoint: PatchCheckpoint) -> None:
        if Path(checkpoint.root).resolve() != self.root:
            raise PatchValidationError("checkpoint root mismatch")
        for relative, data in checkpoint.files.items():
            target = self._resolve(relative)
            if data is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                os.chmod(target, checkpoint.modes[relative])


def _digest(data: bytes | None) -> str:
    return "" if data is None else hashlib.sha256(data).hexdigest()
