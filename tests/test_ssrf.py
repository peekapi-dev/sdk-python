"""Tests for SSRF protection and endpoint validation."""

from __future__ import annotations

import pytest

from apidash._ssrf import is_private_ip, validate_endpoint


class TestIsPrivateIp:
    def test_rfc1918_10(self):
        assert is_private_ip("10.0.0.1") is True

    def test_rfc1918_172(self):
        assert is_private_ip("172.16.0.1") is True

    def test_rfc1918_192(self):
        assert is_private_ip("192.168.1.1") is True

    def test_cgnat(self):
        assert is_private_ip("100.64.0.1") is True

    def test_loopback(self):
        assert is_private_ip("127.0.0.1") is True

    def test_ipv6_loopback(self):
        assert is_private_ip("::1") is True

    def test_ipv4_mapped_ipv6_private(self):
        assert is_private_ip("::ffff:10.0.0.1") is True

    def test_ipv4_mapped_ipv6_public(self):
        assert is_private_ip("::ffff:8.8.8.8") is False

    def test_public_ip(self):
        assert is_private_ip("8.8.8.8") is False

    def test_public_ip2(self):
        assert is_private_ip("1.1.1.1") is False

    def test_hostname_not_ip(self):
        assert is_private_ip("example.com") is False

    def test_zero_address(self):
        assert is_private_ip("0.0.0.0") is True


class TestValidateEndpoint:
    def test_empty_endpoint(self):
        with pytest.raises(ValueError, match="endpoint is required"):
            validate_endpoint("")

    def test_http_non_localhost(self):
        with pytest.raises(ValueError, match="HTTPS required"):
            validate_endpoint("http://example.com/ingest")

    def test_http_localhost_allowed(self):
        result = validate_endpoint("http://127.0.0.1:3000/ingest")
        assert result == "http://127.0.0.1:3000/ingest"

    def test_https_public(self):
        result = validate_endpoint("https://api.example.com/ingest")
        assert result == "https://api.example.com/ingest"

    def test_private_ip_blocked(self):
        with pytest.raises(ValueError, match="private"):
            validate_endpoint("https://10.0.0.1/ingest")

    def test_credentials_blocked(self):
        with pytest.raises(ValueError, match="credentials"):
            validate_endpoint("https://user:pass@example.com/ingest")

    def test_malformed_url(self):
        with pytest.raises(ValueError):
            validate_endpoint("not-a-url")
