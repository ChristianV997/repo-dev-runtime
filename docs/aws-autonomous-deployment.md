# Continuous Autonomous Operation on AWS

A concrete deployment design for running `repo-dev-runtime` unattended,
on a fixed cadence, against a target repository — including against
`repo-dev-runtime` itself. It composes the existing `run` CLI, the atomic
`--scheduler-state-file`/`--schedule-key` primitives
(`repo_dev_runtime/scheduler.py`'s `TaskStateStore.claim()`), and the
OpenRouter-primary/Ollama-backup routing described in README.md's
"OpenRouter as primary, Ollama as backup" section.

**This design is now also real, applicable Terraform**: `infra/aws/`
implements everything below — EC2 instance, IAM role/instance profile,
Secrets Manager containers, and the cloud-init bootstrap that installs
the wrapper script, reaper, and `systemd` unit/timer — as a module you
apply against your own AWS account. See `infra/aws/README.md` for the
exact `terraform apply` steps. This doc remains the design rationale;
that module is the runnable implementation of it.

## Architecture

A single small EC2 instance (a `t3.small`/`t3.medium` is enough — the
runtime itself does no heavy local compute; the model backend is remote)
running one `systemd` timer that invokes the CLI on a fixed cadence:

```
EC2 instance
├── /opt/repo-dev-runtime/            (this package, pip-installed)
├── /opt/repo-dev-runtime/target/     (clone of the target repository)
├── /var/lib/repo-dev-runtime/
│   ├── state.json                    (TaskStateStore file)
│   └── runs/                         (--artifacts-root)
├── run-once.sh                       (the wrapper script below)
└── systemd unit + timer               (invokes run-once.sh on schedule)
```

### `systemd` timer (the OS scheduler)

Per README.md: "An OS scheduler supplies the cadence; this runtime
deliberately does not run a daemon or make recurring, unbounded decisions
itself." A timer, not a loop inside the runtime, is the correct fit:

```ini
# /etc/systemd/system/repo-dev-runtime.timer
[Unit]
Description=repo-dev-runtime scheduled autonomous pass

[Timer]
OnCalendar=*-*-* */2:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/repo-dev-runtime.service
[Unit]
Description=repo-dev-runtime scheduled autonomous pass

[Service]
Type=oneshot
User=repo-dev-runtime
ExecStart=/opt/repo-dev-runtime/run-once.sh
```

### Secrets: AWS Secrets Manager / SSM Parameter Store, not a plaintext env file

`GITHUB_TOKEN`, `DEV_OMNIROUTE_TOKEN` (the `OPENROUTER_API_KEY`), and any
Ollama backend URL should live in Secrets Manager or SSM Parameter
Store, fetched at invocation time — not baked into the AMI or
`EnvironmentFile`. Grant the instance's IAM role read-only access to
exactly those secret ARNs (least privilege), and the wrapper script
resolves them just before invoking the CLI:

```bash
#!/usr/bin/env bash
# run-once.sh
set -euo pipefail

export GITHUB_TOKEN=$(aws secretsmanager get-secret-value --secret-id repo-dev-runtime/github-token --query SecretString --output text)
export DEV_OMNIROUTE_TOKEN=$(aws secretsmanager get-secret-value --secret-id repo-dev-runtime/openrouter-key --query SecretString --output text)
export DEV_OMNIROUTE_URL=https://openrouter.ai/api/v1
export DEV_OMNIROUTE_MODEL=anthropic/claude-3.5-sonnet
export DEV_OMNIROUTE_ENABLED=true
export OLLAMA_URL=$(aws ssm get-parameter --name /repo-dev-runtime/ollama-url --query Parameter.Value --output text)
export DEV_RUNTIME_OLLAMA=true

STATE_FILE=/var/lib/repo-dev-runtime/state.json
STUCK_TIMEOUT_S=1800

# Reap a claim left at "running" by a crash or a killed instance, via
# TaskStateStore.reap_stuck() (see "Known gap" below) rather than a
# hand-rolled unlocked read/write, which could corrupt or lose a
# concurrent claim()/update() call.
python3 -c "
from repo_dev_runtime.scheduler import TaskStateStore
TaskStateStore('$STATE_FILE').reap_stuck('nightly-review', timeout_s=$STUCK_TIMEOUT_S)
"

cd /opt/repo-dev-runtime/target
git fetch origin main --quiet
git checkout main --quiet
git reset --hard origin/main --quiet

python3 -m repo_dev_runtime.cli run . \
  --prompt "Review open issues and propose the highest-value fix" \
  --base-ref main \
  --live --enable-omniroute --enable-ollama --approve-paid \
  --apply-edits --create-pr \
  --scheduler-state-file "$STATE_FILE" \
  --schedule-key nightly-review \
  --artifacts-root /var/lib/repo-dev-runtime/runs
```

### The known gap: a crashed run leaves a claim stuck at `"running"`

`TaskStateStore.claim()` is atomic (single-lock compare-and-set) but
performs no reaping on its own — if the instance is killed or the
process crashes mid-run, the schedule key stays `"running"` forever and
every subsequent scheduled fire is silently skipped.
`TaskStateStore.reap_stuck(task_id, *, stuck_status="running",
timeout_s)` (`repo_dev_runtime/scheduler.py`) closes this: it checks the
state file's own mtime (the only available proxy for "when was this
claim last written", since no per-entry timestamp exists in the
schema) and clears a stuck entry — all inside the same lock
`claim()`/`update()` use, so it can never corrupt or lose a concurrent
write the way a hand-rolled unlocked read-then-write would. Call it from
the wrapper (as in `run-once.sh` above) before every `claim()`-based
`run` invocation. If a single state file is ever shared across multiple
`--schedule-key` values, use a per-key state file instead — the mtime
proxy is only exact for a file dedicated to one key. Size `timeout_s`
comfortably above the manifest's `check_timeout_s` plus provider call
overhead so a merely slow run isn't reaped while still legitimately in
progress.

