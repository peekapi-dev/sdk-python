"""Django middleware — reads config from settings.APIDASH or accepts a client."""

from __future__ import annotations

import contextlib
import time
from typing import Any

from .._consumer import default_identify_consumer
from ..client import ApiDashClient


class ApiDashMiddleware:
    """Django middleware that tracks HTTP request analytics.

    Usage (settings.py)::

        APIDASH = {
            "api_key": "your-api-key",
            "endpoint": "https://your-project.supabase.co/functions/v1/ingest",
        }

        MIDDLEWARE = [
            "apidash.middleware.django.ApiDashMiddleware",
            # ...
        ]
    """

    _client: ApiDashClient | None = None

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

        # Initialize client on first instantiation
        if ApiDashMiddleware._client is None:
            try:
                from django.conf import settings  # type: ignore[import-untyped]

                config = getattr(settings, "APIDASH", None)
                if config and isinstance(config, dict):
                    ApiDashMiddleware._client = ApiDashClient(config)
            except Exception:
                pass  # Django not available or bad config — passthrough

    def __call__(self, request: Any) -> Any:
        if self._client is None:
            return self.get_response(request)

        start = time.perf_counter()

        response = self.get_response(request)

        try:
            elapsed_ms = (time.perf_counter() - start) * 1000

            # Django request.META stores headers as HTTP_* keys
            headers: dict[str, str] = {}
            for key, value in request.META.items():
                if key.startswith("HTTP_"):
                    header_name = key[5:].lower().replace("_", "-")
                    headers[header_name] = value

            consumer_id = default_identify_consumer(headers)

            # Response size from content
            response_size = 0
            if hasattr(response, "content"):
                response_size = len(response.content)

            # Request size
            request_size = 0
            cl = request.META.get("CONTENT_LENGTH")
            if cl:
                with contextlib.suppress(ValueError, TypeError):
                    request_size = int(cl)

            self._client.track(
                {
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                    "response_time_ms": round(elapsed_ms, 2),
                    "request_size": request_size,
                    "response_size": response_size,
                    "consumer_id": consumer_id,
                }
            )
        except Exception:
            pass  # Never crash the app

        return response
