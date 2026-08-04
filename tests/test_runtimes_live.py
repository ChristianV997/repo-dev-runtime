"""Live (real-socket, non-mocked) tests for the network-touching runtime
adapters: OllamaRuntime, OpenAICompatibleRuntime, HermesRuntime (default
request path), DeerFlowRuntime.

Prior to this file, three of these four adapters' `execute()` HTTP/JSON/SSE
code had zero test coverage of any kind (not even a mock) and the fourth
(Hermes) was only ever tested via a dependency-injected fake that bypassed
HTTP entirely. These tests run the real adapter code against a real local
HTTP server (tests/support/live_servers.py) over a real TCP socket.
"""
from __future__ import annotations

import json

from repo_dev_runtime.contracts.models import DevTask
from repo_dev_runtime.runtimes.ollama import OllamaRuntime
from repo_dev_runtime.runtimes.openai_compatible import OpenAICompatibleRuntime
from repo_dev_runtime.runtimes.sidecars import DeerFlowRuntime, HermesRuntime

from tests.support.live_servers import capturing, json_response, raw_response, stub_server, unused_port


def _task(**overrides) -> DevTask:
    fields = dict(task_id="t1", repository="repo", base_ref="HEAD", role="implementer", prompt="inspect")
    fields.update(overrides)
    return DevTask(**fields)


OVERSIZED_BODY = b'{"message":{"content":"' + b"a" * 600_000 + b'"}}'


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def test_ollama_health_reachable_against_real_server():
    with stub_server({("GET", "/api/tags"): json_response(200, {"models": []})}) as server:
        runtime = OllamaRuntime(base_url=server.base_url, enabled=True)
        health = runtime.health()
    assert health.configured is True
    assert health.reachable is True


def test_ollama_health_unreachable_when_nothing_listening():
    runtime = OllamaRuntime(base_url=f"http://127.0.0.1:{unused_port()}", enabled=True)
    health = runtime.health()
    assert health.reachable is False
    assert health.detail == "URLError"


def test_ollama_execute_succeeds_against_real_server():
    with stub_server({("POST", "/api/chat"): json_response(200, {"message": {"content": "hello from ollama"}})}) as server:
        runtime = OllamaRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "succeeded"
    assert result.output == "hello from ollama"


def test_ollama_sends_exact_schema_for_implementer_contract():
    captured: dict = {}
    routes = {("POST", "/api/chat"): capturing(captured, json_response(200, {"message": {"content": "{}"}}))}
    with stub_server(routes) as server:
        OllamaRuntime(base_url=server.base_url, enabled=True).execute(_task(role="implementer"))
    schema = json.loads(captured["body"].decode())["format"]
    assert schema["properties"]["schema"]["const"] == "RepoDev.EditProposal.v1"
    assert set(schema["required"]) >= {"proposal_id", "task_id", "base_commit", "context_hash", "edits"}


def test_ollama_sends_exact_schema_for_reviewer_contract():
    captured: dict = {}
    routes = {("POST", "/api/chat"): capturing(captured, json_response(200, {"message": {"content": "{}"}}))}
    with stub_server(routes) as server:
        OllamaRuntime(base_url=server.base_url, enabled=True).execute(_task(role="reviewer"))
    body = json.loads(captured["body"].decode())
    assert body["format"]["properties"]["schema"]["const"] == "RepoDev.ReviewVerdict.v1"


def test_ollama_execute_classifies_http_error():
    with stub_server({("POST", "/api/chat"): json_response(500, {"error": "boom"})}) as server:
        runtime = OllamaRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "HTTPError"


def test_ollama_execute_classifies_malformed_json():
    with stub_server({("POST", "/api/chat"): raw_response(200, b"not json")}) as server:
        runtime = OllamaRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "JSONDecodeError"


def test_ollama_execute_classifies_oversized_response():
    with stub_server({("POST", "/api/chat"): raw_response(200, OVERSIZED_BODY)}) as server:
        runtime = OllamaRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "ValueError"


def test_ollama_execute_classifies_timeout():
    with stub_server({("POST", "/api/chat"): json_response(200, {"message": {"content": "late"}}, delay=0.5)}) as server:
        runtime = OllamaRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task(timeout_s=0.05))
    assert result.status == "failed"
    assert result.error_type in {"TimeoutError", "timeout"}


