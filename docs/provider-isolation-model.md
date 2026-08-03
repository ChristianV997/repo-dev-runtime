# Provider Isolation Model

Every fixture-benchmark run for a coding-agent provider follows the same
containment model, reusing existing primitives rather than new ones:

1. **Disposable worktree.** `WorktreeManager` creates a fresh worktree
   outside the source fixture repository for every fixture case, and
   removes it unconditionally (`finally` block in
   `eval/harness.run_fixture_case`) — success, failure, or exception.
2. **Source-checkout immutability.** The harness snapshots the source
   fixture repo's `git status --porcelain` before and after each run and
   raises if it changed. The provider only ever sees the worktree copy.
3. **Path enforcement.** `PatchApplier` enforces `allowed_paths`/
   `forbidden_paths` on every edit; a violation is classified
   `safely_rejected`, not silently dropped or retried as if it were a
   normal failure.
4. **No push, merge, or PR — structurally, not just by policy.** The
   `eval/` package never imports `integrations.github`. A provider
   cannot push or open a pull request from inside this harness even if
   it wanted to, independent of `RuntimePolicy`.
5. **Reviewer independence.** The `PRAgentReviewAdapter` can only return
   an opinion (`EvalResult` → `RepoDev.ReviewVerdict.v1`); it has no
   method to apply edits, merge, push, or create a PR, and it never
   reviews its own output — it is invoked, if at all, on the diff
   produced by a separate implementer step. See
   `docs/reviewer-independence.md`.
6. **Credentials are opt-in and provider-scoped.** See
   `docs/credential-policy.md`.
7. **Nothing enters default routing.** No new provider evaluated here
   (PR-Agent, RepoAgent, Tree-sitter, OpenHands, mini-SWE-agent) is
   registered in `runtimes/factory.default_registry()` or added to
   `runtimes/registry.RoutingPolicy`. Enabling any of them requires an
   explicit CLI flag on the `benchmark` subcommand; none of them can be
   reached through `repo-dev-runtime run`.
