from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import random
import re
import signal
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .types import Options, RequestEvent
from ._ssrf import validate_endpoint

logger = logging.getLogger("apidash")

# --- Constants ---
DEFAULT_FLUSH_INTERVAL = 10.0  # seconds
DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_BUFFER_SIZE = 10_000
DEFAULT_MAX_STORAGE_BYTES = 5_242_880  # 5 MB
DEFAULT_MAX_EVENT_BYTES = 65_536  # 64 KB
MAX_PATH_LENGTH = 2_048
MAX_METHOD_LENGTH = 16
MAX_CONSUMER_ID_LENGTH = 256
MAX_CONSECUTIVE_FAILURES = 5
BASE_BACKOFF_S = 1.0
SEND_TIMEOUT_S = 5
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


class _RetryableError(Exception):
    """Marks a send failure as retryable (5xx/429/network)."""


class _NonRetryableError(Exception):
    """Marks a send failure as non-retryable (4xx)."""


class ApiDashClient:
    """Buffered analytics client — zero runtime dependencies.

    Events are accumulated in memory and flushed to the ingestion endpoint
    on a background daemon thread.  Undelivered events are persisted to
    disk (JSONL) and recovered on the next startup.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, options: Options | dict[str, Any]) -> None:
        if isinstance(options, dict):
            options = Options(**options)

        # --- Validate ---
        if not options.api_key:
            raise ValueError("api_key is required")
        if _CONTROL_CHAR_RE.search(options.api_key):
            raise ValueError("api_key contains invalid control characters")

        endpoint = options.endpoint
        if not endpoint:
            raise ValueError("endpoint is required")
        self._endpoint = validate_endpoint(endpoint)

        # --- Apply defaults ---
        self._api_key = options.api_key
        self._flush_interval = options.flush_interval or DEFAULT_FLUSH_INTERVAL
        self._batch_size = options.batch_size or DEFAULT_BATCH_SIZE
        self._max_buffer_size = options.max_buffer_size or DEFAULT_MAX_BUFFER_SIZE
        self._max_storage_bytes = options.max_storage_bytes or DEFAULT_MAX_STORAGE_BYTES
        self._max_event_bytes = options.max_event_bytes or DEFAULT_MAX_EVENT_BYTES
        self._debug = options.debug
        self._on_error = options.on_error

        # --- Storage path ---
        if options.storage_path:
            self._storage_path = options.storage_path
        else:
            h = hashlib.sha256(self._endpoint.encode()).hexdigest()[:12]
            self._storage_path = os.path.join(tempfile.gettempdir(), f"apidash-events-{h}.jsonl")

        self._recovery_path: str | None = None

        # --- Internal state ---
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._in_flight = False
        self._consecutive_failures = 0
        self._backoff_until = 0.0
        self._shutdown = False

        # --- Load persisted events ---
        self._load_from_disk()

        # --- Background flush thread ---
        self._done = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="apidash-flush")
        self._thread.start()

        # --- Signal handlers ---
        self._original_handlers: dict[int, Any] = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    prev = signal.signal(sig, self._signal_handler)
                    self._original_handlers[sig] = prev
                except (OSError, ValueError):
                    pass
        else:
            atexit.register(self._atexit_handler)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def track(self, event: RequestEvent | dict[str, Any]) -> None:
        """Buffer an analytics event.  Never raises."""
        try:
            self._track_inner(event)
        except Exception:
            if self._debug:
                logger.exception("apidash: track() error")

    def flush(self) -> None:
        """Flush buffered events synchronously (blocks until complete)."""
        batch = self._drain_batch()
        if not batch:
            return
        self._do_flush(batch)

    def shutdown(self) -> None:
        """Graceful shutdown: stop thread, final flush, persist remainder."""
        if self._shutdown:
            return
        self._shutdown = True

        # Remove signal handlers
        for sig, handler in self._original_handlers.items():
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                pass
        self._original_handlers.clear()

        # Stop background thread
        self._done.set()
        self._wake.set()
        self._thread.join(timeout=5.0)

        # Final flush
        self.flush()

        # Persist remainder
        with self._lock:
            remaining = list(self._buffer)
            self._buffer.clear()
        if remaining:
            self._persist_to_disk(remaining)

    # ------------------------------------------------------------------
    # Track internals
    # ------------------------------------------------------------------

    def _track_inner(self, event: RequestEvent | dict[str, Any]) -> None:
        if self._shutdown:
            return

        if isinstance(event, RequestEvent):
            d = asdict(event)
        else:
            d = dict(event)

        # Sanitize
        d["method"] = str(d.get("method", ""))[:MAX_METHOD_LENGTH].upper()
        d["path"] = str(d.get("path", ""))[:MAX_PATH_LENGTH]
        if d.get("consumer_id"):
            d["consumer_id"] = str(d["consumer_id"])[:MAX_CONSUMER_ID_LENGTH]

        # Timestamp
        if not d.get("timestamp"):
            d["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Per-event size limit
        raw = json.dumps(d, separators=(",", ":"))
        if len(raw.encode()) > self._max_event_bytes:
            d.pop("metadata", None)
            raw = json.dumps(d, separators=(",", ":"))
            if len(raw.encode()) > self._max_event_bytes:
                if self._debug:
                    logger.warning("apidash: event too large, dropping (%d bytes)", len(raw))
                return

        with self._lock:
            if len(self._buffer) >= self._max_buffer_size:
                # Buffer full — trigger flush instead of dropping
                self._wake.set()
                return
            self._buffer.append(d)
            size = len(self._buffer)

        if size >= self._batch_size:
            self._wake.set()

    # ------------------------------------------------------------------
    # Flush internals
    # ------------------------------------------------------------------

    def _drain_batch(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._buffer or self._in_flight:
                return []
            now = time.monotonic()
            if now < self._backoff_until:
                return []
            batch = self._buffer[: self._batch_size]
            self._buffer = self._buffer[self._batch_size :]
            self._in_flight = True
        return batch

    def _do_flush(self, batch: list[dict[str, Any]]) -> None:
        try:
            self._send(batch)
            # Success
            with self._lock:
                self._consecutive_failures = 0
                self._backoff_until = 0.0
                self._in_flight = False
            self._cleanup_recovery_file()
            if self._debug:
                logger.debug("apidash: flushed %d events", len(batch))
        except _NonRetryableError as exc:
            with self._lock:
                self._in_flight = False
            self._persist_to_disk(batch)
            self._call_on_error(exc)
            if self._debug:
                logger.warning("apidash: non-retryable error, persisted to disk: %s", exc)
        except Exception as exc:
            with self._lock:
                self._consecutive_failures += 1
                failures = self._consecutive_failures

                if failures >= MAX_CONSECUTIVE_FAILURES:
                    self._consecutive_failures = 0
                    self._in_flight = False
                    self._persist_to_disk(batch)
                else:
                    # Re-insert events at the front
                    space = self._max_buffer_size - len(self._buffer)
                    reinsert = batch[:space]
                    self._buffer = reinsert + self._buffer
                    # Exponential backoff with jitter
                    delay = BASE_BACKOFF_S * (2 ** (failures - 1)) * random.uniform(0.5, 1.0)
                    self._backoff_until = time.monotonic() + delay
                    self._in_flight = False

            self._call_on_error(exc)
            if self._debug:
                logger.warning(
                    "apidash: flush failed (attempt %d): %s", failures, exc
                )

    def _send(self, events: list[dict[str, Any]]) -> None:
        body = json.dumps(events, separators=(",", ":")).encode()
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:
                resp.read()  # drain body
                if resp.status < 200 or resp.status >= 300:
                    raise _NonRetryableError(f"HTTP {resp.status}")
        except urllib.error.HTTPError as exc:
            status = exc.code
            snippet = ""
            try:
                snippet = exc.read(1024).decode(errors="replace")
            except Exception:
                pass
            if status in RETRYABLE_STATUS_CODES:
                raise _RetryableError(f"HTTP {status}: {snippet}") from exc
            raise _NonRetryableError(f"HTTP {status}: {snippet}") from exc
        except urllib.error.URLError as exc:
            raise _RetryableError(f"Network error: {exc.reason}") from exc

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._done.is_set():
            self._wake.wait(timeout=self._flush_interval)
            self._wake.clear()
            if self._done.is_set():
                break
            batch = self._drain_batch()
            if batch:
                self._do_flush(batch)

    # ------------------------------------------------------------------
    # Disk persistence
    # ------------------------------------------------------------------

    def _persist_to_disk(self, events: list[dict[str, Any]]) -> None:
        try:
            path = self._storage_path
            # Check file size
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size >= self._max_storage_bytes:
                if self._debug:
                    logger.warning("apidash: storage file full, dropping %d events", len(events))
                return

            line = json.dumps(events, separators=(",", ":")) + "\n"
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(fd, line.encode())
            finally:
                os.close(fd)
        except Exception:
            if self._debug:
                logger.exception("apidash: disk persist failed")

    def _load_from_disk(self) -> None:
        # Try recovery file first (crash-before-flush leftover)
        recovery = self._storage_path + ".recovering"
        for path in (recovery, self._storage_path):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                events: list[dict[str, Any]] = []
                for line in content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                        if isinstance(parsed, list):
                            events.extend(parsed)
                        elif isinstance(parsed, dict):
                            events.append(parsed)
                    except json.JSONDecodeError:
                        continue
                    if len(events) >= self._max_buffer_size:
                        break

                if events:
                    self._buffer.extend(events[: self._max_buffer_size])
                    if self._debug:
                        logger.debug("apidash: loaded %d events from disk", len(events))

                # Rename to .recovering so we don't double-load
                if path == self._storage_path:
                    rpath = self._storage_path + ".recovering"
                    try:
                        os.rename(path, rpath)
                    except OSError:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                    self._recovery_path = rpath
                else:
                    self._recovery_path = path
                break  # loaded from one file, done
            except Exception:
                if self._debug:
                    logger.exception("apidash: disk load failed from %s", path)

    def _cleanup_recovery_file(self) -> None:
        if self._recovery_path:
            try:
                os.unlink(self._recovery_path)
            except OSError:
                pass
            self._recovery_path = None

    # ------------------------------------------------------------------
    # Signal / atexit handlers
    # ------------------------------------------------------------------

    def _signal_handler(self, signum: int, _frame: Any) -> None:
        self._shutdown_sync()
        # Re-raise with original handler
        handler = self._original_handlers.get(signum, signal.SIG_DFL)
        if callable(handler):
            handler(signum, _frame)
        elif handler == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    def _atexit_handler(self) -> None:
        self._shutdown_sync()

    def _shutdown_sync(self) -> None:
        """Synchronous shutdown — persist buffer to disk immediately."""
        if self._shutdown:
            return
        self._shutdown = True
        self._done.set()
        self._wake.set()

        with self._lock:
            remaining = list(self._buffer)
            self._buffer.clear()
        if remaining:
            self._persist_to_disk(remaining)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _call_on_error(self, exc: Exception) -> None:
        if self._on_error:
            try:
                self._on_error(exc)
            except Exception:
                pass
