from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .manifest import load_manifest
from .discovery import probe_repository
from .runtimes.ollama import OllamaRuntime
from .runtimes.openai_compatible import OpenAICompatibleRuntime


def main() -> int:
    parser = argparse.ArgumentParser(prog="repo-dev-runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("path", nargs="?", default=".")
    init = sub.add_parser("init-manifest")
    init.add_argument("path", nargs="?", default=".")
    health = sub.add_parser("health")
    health.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "probe":
        root = Path(args.path).resolve()
        manifest = load_manifest(root)
        print(json.dumps({"manifest": manifest.to_dict(), "capabilities": probe_repository(root)}, indent=2))
        return 0
    if args.command == "init-manifest":
        print(json.dumps(load_manifest(Path(args.path), create_default=True).to_dict(), indent=2))
        return 0
    health = {"ollama": OllamaRuntime().health().__dict__, "omniroute": OpenAICompatibleRuntime().health().__dict__}
    print(json.dumps(health, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
