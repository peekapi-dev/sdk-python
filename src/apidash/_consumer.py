from __future__ import annotations

import hashlib


def hash_consumer_id(raw: str) -> str:
    """SHA-256 hash truncated to 12 hex chars, prefixed with 'hash_'."""
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"hash_{digest}"


def default_identify_consumer(headers: dict[str, str]) -> str | None:
    """Identify consumer from request headers.

    Priority:
      1. x-api-key (stored as-is)
      2. Authorization (hashed — contains credentials)
    """
    api_key = headers.get("x-api-key")
    if api_key:
        return api_key

    auth = headers.get("authorization")
    if auth:
        return hash_consumer_id(auth)

    return None
