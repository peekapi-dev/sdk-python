# API Usage Dashboard — Python SDK

Zero-dependency Python SDK for [API Usage Dashboard](https://github.com/api-usage-dashboard). Tracks API request analytics with built-in middleware for ASGI (FastAPI, Starlette, Litestar), WSGI (Flask, Bottle), and Django.

## Install

```bash
pip install apidash
```

## Quick Start

### FastAPI / Starlette (ASGI)

```python
from fastapi import FastAPI
from apidash import ApiDashClient
from apidash.middleware import ApiDashASGI

client = ApiDashClient({
    "api_key": "your-api-key",
    "endpoint": "https://your-project.supabase.co/functions/v1/ingest",
})

app = FastAPI()
app.add_middleware(ApiDashASGI, client=client)
```

### Flask (WSGI)

```python
from flask import Flask
from apidash import ApiDashClient
from apidash.middleware import ApiDashWSGI

client = ApiDashClient({
    "api_key": "your-api-key",
    "endpoint": "https://your-project.supabase.co/functions/v1/ingest",
})

app = Flask(__name__)
app.wsgi_app = ApiDashWSGI(app.wsgi_app, client=client)
```

### Django

```python
# settings.py
APIDASH = {
    "api_key": "your-api-key",
    "endpoint": "https://your-project.supabase.co/functions/v1/ingest",
}

MIDDLEWARE = [
    "apidash.middleware.django.ApiDashMiddleware",
    # ...
]
```

### Standalone Client

```python
from apidash import ApiDashClient

client = ApiDashClient({
    "api_key": "your-api-key",
    "endpoint": "https://your-project.supabase.co/functions/v1/ingest",
})

client.track({
    "method": "GET",
    "path": "/api/users",
    "status_code": 200,
    "response_time_ms": 42,
})

# Graceful shutdown (flushes remaining events)
client.shutdown()
```

## Features

- **Zero runtime dependencies** — uses only Python stdlib
- **Background flush** — daemon thread with configurable interval and batch size
- **Disk persistence** — undelivered events saved to JSONL, recovered on restart
- **Exponential backoff** — with jitter, max 5 consecutive failures before disk fallback
- **SSRF protection** — private IP blocking, HTTPS enforcement (HTTP only for localhost)
- **Input sanitization** — path (2048), method (16), consumer_id (256) truncation
- **Per-event size limit** — strips metadata first, drops if still too large (default 64KB)
- **Graceful shutdown** — signal handlers (SIGTERM/SIGINT) with disk persistence
- **Consumer identification** — auto-extracts from `x-api-key` or `Authorization` header

## Configuration

| Option | Default | Description |
|---|---|---|
| `api_key` | required | Your API key |
| `endpoint` | required | Ingestion endpoint URL |
| `flush_interval` | `10.0` | Seconds between flushes |
| `batch_size` | `100` | Max events per flush |
| `max_buffer_size` | `10000` | Max in-memory events |
| `max_storage_bytes` | `5242880` | Max disk file size (5MB) |
| `max_event_bytes` | `65536` | Per-event size limit (64KB) |
| `debug` | `False` | Enable debug logging |
| `on_error` | `None` | Error callback `(Exception) -> None` |
| `storage_path` | auto | Custom disk persistence path |

## Requirements

- Python >= 3.10

## License

MIT
