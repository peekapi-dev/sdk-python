from __future__ import annotations

import contextlib
import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from peekapi import PeekApiClient


@pytest.fixture
def tmp_storage_path(tmp_path: Any) -> str:
    """Unique storage path per test — prevents flaky disk tests."""
    return str(tmp_path / "peekapi-events.jsonl")


class IngestHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler that records received payloads."""

    server: IngestServer  # type: ignore[assignment]

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        events = json.loads(body)
        api_key = self.headers.get("x-api-key", "")
        sdk_header = self.headers.get("x-peekapi-sdk", "")
        self.server.payloads.append({"events": events, "api_key": api_key, "sdk": sdk_header})  # type: ignore[attr-defined]

        status = self.server.response_status  # type: ignore[attr-defined]
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"accepted": len(events)}).encode())

    def log_message(self, *args: Any) -> None:
        pass  # Suppress request logging


class IngestServer(HTTPServer):
    payloads: list[dict[str, Any]]
    response_status: int


@pytest.fixture
def ingest_server():
    """Start a local HTTP server in a thread, yield (url, payloads list)."""
    server = IngestServer(("127.0.0.1", 0), IngestHandler)
    server.payloads = []
    server.response_status = 200
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield server, url

    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def make_client(ingest_server, tmp_storage_path):
    """Factory that creates a client pre-configured for test server."""
    server, url = ingest_server
    clients: list[PeekApiClient] = []

    def _make(**overrides: Any) -> PeekApiClient:
        opts = {
            "api_key": "test-key",
            "endpoint": url,
            "flush_interval": 60.0,  # Don't auto-flush in tests
            "batch_size": 100,
            "storage_path": tmp_storage_path,
            "debug": True,
            **overrides,
        }
        c = PeekApiClient(opts)
        clients.append(c)
        return c

    yield _make, server, url

    for c in clients:
        # Restore signal handlers before shutdown to avoid test interference
        for sig, handler in list(c._original_handlers.items()):
            with contextlib.suppress(OSError, ValueError):
                signal.signal(sig, handler)
            c._original_handlers.clear()
        c._shutdown = True
        c._done.set()
        c._wake.set()
        with contextlib.suppress(Exception):
            c._thread.join(timeout=2)

    # Clean up storage
    for path in (tmp_storage_path, tmp_storage_path + ".recovering"):
        with contextlib.suppress(OSError):
            os.unlink(path)
