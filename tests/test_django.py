"""Tests for Django middleware."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from apidash.middleware.django import ApiDashMiddleware


# ── Helpers ──────────────────────────────────────────────────────────


def make_django_request(
    method: str = "GET",
    path: str = "/api/test",
    meta: dict[str, str] | None = None,
) -> MagicMock:
    request = MagicMock()
    request.method = method
    request.path = path
    request.META = meta or {}
    return request


def make_django_response(status_code: int = 200, content: bytes = b"OK") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    return response


# ── Tests ────────────────────────────────────────────────────────────


class TestDjangoMiddleware:
    def setup_method(self):
        # Reset class-level client between tests
        ApiDashMiddleware._client = None

    def test_captures_status_and_path(self, make_client):
        _make, server, _ = make_client
        client = _make()
        ApiDashMiddleware._client = client

        response = make_django_response(201, b'{"id": 1}')
        get_response = MagicMock(return_value=response)
        middleware = ApiDashMiddleware(get_response)

        request = make_django_request("POST", "/users")
        middleware(request)
        client.flush()

        assert len(server.payloads) == 1
        event = server.payloads[0]["events"][0]
        assert event["method"] == "POST"
        assert event["path"] == "/users"
        assert event["status_code"] == 201

    def test_captures_response_size(self, make_client):
        _make, server, _ = make_client
        client = _make()
        ApiDashMiddleware._client = client

        body = b"Hello, Django!"
        response = make_django_response(200, body)
        middleware = ApiDashMiddleware(MagicMock(return_value=response))

        middleware(make_django_request())
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["response_size"] == len(body)

    def test_captures_consumer_from_headers(self, make_client):
        _make, server, _ = make_client
        client = _make()
        ApiDashMiddleware._client = client

        middleware = ApiDashMiddleware(MagicMock(return_value=make_django_response()))
        request = make_django_request(meta={"HTTP_X_API_KEY": "django-key"})
        middleware(request)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["consumer_id"] == "django-key"

    def test_nil_client_passthrough(self):
        ApiDashMiddleware._client = None
        response = make_django_response()
        get_response = MagicMock(return_value=response)
        middleware = ApiDashMiddleware(get_response)

        result = middleware(make_django_request())
        assert result is response
        get_response.assert_called_once()

    def test_response_time_measured(self, make_client):
        _make, server, _ = make_client
        client = _make()
        ApiDashMiddleware._client = client

        middleware = ApiDashMiddleware(MagicMock(return_value=make_django_response()))
        middleware(make_django_request())
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["response_time_ms"] >= 0

    def test_request_size_from_content_length(self, make_client):
        _make, server, _ = make_client
        client = _make()
        ApiDashMiddleware._client = client

        middleware = ApiDashMiddleware(MagicMock(return_value=make_django_response()))
        request = make_django_request(meta={"CONTENT_LENGTH": "256"})
        middleware(request)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["request_size"] == 256