def test_ollama_execute_classifies_connection_refused():
    runtime = OllamaRuntime(base_url=f"http://127.0.0.1:{unused_port()}", enabled=True)
    result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "URLError"


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------

def test_openai_compatible_health_reachable_against_real_server():
    with stub_server({("GET", "/v1/models"): json_response(200, {"data": []})}) as server:
        runtime = OpenAICompatibleRuntime(base_url=server.base_url, enabled=True)
        health = runtime.health()
    assert health.reachable is True


def test_openai_compatible_execute_succeeds_against_real_server():
    with stub_server({("POST", "/v1/chat/completions"): json_response(200, {"choices": [{"message": {"content": "hi there"}}]})}) as server:
        runtime = OpenAICompatibleRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "succeeded"
    assert result.output == "hi there"


def test_openai_compatible_sends_exact_schema_for_contract_roles():
    captured: dict = {}
    routes = {("POST", "/v1/chat/completions"): capturing(captured, json_response(200, {"choices": [{"message": {"content": "{}"}}]}))}
    with stub_server(routes) as server:
        OpenAICompatibleRuntime(base_url=server.base_url, enabled=True).execute(_task(role="implementer"))
    response_format = json.loads(captured["body"].decode())["response_format"]
    assert response_format["type"] == "json_schema"
    # strict=False: the strict subset forbids minLength/minItems and
    # requires every properties key in required, which these schemas
    # violate - a strict request would be rejected by a real OpenAI-
    # compatible endpoint before a model ever ran. parse_edit_proposal
    # validates every field independently regardless of this setting.
    assert response_format["json_schema"]["strict"] is False
    assert response_format["json_schema"]["schema"]["properties"]["schema"]["const"] == "RepoDev.EditProposal.v1"


def test_openai_compatible_execute_classifies_oversized_response():
    with stub_server({("POST", "/v1/chat/completions"): raw_response(200, OVERSIZED_BODY)}) as server:
        runtime = OpenAICompatibleRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "ValueError"


def test_openai_compatible_execute_classifies_timeout():
    with stub_server({("POST", "/v1/chat/completions"): json_response(200, {"choices": [{"message": {"content": "late"}}]}, delay=0.5)}) as server:
        runtime = OpenAICompatibleRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task(timeout_s=0.05))
    assert result.status == "failed"
    assert result.error_type in {"TimeoutError", "timeout"}


def test_openai_compatible_forwards_bearer_token():
    captured: dict = {}
    routes = {("POST", "/v1/chat/completions"): capturing(captured, json_response(200, {"choices": [{"message": {"content": "ok"}}]}))}
    with stub_server(routes) as server:
        runtime = OpenAICompatibleRuntime(base_url=server.base_url, token="secret-token", enabled=True)
        result = runtime.execute(_task())
    assert result.status == "succeeded"
    assert captured["headers"].get("Authorization") == "Bearer secret-token"


def test_openai_compatible_sends_no_auth_header_without_token():
    captured: dict = {}
    routes = {("POST", "/v1/chat/completions"): capturing(captured, json_response(200, {"choices": [{"message": {"content": "ok"}}]}))}
    with stub_server(routes) as server:
        runtime = OpenAICompatibleRuntime(base_url=server.base_url, token="", enabled=True)
        runtime.execute(_task())
    assert "Authorization" not in captured["headers"]


# ---------------------------------------------------------------------------
# Hermes (default `_post_json` real-HTTP path, not the injected fake)
# ---------------------------------------------------------------------------

def test_hermes_execute_succeeds_against_real_server_default_request_path():
    with stub_server({("POST", "/v1/chat/completions"): json_response(200, {"choices": [{"message": {"content": "hermes reply"}}]})}) as server:
        runtime = HermesRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "succeeded"
    assert result.output == "hermes reply"


def test_hermes_forwards_bearer_token_default_request_path():
    captured: dict = {}
    routes = {("POST", "/v1/chat/completions"): capturing(captured, json_response(200, {"choices": [{"message": {"content": "ok"}}]}))}
    with stub_server(routes) as server:
        runtime = HermesRuntime(base_url=server.base_url, token="hermes-secret", enabled=True)
        runtime.execute(_task())
    assert captured["headers"].get("Authorization") == "Bearer hermes-secret"


