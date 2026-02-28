# PeekAPI — Python SDK

[![PyPI](https://img.shields.io/pypi/v/peekapi)](https://pypi.org/project/peekapi/)
[![license](https://img.shields.io/pypi/l/peekapi)](./LICENSE)
[![CI](https://github.com/peekapi-dev/sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/peekapi-dev/sdk-python/actions/workflows/ci.yml)

Zero-dependency Python SDK for [PeekAPI](https://peekapi.dev). Built-in middleware for ASGI (FastAPI, Starlette, Litestar), WSGI (Flask, Bottle), and Django.

## Install

```bash
pip install peekapi
```

## Quick Start

### FastAPI / Starlette (ASGI)

```python
from fastapi import FastAPI
from peekapi import PeekApiClient
from peekapi.middleware import PeekApiASGI

client = PeekApiClient({"api_key": "ak_live_xxx"})

app = FastAPI()
app.add_middleware(PeekApiASGI, client=client)
```

### Flask (WSGI)

```python
from flask import Flask
from peekapi import PeekApiClient
from peekapi.middleware import PeekApiWSGI

client = PeekApiClient({"api_key": "ak_live_xxx"})

app = Flask(__name__)
app.wsgi_app = PeekApiWSGI(app.wsgi_app, client=client)
```

### Django

```python
# settings.py
PEEKAPI = {
    "api_key": "ak_live_xxx",
}

MIDDLEWARE = [
    "peekapi.middleware.django.PeekApiMiddleware",
    # ... other middleware
]
```

### Standalone Client

```python
from peekapi import PeekApiClient

client = PeekApiClient({"api_key": "ak_live_xxx"})

client.track({
    "method": "GET",
    "path": "/api/users",
    "status_code": 200,
    "response_time_ms": 42,
})

# Graceful shutdown (flushes remaining events)
client.shutdown()
```

## Configuration

| Option | Default | Description |
|---|---|---|
| `api_key` | required | Your PeekAPI key |
| `endpoint` | PeekAPI cloud | Ingestion endpoint URL |
| `flush_interval` | `10.0` | Seconds between automatic flushes |
| `batch_size` | `100` | Events per HTTP POST (triggers flush) |
| `max_buffer_size` | `10000` | Max events held in memory |
| `max_storage_bytes` | `5242880` | Max disk fallback file size (5MB) |
| `max_event_bytes` | `65536` | Per-event size limit (64KB) |
| `storage_path` | auto | Custom path for JSONL persistence file |
| `debug` | `False` | Enable debug logging |
| `on_error` | `None` | Callback `(Exception) -> None` for flush errors |

## How It Works

1. Middleware intercepts every request/response
2. Captures method, path, status code, response time, request/response sizes, consumer ID
3. Events are buffered in memory and flushed in batches on a daemon thread
4. On network failure: exponential backoff with jitter, up to 5 retries
5. After max retries: events are persisted to a JSONL file on disk
6. On next startup: persisted events are recovered and re-sent
7. On SIGTERM/SIGINT: remaining buffer is flushed or persisted to disk

## Consumer Identification

By default, consumers are identified by:

1. `X-API-Key` header — stored as-is
2. `Authorization` header — hashed with SHA-256 (stored as `hash_<hex>`)

Override with the `identify_consumer` option to use any header or request property:

```python
client = PeekApiClient({
    "api_key": "...",
    "identify_consumer": lambda headers: headers.get("x-tenant-id"),
})
```

The callback receives a `dict[str, str]` of lowercase header names and should return a consumer ID string or `None`.

## Features

- **Zero runtime dependencies** — uses only Python stdlib
- **Background flush** — daemon thread with configurable interval and batch size
- **Disk persistence** — undelivered events saved to JSONL, recovered on restart
- **Exponential backoff** — with jitter, max 5 consecutive failures before disk fallback
- **SSRF protection** — private IP blocking, HTTPS enforcement (HTTP only for localhost)
- **Input sanitization** — path (2048), method (16), consumer_id (256) truncation
- **Per-event size limit** — strips metadata first, drops if still too large (default 64KB)
- **Graceful shutdown** — signal handlers (SIGTERM/SIGINT) with disk persistence

## Requirements

- Python >= 3.10

## Contributing

1. Fork & clone the repo
2. Install dev dependencies — `uv sync --no-install-project`
3. Run tests — `uv run pytest -v`
4. Lint & format — `uv run ruff check src/ tests/` / `uv run ruff format src/ tests/`
5. Submit a PR

## License

MIT
