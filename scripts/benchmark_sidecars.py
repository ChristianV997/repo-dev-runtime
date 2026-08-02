"""Run a non-persisting Hermes versus DeerFlow benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repo_dev_runtime.benchmark import benchmark_runtimes
from repo_dev_runtime.contracts.models import DevTask
from repo_dev_runtime.runtimes.sidecars import DeerFlowRuntime, HermesRuntime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--live", action="store_true", help="explicitly enable configured sidecars for this process")
    args = parser.parse_args()
    runtimes = {"hermes": HermesRuntime(enabled=args.live), "deerflow": DeerFlowRuntime(enabled=args.live)}
    task = DevTask.create(repository=args.repository, base_ref="HEAD", role="planner", prompt=args.prompt)
    print(json.dumps(benchmark_runtimes(task, runtimes), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