### Every invocation needs `--approve-paid`

Because OpenRouter (`openai_compatible`) is now the first routing
preference for every role, omitting `--approve-paid` in the wrapper
above makes every scheduled run silently fall back to Ollama (or fail
closed with `no_authorized_runtime` if Ollama is also unreachable) —
this is deliberate per README.md's routing section, but worth a comment
in the wrapper script itself so a future edit doesn't drop the flag
without noticing the behavior change.

### Artifact and log retention

Point `--artifacts-root` at a dedicated EBS volume (or sync periodically
to S3 with a lifecycle rule) rather than the root volume — each run
writes bounded but real artifacts (`governance/artifacts.py`'s
`_MAX_RUN_ARTIFACT_BYTES`/`_MAX_EVENTS_BYTES` bound each individual run,
but they accumulate across every scheduled fire). A simple retention
policy: keep the last N runs' directories, or sync-and-delete anything
older than a fixed window via a second, less-frequent `systemd` timer
running `aws s3 sync /var/lib/repo-dev-runtime/runs
s3://your-bucket/repo-dev-runtime-runs/ && find
/var/lib/repo-dev-runtime/runs -mtime +30 -delete`.

## What this design deliberately does not do

- No background daemon inside `repo-dev-runtime` itself — the cadence is
  entirely the `systemd` timer's responsibility, matching the existing
  "Scheduling is declarative and one-shot" design in README.md.
- No automatic PR merging — `--create-pr` only publishes a `repo-dev/*`
  branch and opens a PR; a human still reviews and merges, exactly as
  documented for every other invocation of this flag.
- No in-repo Dockerfile/AMI/Terraform is checked in by this doc — this
  is a design to adapt to the user's own AWS conventions, not a
  ready-to-apply artifact. Decide separately whether to commit
  infrastructure-as-code for it.

## Verification before relying on this in production

1. Run the wrapper script manually once on a real EC2 instance (or
   locally) against a real throwaway target repository, confirming the
   secrets resolve, `--approve-paid` routes to OpenRouter when reachable
   and falls back to Ollama when it isn't, and a real PR is opened.
2. Kill the process mid-run deliberately, confirm the schedule key is
   left at `"running"` in `state.json`, then confirm
   `TaskStateStore.reap_stuck()` clears it after the configured timeout
   and the next scheduled fire proceeds instead of skipping.
3. Confirm IAM permissions are least-privilege: the instance role should
   only read the specific secret ARNs it needs, and the `GITHUB_TOKEN`
   should be scoped to `repo` only, per `docs/credential-policy.md`.
