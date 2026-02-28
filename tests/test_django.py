"""Tests for Django middleware."""

from __future__ import annotations

from unittest.mock import MagicMock

from peekapi.middleware.django import PeekApiMiddleware

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
        PeekApiMiddleware._client = None

    def test_captures_status_and_path(self, make_client):
        _make, server, _ = make_client
        client = _make()
        PeekApiMiddleware._client = client

        response = make_django_response(201, b'{"id": 1}')
        get_response = MagicMock(return_value=response)
        middleware = PeekApiMiddleware(get_response)

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
        PeekApiMiddleware._client = client

        body = b"Hello, Django!"
        response = make_django_response(200, body)
        middleware = PeekApiMiddleware(MagicMock(return_value=response))

        middleware(make_django_request())
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["response_size"] == len(body)

    def test_captures_consumer_from_headers(self, make_client):
        _make, server, _ = make_client
        client = _make()
        PeekApiMiddleware._client = client

        middleware = PeekApiMiddleware(MagicMock(return_value=make_django_response()))
        request = make_django_request(meta={"HTTP_X_API_KEY": "django-key"})
        middleware(request)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["consumer_id"] == "django-key"

    def test_custom_identify_consumer(self, make_client):
        _make, server, _ = make_client
        client = _make(identify_consumer=lambda headers: headers.get("x-tenant-id"))
        PeekApiMiddleware._client = client

        middleware = PeekApiMiddleware(MagicMock(return_value=make_django_response()))
        request = make_django_request(
            meta={
                "HTTP_X_TENANT_ID": "tenant-42",
                "HTTP_X_API_KEY": "ignored",
            }
        )
        middleware(request)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["consumer_id"] == "tenant-42"

    def test_nil_client_passthrough(self):
        PeekApiMiddleware._client = None
        response = make_django_response()
        get_response = MagicMock(return_value=response)
        middleware = PeekApiMiddleware(get_response)

        result = middleware(make_django_request())
        assert result is response
        get_response.assert_called_once()

    def test_response_time_measured(self, make_client):
        _make, server, _ = make_client
        client = _make()
        PeekApiMiddleware._client = client

        middleware = PeekApiMiddleware(MagicMock(return_value=make_django_response()))
        middleware(make_django_request())
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["response_time_ms"] >= 0

    def test_request_size_from_content_length(self, make_client):
        _make, server, _ = make_client
        client = _make()
        PeekApiMiddleware._client = client

        middleware = PeekApiMiddleware(MagicMock(return_value=make_django_response()))
        request = make_django_request(meta={"CONTENT_LENGTH": "256"})
        middleware(request)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["request_size"] == 256

    def test_collect_query_string_disabled_by_default(self, make_client):
        _make, server, _ = make_client
        client = _make()
        PeekApiMiddleware._client = client

        middleware = PeekApiMiddleware(MagicMock(return_value=make_django_response()))
        request = make_django_request(path="/search", meta={"QUERY_STRING": "z=3&a=1"})
        middleware(request)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["path"] == "/search"

    def test_collect_query_string_enabled(self, make_client):
        _make, server, _ = make_client
        client = _make(collect_query_string=True)
        PeekApiMiddleware._client = client

        middleware = PeekApiMiddleware(MagicMock(return_value=make_django_response()))
        request = make_django_request(path="/search", meta={"QUERY_STRING": "z=3&a=1"})
        middleware(request)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["path"] == "/search?a=1&z=3"

    def test_collect_query_string_no_qs(self, make_client):
        _make, server, _ = make_client
        client = _make(collect_query_string=True)
        PeekApiMiddleware._client = client

        middleware = PeekApiMiddleware(MagicMock(return_value=make_django_response()))
        request = make_django_request(path="/users")
        middleware(request)
        client.flush()

        event = server.payloads[0]["events"][0]
        assert event["path"] == "/users"
