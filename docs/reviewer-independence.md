# Reviewer Independence

`review.py`'s module docstring already establishes that a
`ReviewVerdict` is **non-authoritative** — the governed workflow treats
`approved=False` as a blocking condition, but a human is still the actual
approver of promotion, and `RuntimePolicy.validate()` permanently forbids
`allow_merge`.

The evaluation layer's `PRAgentReviewAdapter` extends this same principle
to an external reviewer bridge:

- It can only be **invoked** on a diff and objective already produced by
  a separate implementer step — it never reviews code it wrote itself,
  because it has no method to write code at all.
- It has no method to apply edits, merge, push, or create a pull
  request. There is nothing to disable — the capability was never added.
- Its raw subprocess output is stored separately (`EvalResult.raw_output`)
  from its normalized verdict (`EvalResult.normalized`), so a malformed
  or suspicious response is auditable even when it's rejected.
- Malformed or unparseable output **fails closed**: it becomes a
  `status="failed"` `EvalResult`, never a `ReviewVerdict` — a broken
  bridge cannot accidentally toggle `approved=True` and skip review.
- It is disabled by default (`DEV_RUNTIME_PR_AGENT`); the benchmark
  harness's `reviewer_should_reject` fixture demonstrates it correctly
  producing a rejection.

Why external providers cannot promote or merge: promotion in the
governed workflow requires `run_quality_checks(...)["status"] == "passed"`
*and* a `ReviewVerdict.approved == True` from the workflow's own reviewer
role, and even then the terminal state is `ready_for_human_review` or, if
`--create-pr` is explicitly requested with a live publisher, `pr_created`
— never a merge. `RuntimePolicy.validate()` raises if `allow_merge` is
ever set `True`, unconditionally. No new capability introduced by the
evaluation layer touches that check.
