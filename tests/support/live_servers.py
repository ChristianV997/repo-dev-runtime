"""Real local HTTP servers speaking the exact wire protocols the runtime
adapters (`repo_dev_runtime/runtimes/*.py`) call over the network.

These are used instead of mocking `urlopen` so that adapter tests exercise
a genuine TCP round-trip, real HTTP parsing, and real JSON/SSE decoding.
Every server binds to 127.0.0.1 on an OS-assigned free port, so this stays
fully hermetic and safe to run unconditionally in CI.
"""
from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterator

# A responder inspects the raw request body/headers and decides how to reply:
# (delay_seconds, status_code, response_body, content_type).
Responder = Callable[[bytes, dict], "tuple[float, int, bytes, str]"]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _dispatch(self, method: str) -> None:
        routes: dict[tuple[str, str], Responder] = self.server.routes  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        responder = routes.get((method, self.path))
        if responder is None:
            self.send_response(404)
            self.end_headers()
            return
        # self.headers is an email.message.Message: case-insensitive lookup,
        # matching real HTTP header semantics (unlike a plain dict()).
        delay, status, response_body, content_type = responder(body, self.headers)
        if delay:
            import time

            time.sleep(delay)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        # A client that times out and closes its socket before a delayed
        # response is written causes a BrokenPipeError on this thread — that
        # is the test's intended behavior, not a real server fault, so don't
        # spam CI logs with it. Anything else still prints normally.
        import sys

        exc_type = sys.exc_info()[0]
        if exc_type is not None and issubclass(exc_type, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class StubServer:
    def __init__(self, routes: dict[tuple[str, str], Responder]) -> None:
        self._httpd = _QuietThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.routes = routes  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        _, port = self._httpd.server_address
        return f"http://127.0.0.1:{port}"

    def start(self) -> "StubServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


@contextmanager
def stub_server(routes: dict[tuple[str, str], Responder]) -> Iterator[StubServer]:
    server = StubServer(routes).start()
    try:
        yield server
    finally:
        server.stop()


def json_response(status: int, payload: object, *, delay: float = 0.0) -> Responder:
    body = json.dumps(payload).encode("utf-8")

    def _responder(_body: bytes, _headers: dict) -> tuple[float, int, bytes, str]:
        return delay, status, body, "application/json"

    return _responder


def raw_response(status: int, body: bytes, content_type: str = "application/json", *, delay: float = 0.0) -> Responder:
    def _responder(_body: bytes, _headers: dict) -> tuple[float, int, bytes, str]:
        return delay, status, body, content_type

    return _responder


def capturing(store: dict, inner: Responder) -> Responder:
    """Wrap a responder to record the last request's body/headers it received."""

    def _responder(body: bytes, headers: dict) -> tuple[float, int, bytes, str]:
        store["body"] = body
        store["headers"] = headers
        return inner(body, headers)

    return _responder


def unused_port() -> int:
    """A port nothing is listening on, for real connection-refused tests."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _demo_ollama_server(port: int) -> None:
    """Standalone entry point (`python -m tests.support.live_servers [port]`):
    serves a real Ollama-protocol stub until interrupted, so a developer can
    manually exercise `repo_dev_runtime.cli health` / `run --live` against a
    genuine (if scripted) backend. See docs/live-validation.md."""
    routes = {
        ("GET", "/api/tags"): json_response(200, {"models": [{"name": "demo-model"}]}),
        ("POST", "/api/chat"): json_response(
            200,
            {"message": {"content": "This is a real HTTP response from the live-validation demo stub, proving OllamaRuntime's request/response code path works end-to-end."}},
        ),
    }
    httpd = _QuietThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.routes = routes  # type: ignore[attr-defined]
    print(f"Serving real Ollama-protocol stub on http://127.0.0.1:{port} (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import sys

    _demo_ollama_server(int(sys.argv[1]) if len(sys.argv) > 1 else 11434)
