from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RequestEvent:
    method: str
    path: str
    status_code: int
    response_time_ms: float
    request_size: int = 0
    response_size: int = 0
    consumer_id: str | None = None
    metadata: dict[str, Any] | None = None
    timestamp: str = ""


@dataclass
class Options:
    api_key: str
    endpoint: str = ""
    flush_interval: float = 10.0
    batch_size: int = 100
    max_buffer_size: int = 10_000
    max_storage_bytes: int = 5_242_880  # 5 MB
    max_event_bytes: int = 65_536  # 64 KB
    debug: bool = False
    identify_consumer: Callable[..., str | None] | None = None
    storage_path: str = ""
    on_error: Callable[[Exception], None] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
