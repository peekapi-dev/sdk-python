from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

# Matches private/reserved IPv4 ranges (fast path)
_PRIVATE_IP_RE = re.compile(
    r"^(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|0\.0\.0\.0"
    r")$"
)


def is_private_ip(host: str) -> bool:
    """Check if a hostname/IP is a private or reserved address.

    Covers: RFC 1918, CGNAT (100.64/10), loopback, link-local,
    IPv6 ULA/link-local, IPv4-mapped IPv6.
    """
    # Fast path regex
    if _PRIVATE_IP_RE.match(host):
        return True

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False

    if isinstance(addr, ipaddress.IPv6Address):
        # Check IPv4-mapped IPv6 (::ffff:x.x.x.x)
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return mapped.is_private or mapped.is_loopback or mapped.is_link_local
        return addr.is_private or addr.is_loopback or addr.is_link_local

    # IPv4 — ipaddress.is_private covers RFC 1918 + CGNAT + loopback + link-local
    return addr.is_private or addr.is_loopback or addr.is_link_local


def validate_endpoint(endpoint: str) -> str:
    """Validate and normalize the ingestion endpoint URL.

    Raises ValueError for:
      - Non-HTTPS URLs (except localhost)
      - Private/reserved IP addresses (SSRF protection)
      - Embedded credentials in URL
      - Malformed URLs
    """
    if not endpoint:
        raise ValueError("endpoint is required")

    parsed = urlparse(endpoint)

    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"Invalid endpoint URL: {endpoint}")

    hostname = parsed.hostname.lower()

    # Allow HTTP only for localhost
    is_localhost = hostname in ("localhost", "127.0.0.1", "::1")

    if parsed.scheme != "https" and not is_localhost:
        raise ValueError(f"HTTPS required for non-localhost endpoint: {endpoint}")

    # Reject embedded credentials
    if parsed.username or parsed.password:
        raise ValueError("Endpoint URL must not contain credentials")

    # SSRF check — skip for localhost
    if not is_localhost and is_private_ip(hostname):
        raise ValueError(f"Endpoint resolves to private/reserved IP: {hostname}")

    return endpoint
