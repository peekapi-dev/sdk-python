"""Tests for ASGI middleware (FastAPI, Starlette, Litestar)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from apidash.middleware.asgi import ApiDashASGI


# ── Helpers ──────────────────────────────────────────────────────────


async def simple_asgi_app(scope: dict, receive: Any, send: Any) -> None:
    """Minimal ASGI app that returns 200 with a body."""
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"content-type", b"text/plain"]],
    })
    await send({
        "type": "http.response.body",
        "body": b"Hello, World!",
    })


async def error_asgi_app(scope: dict, receive: Any, send: Any) -> None:
    """ASGI app that raises an exception."""
    raise RuntimeError("app error")


def make_scope(
    method: str = "GET",
    path: str = "/api/test",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
    }


async def collect_response(app: Any, scope: dict) -> list[dict]:
    """Run ASGI app and collect sent messages."""
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


# ── Tests ────────────────────────────────────────────────────────────


class TestAsgiMiddleware:
    @pytest.mark.asyncio
    async def test_captures_status_and_path(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = ApiDashASGI(simple_asgi_app, client=client)

        scope = make_scope(method="POST", path="/users")
        await collect_response(app, scope)
        client.flush()

        assert len(server.payloads) == 1
        event = server.payloads[0]["events"][0]
        assert event["method"] == "POST"
        assert event["path"] == "/users"
        assert event["status_code"] == 200

    @pytest.mark.asyncio
    async def test_captures_response_size(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = ApiDashASGI(simple_asgi_app, client=client)

        await collect_response(app, make_scope())
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["response_size"] == len(b"Hello, World!")

    @pytest.mark.asyncio
    async def test_captures_consumer_from_headers(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = ApiDashASGI(simple_asgi_app, client=client)

        scope = make_scope(headers=[(b"x-api-key", b"client-key-123")])
        await collect_response(app, scope)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["consumer_id"] == "client-key-123"

    @pytest.mark.asyncio
    async def test_nil_client_passthrough(self):
        app = ApiDashASGI(simple_asgi_app, client=None)
        sent = await collect_response(app, make_scope())
        assert any(m.get("status") == 200 for m in sent)

    @pytest.mark.asyncio
    async def test_error_propagation(self, make_client):
        _make, _, _ = make_client
        client = _make()
        app = ApiDashASGI(error_asgi_app, client=client)

        with pytest.raises(RuntimeError, match="app error"):
            await collect_response(app, make_scope())

    @pytest.mark.asyncio
    async def test_non_http_scope_passthrough(self, make_client):
        _make, server, _ = make_client
        client = _make()

        called = False

        async def websocket_app(scope, receive, send):
            nonlocal called
            called = True

        app = ApiDashASGI(websocket_app, client=client)
        scope = {"type": "websocket", "path": "/ws"}
        await app(scope, None, None)
        assert called
        assert len(client._buffer) == 0

    @pytest.mark.asyncio
    async def test_response_time_measured(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = ApiDashASGI(simple_asgi_app, client=client)

        await collect_response(app, make_scope())
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["response_time_ms"] >= 0

    @pytest.mark.asyncio
    async def test_request_size_from_content_length(self, make_client):
        _make, server, _ = make_client
        client = _make()
        app = ApiDashASGI(simple_asgi_app, client=client)

        scope = make_scope(headers=[(b"content-length", b"42")])
        await collect_response(app, scope)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["request_size"] == 42
