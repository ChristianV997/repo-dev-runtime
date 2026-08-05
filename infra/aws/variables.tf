variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type. The runtime itself is stdlib-only and does no local compute; the model backend is remote, so a small instance is enough."
  type        = string
  default     = "t3.small"
}

variable "key_name" {
  description = "Existing EC2 key pair name for SSH access. Leave empty to disable SSH entirely (recommended once the deployment is validated)."
  type        = string
  default     = ""
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to SSH in, only used when key_name is set. Never leave this as 0.0.0.0/0 in a real deployment."
  type        = string
  default     = ""
}

variable "target_repo_url" {
  description = "Git URL of the repository repo-dev-runtime should develop continuously (e.g. https://github.com/christianv997/repo-dev-runtime.git)."
  type        = string
}

variable "target_repo_ref" {
  description = "Branch or ref to check out and reset to on every scheduled run."
  type        = string
  default     = "main"
}

variable "runtime_pip_spec" {
  description = "pip install target for repo-dev-runtime itself. Defaults to installing straight from this GitHub repo at a pinned ref; override to pin a tag/commit instead of a moving branch once you've validated a release."
  type        = string
  default     = "git+https://github.com/christianv997/repo-dev-runtime.git@main"
}

variable "schedule_oncalendar" {
  description = "systemd OnCalendar expression for the run cadence (systemd.time(7) syntax, not cron)."
  type        = string
  default     = "*-*-* */2:00:00"
}

variable "schedule_key" {
  description = "Stable --schedule-key value threaded into every scheduled run's TaskStateStore.claim() call."
  type        = string
  default     = "nightly-review"
}

variable "run_prompt" {
  description = "The --prompt passed to every scheduled run."
  type        = string
  default     = "Review open issues and propose the highest-value fix"
}

variable "openrouter_model" {
  description = "DEV_OMNIROUTE_MODEL value (an OpenRouter model slug)."
  type        = string
  default     = "anthropic/claude-3.5-sonnet"
}

variable "ollama_url_parameter_name" {
  description = "Name of the SSM SecureString parameter holding the reachable Ollama backend URL. Terraform creates the parameter with a placeholder value only (and never overwrites it on subsequent applies via lifecycle.ignore_changes) - populate the real value out-of-band (see README.md) so it never enters Terraform state as a plaintext instance user-data value."
  type        = string
  default     = "/repo-dev-runtime/ollama-url"
}

variable "github_token_secret_name" {
  description = "Name of the Secrets Manager secret holding a GITHUB_TOKEN (repo scope). Terraform creates the secret container only; populate its value out-of-band (see README.md) so the real token never enters Terraform state."
  type        = string
  default     = "repo-dev-runtime/github-token"
}

variable "openrouter_key_secret_name" {
  description = "Name of the Secrets Manager secret holding the OPENROUTER_API_KEY. Terraform creates the secret container only; populate its value out-of-band."
  type        = string
  default     = "repo-dev-runtime/openrouter-key"
}

variable "reap_stuck_timeout_s" {
  description = "Seconds after which a claim left at \"running\" (from a crashed or killed run) is considered stuck and reaped. Must exceed the manifest's check_timeout_s plus provider call overhead."
  type        = number
  default     = 1800
}

variable "tags" {
  description = "Extra tags applied to every resource this module creates."
  type        = map(string)
  default     = {}
}
