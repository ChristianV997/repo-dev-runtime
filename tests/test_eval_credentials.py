"""Tests for repo_dev_runtime.governance.credentials."""
from __future__ import annotations

from repo_dev_runtime.eval.conformance import assert_no_credential_leak
from repo_dev_runtime.governance.credentials import (
    CredentialAllowlist,
    build_subprocess_env,
    missing_credential_result,
    redact_json,
    redact_text,
)
from repo_dev_runtime.tools.runner import run_command


def test_build_subprocess_env_excludes_unrelated_host_vars():
    source = {
        "PR_AGENT_TOKEN": "secret-value",
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "AWS_SECRET_ACCESS_KEY": "leaked-if-present",
        "SOME_OTHER_APP_TOKEN": "should-not-appear",
    }
    allowlist = CredentialAllowlist(provider="pr_agent", env_prefixes=("PR_AGENT_",))
    env = build_subprocess_env(allowlist, source=source)

    assert env == {"PR_AGENT_TOKEN": "secret-value", "PATH": "/usr/bin", "HOME": "/home/user"}
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "SOME_OTHER_APP_TOKEN" not in env


def test_build_subprocess_env_with_no_prefixes_is_credential_free():
    source = {"PR_AGENT_TOKEN": "secret", "PATH": "/usr/bin"}
    allowlist = CredentialAllowlist(provider="context_provider")
    env = build_subprocess_env(allowlist, source=source)

    assert "PR_AGENT_TOKEN" not in env
    assert env == {"PATH": "/usr/bin"}


def test_credential_allowlist_requires_provider_name():
    try:
        CredentialAllowlist(provider="")
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty provider name")


def test_redact_text_scrubs_key_names_bearer_and_value_fields():
    payload = (
        '{"API_KEY": "abc", "note": "Authorization: Bearer sk-live-1234567890", '
        '"token": "raw-secret-value"}'
    )
    redacted = redact_text(payload)

    assert "abc" not in redacted
    assert "sk-live-1234567890" not in redacted
    assert "raw-secret-value" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_json_scrubs_nested_sensitive_keys():
    payload = {
        "telemetry": {"api_key": "super-secret", "duration_ms": 12.5},
        "history": [{"password": "hunter2"}, {"note": "safe value"}],
    }
    redacted = redact_json(payload)

    assert redacted["telemetry"]["api_key"] == "[REDACTED]"
    assert redacted["telemetry"]["duration_ms"] == 12.5
    assert redacted["history"][0]["password"] == "[REDACTED]"
    assert redacted["history"][1]["note"] == "safe value"


def test_missing_credential_result_is_blocked_not_raised():
    result = missing_credential_result(provider="pr_agent", name="PR_AGENT_TOKEN")

    assert result["status"] == "blocked"
    assert result["error_type"] == "credential_missing"
    assert "PR_AGENT_TOKEN" in result["error_message"]


def test_shared_leak_assertion_covers_strings_and_json():
    # The same check real provider tests are expected to call.
    assert_no_credential_leak("api_key: hunter2", secret="hunter2")
    assert_no_credential_leak({"telemetry": {"api_key": "hunter2"}}, secret="hunter2")


def test_run_command_redacts_leaked_secret_in_captured_output(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "do-not-leak-me")
    script = tmp_path / "leak.py"
    script.write_text(
        "import os\n"
        "print('token: ' + os.environ.get('SUPER_SECRET_TOKEN', ''))\n"
        "print('Authorization: Bearer do-not-leak-me')\n"
    )
    result = run_command(["python3", str(script)], cwd=tmp_path)

    assert "do-not-leak-me" not in result.stdout
    assert "do-not-leak-me" not in result.stderr