def test_hermes_execute_classifies_http_error_default_request_path():
    with stub_server({("POST", "/v1/chat/completions"): json_response(500, {"error": "boom"})}) as server:
        runtime = HermesRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "HTTPError"


def test_hermes_health_reachable_against_real_server():
    with stub_server({("GET", "/v1/models"): json_response(200, {"data": []})}) as server:
        runtime = HermesRuntime(base_url=server.base_url, enabled=True)
        health = runtime.health()
    assert health.reachable is True


def test_hermes_health_unreachable_when_nothing_listening():
    runtime = HermesRuntime(base_url=f"http://127.0.0.1:{unused_port()}", enabled=True)
    health = runtime.health()
    assert health.reachable is False
    assert health.detail == "URLError"


def test_hermes_execute_classifies_malformed_json_default_request_path():
    with stub_server({("POST", "/v1/chat/completions"): raw_response(200, b"not json")}) as server:
        runtime = HermesRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "JSONDecodeError"


def test_hermes_execute_classifies_oversized_response_as_truncated_json():
    # The shared helper enforces the task output budget before JSON parsing.
    hermes_oversized_body = b'{"choices":[{"message":{"content":"' + b"a" * 2_100_000 + b'"}}]}'
    with stub_server({("POST", "/v1/chat/completions"): raw_response(200, hermes_oversized_body)}) as server:
        runtime = HermesRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "ValueError"


def test_hermes_execute_classifies_timeout_default_request_path():
    with stub_server({("POST", "/v1/chat/completions"): json_response(200, {"choices": [{"message": {"content": "late"}}]}, delay=0.5)}) as server:
        runtime = HermesRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task(timeout_s=0.05))
    assert result.status == "failed"
    assert result.error_type in {"TimeoutError", "timeout"}


def test_hermes_execute_classifies_connection_refused_default_request_path():
    runtime = HermesRuntime(base_url=f"http://127.0.0.1:{unused_port()}", enabled=True)
    result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "URLError"


# ---------------------------------------------------------------------------
# DeerFlow (SSE)
# ---------------------------------------------------------------------------

def test_deerflow_health_reachable_against_real_server():
    with stub_server({("GET", "/health"): json_response(200, {"status": "ok"})}) as server:
        runtime = DeerFlowRuntime(base_url=server.base_url, enabled=True)
        health = runtime.health()
    assert health.reachable is True


def test_deerflow_execute_extracts_last_assistant_message_from_sse_stream():
    body = (
        b"data: " + b'{"messages":[{"role":"assistant","content":"first"}]}' + b"\n\n"
        b"data: " + b'{"messages":[{"role":"assistant","content":"final answer"}]}' + b"\n\n"
        b"data: [DONE]\n\n"
    )
    with stub_server({("POST", "/api/langgraph/runs/stream"): raw_response(200, body, "text/event-stream")}) as server:
        runtime = DeerFlowRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "succeeded"
    assert result.output == "final answer"


def test_deerflow_execute_falls_back_to_bare_content_field():
    body = b"data: " + b'{"content":"bare content event"}' + b"\n\ndata: [DONE]\n\n"
    with stub_server({("POST", "/api/langgraph/runs/stream"): raw_response(200, body, "text/event-stream")}) as server:
        runtime = DeerFlowRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "succeeded"
    assert result.output == "bare content event"


def test_deerflow_execute_fails_when_stream_has_no_assistant_content():
    body = b"data: [DONE]\n\n"
    with stub_server({("POST", "/api/langgraph/runs/stream"): raw_response(200, body, "text/event-stream")}) as server:
        runtime = DeerFlowRuntime(base_url=server.base_url, enabled=True)
        result = runtime.execute(_task())
    assert result.status == "failed"
    assert result.error_type == "ValueError"
    assert "no assistant content" in result.error_message


def test_deerflow_execute_forwards_auth_headers():
    captured: dict = {}
    routes = {("POST", "/api/langgraph/runs/stream"): capturing(captured, raw_response(200, b"data: [DONE]\n\n", "text/event-stream"))}
    with stub_server(routes) as server:
        runtime = DeerFlowRuntime(base_url=server.base_url, token="deerflow-secret", owner_id="owner-1", enabled=True)
        runtime.execute(_task())
    assert captured["headers"].get("X-DeerFlow-Internal-Token") == "deerflow-secret"
    assert captured["headers"].get("X-DeerFlow-Owner-User-Id") == "owner-1"
