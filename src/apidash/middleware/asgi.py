"""ASGI middleware — works with FastAPI, Starlette, Litestar, and any ASGI app."""

from __future__ import annotations

import time
from typing import Any

from .._consumer import default_identify_consumer
from ..client import ApiDashClient


class ApiDashASGI:
    """ASGI middleware that tracks HTTP request analytics.

    Usage (FastAPI / Starlette)::

        from apidash import ApiDashClient
        from apidash.middleware import ApiDashASGI

        client = ApiDashClient({"api_key": "...", "endpoint": "..."})
        app.add_middleware(ApiDashASGI, client=client)
    """

    def __init__(self, app: Any, client: ApiDashClient | None = None, **kwargs: Any) -> None:
        self.app = app
        self.client = client

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or self.client is None:
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 0
        response_size = 0

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code, response_size
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                response_size += len(body)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                elapsed_ms = (time.perf_counter() - start) * 1000

                # Extract headers from ASGI scope (list of [name, value] byte tuples)
                headers: dict[str, str] = {}
                for name, value in scope.get("headers", []):
                    headers[name.decode("latin-1").lower()] = value.decode("latin-1")

                consumer_id = default_identify_consumer(headers)

                method = scope.get("method", "GET")
                path = scope.get("path", "/")

                # Request size from content-length header
                request_size = 0
                cl = headers.get("content-length")
                if cl:
                    try:
                        request_size = int(cl)
                    except (ValueError, TypeError):
                        pass

                self.client.track({
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "response_time_ms": round(elapsed_ms, 2),
                    "request_size": request_size,
                    "response_size": response_size,
                    "consumer_id": consumer_id,
                })
            except Exception:
                pass  # Never crash the app
