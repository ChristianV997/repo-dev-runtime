# Lint and Type-Checking Policy

## Lint: ruff, scoped to `F` + `E9`

CI runs `ruff check repo_dev_runtime tests scripts` gated on
`select = ["F", "E9"]` (`pyproject.toml`) — pyflakes-equivalent checks
(unused imports/variables, undefined names) plus syntax errors. Running
ruff with no config (its full modern default rule set) surfaced 105
findings, but 91 of them were import-sorting (`I001`), typing
modernization (`UP035`/`UP037`), and style preferences (`BLE001` blind-except,
`C408` collection-call style) — a large, low-value reformatting diff with
no correctness change, across files this repo didn't necessarily author in
their current style. The remaining 14 were genuine (13 unused imports, 1
unused local variable) — all fixed, all auto-fixable, zero behavior change.
`select = ["F", "E9"]` keeps that signal-to-noise ratio going forward
without forcing unrelated stylistic churn on every future change.

## Type-checking: mypy is a manual diagnostic, not a CI gate — by decision, not oversight

Running `mypy repo_dev_runtime --ignore-missing-imports` once (not in CI)
surfaced 13 findings. Investigating each:

- **One was a real bug**: `workflow.py`'s call to
  `GitHubPublisher.create_from_worktree` omitted the required
  `allowed_paths`/`forbidden_paths` keyword arguments — a live
  `--create-pr` run would have raised `TypeError` immediately, silently
  caught by workflow.py's own exception handler and reported as a generic
  `blocked`/`pr_creation_failed` result rather than surfacing the real
  cause. Zero test coverage existed for `create_pr=True` at all. Fixed,
  with a regression test (`tests/test_tools_workflow.py::test_create_pr_workflow_calls_publisher_with_correct_signature`)
  proven to fail against the old code and pass against the fix.
- **The other 12 are not real bugs** — reproduced in isolation
  (`(x or os.getenv("K", "default")).rstrip("/")` with `x: str | None`):
  mypy does not narrow an `Optional` value through the `or`-fallback idiom
  the way it narrows an explicit `if x is None: x = default`. This is a
  known mypy inference limitation, not incorrect code — the runtime value
  is always a `str` in every one of these cases. The other 2 non-bug
  findings are a `tuple[str, ...]`-widening false positive in
  `manifest.detect_manifest` (mypy infers a narrow tuple-length type from
  the first branch's literal) and two harmless variable-name reuses in
  `cli.py`.

Given that ratio — 1 real, fixed bug against 12 findings that would each
need either a stylistic rewrite of already-correct code or a
`# type: ignore` comment to silence — adding mypy as a blocking CI gate
right now would trade a large amount of low-value churn for a
one-time-already-captured signal. The call: fix the real bug, don't add
mypy to CI yet. Re-run `mypy repo_dev_runtime --ignore-missing-imports`
manually after significant changes to catch the next `create_from_worktree`-shaped
bug; revisit CI adoption if a real annotation strategy (explicit
`str`-typed locals instead of the `or`-fallback idiom) is adopted broadly
enough that the false-positive rate drops.
