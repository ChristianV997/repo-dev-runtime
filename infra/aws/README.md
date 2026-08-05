# AWS Terraform: Continuous Autonomous Operation

Applies the design in `docs/aws-autonomous-deployment.md` as real,
runnable Terraform: one EC2 instance running a `systemd` timer that
invokes `repo-dev-runtime run` on a fixed cadence against a target
repository, with `GITHUB_TOKEN`/`OPENROUTER_API_KEY` in Secrets Manager
rather than a plaintext env file, and a reaper script that clears a
`--schedule-key` claim left stuck at `"running"` by a crashed run.

This module creates infrastructure in **your own AWS account** — nothing
here is applied automatically by any CI or agent. Read it before running
`terraform apply`.

## What this creates

- One EC2 instance (Amazon Linux 2023, `t3.small` by default) with
  egress-only networking (SSH is off unless you explicitly set
  `key_name` and `allowed_ssh_cidr`).
- An IAM role/instance profile scoped to `secretsmanager:GetSecretValue`
  on exactly two secret ARNs — nothing broader.
- Two empty Secrets Manager secret containers (`github_token`,
  `openrouter_key`) — **Terraform never writes a real secret value into
  either one**, so no token ever enters Terraform state or this repo.
- A cloud-init script (`templates/user_data.sh.tftpl`) that, on first
  boot: installs `repo-dev-runtime` into a venv, clones the target
  repository, writes `run-once.sh`/`reap_stuck_claim.py`, and installs +
  enables a `systemd` service/timer pair.

## Apply

```bash
cd infra/aws
terraform init
terraform apply \
  -var 'target_repo_url=https://github.com/<you>/<your-repo>.git' \
  -var 'target_repo_ref=main'
```

Review every other variable in `variables.tf` — in particular
`schedule_oncalendar` (systemd `OnCalendar` syntax, not cron; default is
every 2 hours), `run_prompt`, and `openrouter_model`.

## Populate the secrets (required, do this after apply)

Terraform only creates the secret/parameter *containers*, printed as
outputs (plus a placeholder `"REPLACE_ME"` value for the SSM parameter
that a subsequent `terraform apply` never overwrites, via
`lifecycle.ignore_changes`). Populate their real values directly, so
they never touch Terraform state or this repository:

```bash
aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw github_token_secret_arn)" \
  --secret-string "ghp_your_token_with_repo_scope"

aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw openrouter_key_secret_arn)" \
  --secret-string "sk-or-your-real-key"

aws ssm put-parameter --overwrite --type SecureString \
  --name "$(terraform output -raw ollama_url_parameter_name)" \
  --value "http://your-real-ollama-endpoint:11434"
```

The `GITHUB_TOKEN` needs `repo` scope; the target repository's `origin`
remote must be a real `github.com` URL for `--create-pr` to work (see
`docs/credential-policy.md`). The Ollama URL is stored as a
`SecureString` because it can embed credentials (e.g. `http://user:pass@host`)
— an earlier revision of this module baked it directly into the
instance's `user_data` as plaintext; `run-once.sh` now fetches it via
`aws ssm get-parameter --with-decryption` at runtime instead.

## Every scheduled run needs `--approve-paid`

`run-once.sh` (baked into the instance by the user-data script) already
passes `--approve-paid` alongside `--enable-omniroute --enable-ollama`,
matching README.md's "OpenRouter as primary, Ollama as backup" section —
OpenRouter (`openai_compatible`) is a paid runtime and is now the first
routing preference for every role, so omitting this flag would make
every scheduled run fall back to Ollama (or fail closed if Ollama is
unreachable too). If you edit the systemd unit or wrapper script
directly on the instance, keep this flag.

## Ollama as the backup

Populate the SSM parameter (see above) with your real, reachable Ollama
endpoint. If you don't run Ollama at all, OpenRouter alone still works;
you simply lose the automatic backup path when OpenRouter is
unreachable, and `run-once.sh`'s `aws ssm get-parameter` call will fail
closed with an empty/placeholder value rather than silently working.

## Verify it worked

```bash
# SSH in only if you set key_name/allowed_ssh_cidr, or use SSM Session Manager
sudo systemctl status repo-dev-runtime.timer
sudo systemctl status repo-dev-runtime.service
sudo journalctl -u repo-dev-runtime.service -n 200
cat /var/lib/repo-dev-runtime/state.json
ls /var/lib/repo-dev-runtime/runs
```

Trigger a run immediately without waiting for the timer, to validate the
whole path end to end:

```bash
sudo systemctl start repo-dev-runtime.service
```

## Artifact retention

`/var/lib/repo-dev-runtime/runs` (the instance's `--artifacts-root`)
accumulates one directory per run. This module does not set up
S3 sync/lifecycle automatically — add a second, less-frequent
`systemd` timer running something like
`aws s3 sync /var/lib/repo-dev-runtime/runs s3://your-bucket/... && find
/var/lib/repo-dev-runtime/runs -mtime +30 -delete` once you've decided on
a retention window and bucket.

## Teardown

```bash
terraform destroy
```

Secrets Manager secrets have a recovery window by default; pass
`--force-delete-without-recovery` via the AWS CLI first if you want them
gone immediately rather than pending deletion.

## What this module deliberately does not do

- No automatic PR merging — `--create-pr` only publishes a `repo-dev/*`
  branch and opens a PR; a human still reviews and merges.
- No background daemon inside `repo-dev-runtime` — the `systemd` timer
  supplies the cadence entirely externally, per README.md's "Scheduling
  is declarative and one-shot" design.
- No multi-instance/HA setup — this is a single small instance for one
  target repository, matching the scope this doc's design was written
  for. A fleet across many repositories is a larger, separate design.
