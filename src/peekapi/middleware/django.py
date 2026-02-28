"""Django middleware — reads config from settings.PEEKAPI or accepts a client."""

from __future__ import annotations

import contextlib
import time
from typing import Any

from .._consumer import default_identify_consumer
from ..client import PeekApiClient


class PeekApiMiddleware:
    """Django middleware that tracks HTTP request analytics.

    Usage (settings.py)::

        PEEKAPI = {
            "api_key": "your-api-key",
            "endpoint": "https://your-project.supabase.co/functions/v1/ingest",
        }

        MIDDLEWARE = [
            "peekapi.middleware.django.PeekApiMiddleware",
            # ...
        ]
    """

    _client: PeekApiClient | None = None

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

        # Initialize client on first instantiation
        if PeekApiMiddleware._client is None:
            try:
                from django.conf import settings  # type: ignore[import-untyped]

                config = getattr(settings, "PEEKAPI", None)
                if config and isinstance(config, dict):
                    PeekApiMiddleware._client = PeekApiClient(config)
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

            if self._client.identify_consumer:
                consumer_id = self._client.identify_consumer(headers)
            else:
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

            path = request.path
            if self._client.collect_query_string:
                qs = request.META.get("QUERY_STRING", "")
                if qs:
                    sorted_qs = "&".join(sorted(qs.split("&")))
                    path = f"{path}?{sorted_qs}"

            self._client.track(
                {
                    "method": request.method,
                    "path": path,
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
