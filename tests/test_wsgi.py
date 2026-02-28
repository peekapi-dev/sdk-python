"""Tests for WSGI middleware (Flask, Bottle)."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest

from peekapi.middleware.wsgi import PeekApiWSGI

# ── Helpers ──────────────────────────────────────────────────────────


def simple_wsgi_app(environ: dict, start_response: Any) -> list[bytes]:
    """Minimal WSGI app that returns 200 with a body."""
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"Hello, World!"]


def error_wsgi_app(environ: dict, start_response: Any) -> list[bytes]:
    """WSGI app that raises an exception."""
    raise RuntimeError("app error")


def make_environ(
    method: str = "GET",
    path: str = "/api/test",
    headers: dict[str, str] | None = None,
    content_length: int = 0,
) -> dict:
    env: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "wsgi.input": BytesIO(b""),
    }
    if content_length:
        env["CONTENT_LENGTH"] = str(content_length)
    if headers:
        for key, value in headers.items():
            env_key = "HTTP_" + key.upper().replace("-", "_")
            env[env_key] = value
    return env


def consume_response(app: Any, environ: dict) -> tuple[str, list[bytes]]:
    """Run WSGI app and consume all response data."""
    status_holder: list[str] = []

    def start_response(status: str, headers: list, exc_info: Any = None) -> Any:
        status_holder.append(status)
        return lambda s: None

    response = app(environ, start_response)
    body_parts = list(response)
    if hasattr(response, "close"):
        response.close()
    return status_holder[0] if status_holder else "", body_parts


# ── Tests ────────────────────────────────────────────────────────────


class TestWsgiMiddleware:
    def test_captures_status_and_path(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        environ = make_environ(method="POST", path="/orders")
        consume_response(app, environ)
        client.flush()

        assert len(server.payloads) == 1
        event = server.payloads[0]["events"][0]
        assert event["method"] == "POST"
        assert event["path"] == "/orders"
        assert event["status_code"] == 200

    def test_captures_response_size(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        consume_response(app, make_environ())
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["response_size"] == len(b"Hello, World!")

    def test_captures_consumer_from_headers(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        environ = make_environ(headers={"x-api-key": "wsgi-client-key"})
        consume_response(app, environ)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["consumer_id"] == "wsgi-client-key"

    def test_custom_identify_consumer(self, make_client):
        _make, server, _ = make_client
        client = _make(identify_consumer=lambda headers: headers.get("x-tenant-id"))
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        environ = make_environ(headers={"x-tenant-id": "tenant-42", "x-api-key": "ignored"})
        consume_response(app, environ)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["consumer_id"] == "tenant-42"

    def test_nil_client_passthrough(self):
        app = PeekApiWSGI(simple_wsgi_app, client=None)
        status, body = consume_response(app, make_environ())
        assert status == "200 OK"
        assert body == [b"Hello, World!"]

    def test_error_propagation(self, make_client):
        _make, _, _ = make_client
        client = _make()
        app = PeekApiWSGI(error_wsgi_app, client=client)

        with pytest.raises(RuntimeError, match="app error"):
            consume_response(app, make_environ())

    def test_response_time_measured(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        consume_response(app, make_environ())
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["response_time_ms"] >= 0

    def test_request_size_from_content_length(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        environ = make_environ(content_length=128)
        consume_response(app, environ)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["request_size"] == 128

    def test_collect_query_string_disabled_by_default(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        environ = make_environ(path="/search")
        environ["QUERY_STRING"] = "z=3&a=1"
        consume_response(app, environ)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["path"] == "/search"

    def test_collect_query_string_enabled(self, make_client):
        _make, server, _ = make_client
        client = _make(collect_query_string=True)
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        environ = make_environ(path="/search")
        environ["QUERY_STRING"] = "z=3&a=1"
        consume_response(app, environ)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["path"] == "/search?a=1&z=3"

    def test_collect_query_string_no_qs(self, make_client):
        _make, server, _ = make_client
        client = _make(collect_query_string=True)
        app = PeekApiWSGI(simple_wsgi_app, client=client)

        consume_response(app, make_environ(path="/users"))
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["path"] == "/users"
