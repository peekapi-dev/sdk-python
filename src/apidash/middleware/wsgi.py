"""WSGI middleware — works with Flask, Bottle, and any WSGI app."""

from __future__ import annotations

import contextlib
import time
from typing import Any

from .._consumer import default_identify_consumer
from ..client import ApiDashClient


class ApiDashWSGI:
    """WSGI middleware that tracks HTTP request analytics.

    Usage (Flask)::

        from apidash import ApiDashClient
        from apidash.middleware import ApiDashWSGI

        client = ApiDashClient({"api_key": "...", "endpoint": "..."})
        app.wsgi_app = ApiDashWSGI(app.wsgi_app, client=client)
    """

    def __init__(self, app: Any, client: ApiDashClient | None = None) -> None:
        self.app = app
        self.client = client

    def __call__(self, environ: dict, start_response: Any) -> Any:
        if self.client is None:
            return self.app(environ, start_response)

        start = time.perf_counter()
        status_code = 0

        def tracking_start_response(status: str, headers: list, exc_info: Any = None) -> Any:
            nonlocal status_code
            with contextlib.suppress(ValueError, IndexError, AttributeError):
                status_code = int(status.split(" ", 1)[0])
            return start_response(status, headers, exc_info)

        try:
            response = self.app(environ, tracking_start_response)
            # Wrap the response iterator to measure size
            return _ResponseWrapper(response, self, environ, start, status_code)
        except Exception:
            # If the app raises, still try to track
            try:
                elapsed_ms = (time.perf_counter() - start) * 1000
                headers = _extract_headers(environ)
                consumer_id = default_identify_consumer(headers)
                self.client.track(
                    {
                        "method": environ.get("REQUEST_METHOD", "GET"),
                        "path": environ.get("PATH_INFO", "/"),
                        "status_code": 500,
                        "response_time_ms": round(elapsed_ms, 2),
                        "request_size": _get_content_length(environ),
                        "response_size": 0,
                        "consumer_id": consumer_id,
                    }
                )
            except Exception:
                pass
            raise


class _ResponseWrapper:
    """Wraps a WSGI response iterator to accumulate response size."""

    def __init__(
        self,
        response: Any,
        middleware: ApiDashWSGI,
        environ: dict,
        start: float,
        status_code: int,
    ) -> None:
        self._response = response
        self._middleware = middleware
        self._environ = environ
        self._start = start
        self._status_code = status_code
        self._size = 0

    def __iter__(self) -> Any:
        try:
            for chunk in self._response:
                self._size += len(chunk)
                yield chunk
        finally:
            self._finish()

    def _finish(self) -> None:
        try:
            if hasattr(self._response, "close"):
                self._response.close()
        except Exception:
            pass
        try:
            client = self._middleware.client
            if client is None:
                return
            elapsed_ms = (time.perf_counter() - self._start) * 1000
            headers = _extract_headers(self._environ)
            consumer_id = default_identify_consumer(headers)
            client.track(
                {
                    "method": self._environ.get("REQUEST_METHOD", "GET"),
                    "path": self._environ.get("PATH_INFO", "/"),
                    "status_code": self._status_code,
                    "response_time_ms": round(elapsed_ms, 2),
                    "request_size": _get_content_length(self._environ),
                    "response_size": self._size,
                    "consumer_id": consumer_id,
                }
            )
        except Exception:
            pass  # Never crash the app

    def close(self) -> None:
        # Called explicitly by the WSGI server
        pass


def _extract_headers(environ: dict) -> dict[str, str]:
    """Extract HTTP headers from WSGI environ (HTTP_* keys)."""
    headers: dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            header_name = key[5:].lower().replace("_", "-")
            headers[header_name] = value
    return headers


def _get_content_length(environ: dict) -> int:
    try:
        return int(environ.get("CONTENT_LENGTH", 0) or 0)
    except (ValueError, TypeError):
        return 0
