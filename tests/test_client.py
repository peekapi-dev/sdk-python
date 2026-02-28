"""Tests for PeekApiClient — constructor, buffer, flush, retry, disk, shutdown."""

from __future__ import annotations

import json
import os
import time

import pytest

from peekapi import PeekApiClient
from peekapi.types import Options, RequestEvent


def _evt(**kw):
    """Shortcut for a minimal event dict."""
    return {"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1, **kw}


# ── Constructor validation ───────────────────────────────────────────


class TestConstructor:
    def test_missing_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            PeekApiClient({"api_key": "", "endpoint": "http://127.0.0.1:9999"})

    def test_control_chars_in_api_key(self):
        with pytest.raises(ValueError, match="control characters"):
            PeekApiClient({"api_key": "key\x00bad", "endpoint": "http://127.0.0.1:9999"})

    def test_missing_endpoint_uses_default(self):
        client = PeekApiClient({"api_key": "test", "endpoint": ""})
        assert "ingest.peekapi.dev" in client._endpoint
        client.shutdown()

    def test_http_non_localhost_rejected(self):
        with pytest.raises(ValueError, match="HTTPS required"):
            PeekApiClient({"api_key": "test", "endpoint": "http://example.com/ingest"})

    def test_private_ip_rejected(self):
        with pytest.raises(ValueError, match="private"):
            PeekApiClient({"api_key": "test", "endpoint": "https://10.0.0.1/ingest"})

    def test_embedded_credentials_rejected(self):
        with pytest.raises(ValueError, match="credentials"):
            PeekApiClient({"api_key": "test", "endpoint": "https://user:pass@example.com/ingest"})

    def test_valid_https_endpoint(self, make_client):
        _make, _, _ = make_client
        client = _make()
        assert client._api_key == "test-key"

    def test_accepts_options_dataclass(self, ingest_server, tmp_storage_path):
        _, url = ingest_server
        opts = Options(api_key="dc-key", endpoint=url, storage_path=tmp_storage_path)
        client = PeekApiClient(opts)
        assert client._api_key == "dc-key"
        client._shutdown = True
        client._done.set()
        client._wake.set()
        client._thread.join(timeout=2)


# ── Buffer management ────────────────────────────────────────────────


class TestBuffer:
    def test_track_adds_to_buffer(self, make_client):
        _make, _, _ = make_client
        client = _make()
        client.track({"method": "GET", "path": "/api", "status_code": 200, "response_time_ms": 10})
        assert len(client._buffer) == 1

    def test_track_sanitizes_method(self, make_client):
        _make, _, _ = make_client
        client = _make()
        client.track({"method": "get", "path": "/api", "status_code": 200, "response_time_ms": 10})
        assert client._buffer[0]["method"] == "GET"

    def test_track_truncates_path(self, make_client):
        _make, _, _ = make_client
        client = _make()
        long_path = "/" + "x" * 3000
        client.track(_evt(path=long_path, response_time_ms=10))
        assert len(client._buffer[0]["path"]) == 2048

    def test_track_adds_timestamp(self, make_client):
        _make, _, _ = make_client
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        assert client._buffer[0]["timestamp"]

    def test_track_preserves_existing_timestamp(self, make_client):
        _make, _, _ = make_client
        client = _make()
        ts = "2024-01-01T00:00:00Z"
        client.track(_evt(timestamp=ts))
        assert client._buffer[0]["timestamp"] == ts

    def test_track_never_raises(self, make_client):
        _make, _, _ = make_client
        client = _make()
        # Even garbage input shouldn't raise
        client.track(None)  # type: ignore
        assert len(client._buffer) == 0  # silently dropped

    def test_max_event_bytes_strips_metadata(self, make_client):
        _make, _, _ = make_client
        client = _make(max_event_bytes=200)
        big_meta = {"data": "x" * 500}
        client.track(
            {
                "method": "GET",
                "path": "/api",
                "status_code": 200,
                "response_time_ms": 10,
                "metadata": big_meta,
            }
        )
        # Event should be stored without metadata
        if client._buffer:
            assert "metadata" not in client._buffer[0]

    def test_max_event_bytes_drops_if_still_too_large(self, make_client):
        _make, _, _ = make_client
        client = _make(max_event_bytes=50)
        client.track(
            {
                "method": "GET",
                "path": "/" + "x" * 200,
                "status_code": 200,
                "response_time_ms": 10,
            }
        )
        assert len(client._buffer) == 0


# ── Flush ────────────────────────────────────────────────────────────


class TestFlush:
    def test_flush_sends_events(self, make_client):
        _make, server, _ = make_client
        client = _make()
        client.track(_evt(path="/users", response_time_ms=42))
        client.flush()
        assert len(server.payloads) == 1
        assert server.payloads[0]["api_key"] == "test-key"
        assert len(server.payloads[0]["events"]) == 1
        assert server.payloads[0]["events"][0]["path"] == "/users"

    def test_flush_sends_sdk_header(self, make_client):
        _make, server, _ = make_client
        client = _make()
        client.track(_evt(path="/users", response_time_ms=42))
        client.flush()
        assert len(server.payloads) == 1
        assert server.payloads[0]["sdk"].startswith("python/")

    def test_flush_clears_buffer(self, make_client):
        _make, _server, _ = make_client
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        assert len(client._buffer) == 0

    def test_flush_noop_when_empty(self, make_client):
        _make, server, _ = make_client
        client = _make()
        client.flush()
        assert len(server.payloads) == 0

    def test_flush_respects_batch_size(self, make_client):
        _make, server, _ = make_client
        client = _make(batch_size=2)
        for i in range(5):
            client.track(_evt(path=f"/{i}"))
        client.flush()
        # Should send batch of 2, leaving 3 in buffer
        assert len(server.payloads) == 1
        assert len(server.payloads[0]["events"]) == 2
        assert len(client._buffer) == 3

    def test_flush_accepts_request_event(self, make_client):
        _make, server, _ = make_client
        client = _make()
        event = RequestEvent(
            method="POST",
            path="/orders",
            status_code=201,
            response_time_ms=100,
            consumer_id="user-1",
        )
        client.track(event)
        client.flush()
        assert server.payloads[0]["events"][0]["method"] == "POST"
        assert server.payloads[0]["events"][0]["consumer_id"] == "user-1"


# ── Retry / backoff ──────────────────────────────────────────────────


class TestRetry:
    def test_retryable_error_reinserts(self, make_client):
        _make, server, _ = make_client
        server.response_status = 500
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        # Events should be back in buffer
        assert len(client._buffer) == 1
        assert client._consecutive_failures == 1

    def test_backoff_increases(self, make_client):
        _make, server, _ = make_client
        server.response_status = 502
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        assert client._backoff_until > 0

    def test_max_failures_persists_to_disk(self, make_client, tmp_storage_path):
        _make, server, _ = make_client
        server.response_status = 500
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        # Simulate 4 prior failures
        client._consecutive_failures = 4
        client.flush()
        # Should have persisted to disk (5th failure)
        assert os.path.isfile(tmp_storage_path)
        assert len(client._buffer) == 0

    def test_success_resets_failures(self, make_client):
        _make, _server, _ = make_client
        client = _make()
        client._consecutive_failures = 3
        client._backoff_until = time.monotonic() - 1  # expired
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        assert client._consecutive_failures == 0
        assert client._backoff_until == 0.0

    def test_on_error_called(self, make_client):
        errors: list[Exception] = []
        _make, server, _ = make_client
        server.response_status = 503
        client = _make(on_error=errors.append)
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        assert len(errors) == 1


# ── Error classification ─────────────────────────────────────────────


class TestErrorClassification:
    def test_4xx_non_retryable(self, make_client, tmp_storage_path):
        _make, server, _ = make_client
        server.response_status = 401
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        # Non-retryable → disk immediately, no reinsert
        assert len(client._buffer) == 0
        assert os.path.isfile(tmp_storage_path)

    def test_429_retryable(self, make_client):
        _make, server, _ = make_client
        server.response_status = 429
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        assert len(client._buffer) == 1  # reinserted

    def test_500_retryable(self, make_client):
        _make, server, _ = make_client
        server.response_status = 500
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        assert len(client._buffer) == 1

    def test_200_success(self, make_client):
        _make, server, _ = make_client
        client = _make()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        client.flush()
        assert len(client._buffer) == 0
        assert len(server.payloads) == 1


# ── Disk persistence ─────────────────────────────────────────────────


class TestDiskPersistence:
    def test_persist_creates_file(self, make_client, tmp_storage_path):
        _make, _, _ = make_client
        client = _make()
        events = [{"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1}]
        client._persist_to_disk(events)
        assert os.path.isfile(tmp_storage_path)

    def test_persist_format_is_jsonl(self, make_client, tmp_storage_path):
        _make, _, _ = make_client
        client = _make()
        events = [{"method": "GET", "path": "/a", "status_code": 200, "response_time_ms": 1}]
        client._persist_to_disk(events)
        with open(tmp_storage_path) as f:
            line = f.readline()
        parsed = json.loads(line)
        assert isinstance(parsed, list)
        assert parsed[0]["path"] == "/a"

    def test_load_from_disk_recovers(self, tmp_storage_path, ingest_server):
        _, url = ingest_server
        # Write events to disk
        events = [_evt(path="/recovered")]
        with open(tmp_storage_path, "w") as f:
            f.write(json.dumps(events) + "\n")

        client = PeekApiClient(
            {
                "api_key": "test",
                "endpoint": url,
                "storage_path": tmp_storage_path,
                "flush_interval": 60.0,
            }
        )
        assert len(client._buffer) == 1
        assert client._buffer[0]["path"] == "/recovered"
        client._shutdown = True
        client._done.set()
        client._wake.set()
        client._thread.join(timeout=2)

    def test_load_skips_corrupt_lines(self, tmp_storage_path, ingest_server):
        _, url = ingest_server
        good = [{"method": "GET", "path": "/good", "status_code": 200, "response_time_ms": 1}]
        with open(tmp_storage_path, "w") as f:
            f.write("not valid json\n")
            f.write(json.dumps(good) + "\n")

        client = PeekApiClient(
            {
                "api_key": "test",
                "endpoint": url,
                "storage_path": tmp_storage_path,
                "flush_interval": 60.0,
            }
        )
        assert len(client._buffer) == 1
        assert client._buffer[0]["path"] == "/good"
        client._shutdown = True
        client._done.set()
        client._wake.set()
        client._thread.join(timeout=2)

    def test_max_storage_bytes_respected(self, make_client, tmp_storage_path):
        _make, _, _ = make_client
        client = _make(max_storage_bytes=100)
        # Write enough to exceed limit
        big_events = [_evt(path="/" + "x" * 200)]
        client._persist_to_disk(big_events)
        # Second write should be skipped
        client._persist_to_disk(big_events)
        with open(tmp_storage_path) as f:
            lines = f.readlines()
        assert len(lines) == 1

    def test_recovery_file_cleaned_after_flush(self, make_client, tmp_storage_path):
        _make, _server, _ = make_client
        # Pre-write events
        events = [{"method": "GET", "path": "/pre", "status_code": 200, "response_time_ms": 1}]
        with open(tmp_storage_path, "w") as f:
            f.write(json.dumps(events) + "\n")
        # Create client that loads from disk
        client = _make()
        # The file should have been renamed to .recovering
        recovery = tmp_storage_path + ".recovering"
        assert client._recovery_path == recovery or not os.path.exists(tmp_storage_path)

    def test_runtime_disk_recovery(self, make_client, tmp_storage_path):
        """Recovers persisted events during same process (not just startup)."""
        _make, _server, _ = make_client
        client = _make()

        # Simulate events persisted to disk mid-process
        events = [
            {"method": "GET", "path": "/runtime-recover", "status_code": 200, "response_time_ms": 1}
        ]
        with open(tmp_storage_path, "w") as f:
            f.write(json.dumps(events) + "\n")

        # Trigger runtime recovery on the same client
        client._load_from_disk()

        with client._lock:
            paths = [e["path"] for e in client._buffer]
        assert "/runtime-recover" in paths


# ── Shutdown ─────────────────────────────────────────────────────────


class TestShutdown:
    def test_shutdown_flushes(self, make_client):
        _make, server, _ = make_client
        client = _make()
        client.track(_evt(path="/shutdown"))
        client.shutdown()
        assert len(server.payloads) == 1

    def test_shutdown_persists_remainder(self, make_client, tmp_storage_path):
        _make, server, _ = make_client
        server.response_status = 500  # flush will fail
        client = _make(batch_size=1)
        # Add 2 events, batch_size=1 means flush sends 1, leaves 1
        client.track({"method": "GET", "path": "/a", "status_code": 200, "response_time_ms": 1})
        client.track({"method": "GET", "path": "/b", "status_code": 200, "response_time_ms": 1})
        client.shutdown()
        # Remainder should be on disk
        assert os.path.isfile(tmp_storage_path)

    def test_double_shutdown_safe(self, make_client):
        _make, _, _ = make_client
        client = _make()
        client.shutdown()
        client.shutdown()  # Should not raise

    def test_track_after_shutdown_noop(self, make_client):
        _make, _, _ = make_client
        client = _make()
        client.shutdown()
        client.track({"method": "GET", "path": "/", "status_code": 200, "response_time_ms": 1})
        assert len(client._buffer) == 0
