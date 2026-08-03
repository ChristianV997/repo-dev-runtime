# Canonical Architecture

Updated: {{DATE}}
Scope: {{REPO_NAME}} agent-assisted development context.

Source of truth: executable code, tests, and current deployment
configuration. This file is a compact index, not a second architecture
document.

Inspect first: <!-- fill in this repo's top-level source directories, e.g. `src/`, `api/`, `worker/`, `tests/` -->

Current shape: <!-- one or two sentences describing how major subsystems connect end to end -->

Avoid duplicating orchestration, provider clients, risk gates, or
telemetry. Search callers before adding a subsystem.
