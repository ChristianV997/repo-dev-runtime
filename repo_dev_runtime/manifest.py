"""Repository manifest loading and conservative auto-detection."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepoManifest:
    schema: str = "RepoDev.Repository.v1"
    name: str = ""
    root: str = ""
    source_paths: tuple[str, ...] = ()
    test_command: tuple[str, ...] = ("python", "-m", "pytest", "-q")
    lint_command: tuple[str, ...] = ()
    security_command: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = (".env", ".git", "credentials", "secrets")
    agent_roles: tuple[str, ...] = ("planner", "implementer", "tester", "reviewer", "integrator")
    network_access: bool = False
    pull_request_creation: bool = False
    check_timeout_s: float = 600.0
    context_max_bytes: int = 4_096

    def validate(self) -> None:
        if self.schema != "RepoDev.Repository.v1":
            raise ValueError("unsupported repository manifest schema")
        if not self.name.strip() or not self.root.strip():
            raise ValueError("manifest name and root are required")
        if not self.test_command:
            raise ValueError("test_command must not be empty")
        if not 1 <= float(self.check_timeout_s) <= 7_200:
            raise ValueError("check_timeout_s is out of bounds")
        if not 4_096 <= int(self.context_max_bytes) <= 200_000:
            raise ValueError("context_max_bytes is out of bounds")
        valid_roles = {"planner", "implementer", "tester", "reviewer", "integrator"}
        if set(self.agent_roles) - valid_roles:
            raise ValueError("manifest contains an unsupported agent role")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        value = asdict(self)
        for field_name in ("source_paths", "test_command", "lint_command", "security_command", "allowed_paths", "forbidden_paths", "agent_roles"):
            value[field_name] = list(value[field_name])
        return value


def load_manifest(root: str | Path, *, create_default: bool = False) -> RepoManifest:
    path = Path(root).resolve()
    manifest_path = path / ".dev-runtime" / "repository.json"
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("root") in {"", "."}:
            payload["root"] = str(path)
        manifest = RepoManifest(**payload)
        manifest.validate()
        return manifest
    if not create_default:
        return detect_manifest(path)
    manifest = detect_manifest(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")
    return manifest


def detect_manifest(root: Path) -> RepoManifest:
    if (root / "package.json").exists():
        test_command = ("npm", "test")
    elif (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "tests").exists():
        test_command = ("python", "-m", "pytest", "-q")
    else:
        test_command = ("git", "status", "--short")
    manifest = RepoManifest(name=root.name or "repository", root=str(root), source_paths=(".",), test_command=test_command, allowed_paths=(".",))
    manifest.validate()
    return manifest
