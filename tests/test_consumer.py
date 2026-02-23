"""Tests for consumer ID hashing and identification."""

from __future__ import annotations

from apidash._consumer import default_identify_consumer, hash_consumer_id


class TestHashConsumerId:
    def test_format(self):
        result = hash_consumer_id("Bearer token123")
        assert result.startswith("hash_")
        assert len(result) == 17  # "hash_" + 12 hex chars

    def test_deterministic(self):
        a = hash_consumer_id("same-input")
        b = hash_consumer_id("same-input")
        assert a == b

    def test_different_inputs(self):
        a = hash_consumer_id("Bearer abc")
        b = hash_consumer_id("Bearer xyz")
        assert a != b

    def test_hex_output(self):
        result = hash_consumer_id("test")
        hex_part = result[5:]  # strip "hash_"
        int(hex_part, 16)  # Should not raise


class TestDefaultIdentifyConsumer:
    def test_x_api_key_priority(self):
        headers = {"x-api-key": "my-key", "authorization": "Bearer tok"}
        assert default_identify_consumer(headers) == "my-key"

    def test_authorization_hashed(self):
        headers = {"authorization": "Bearer secret-token"}
        result = default_identify_consumer(headers)
        assert result is not None
        assert result.startswith("hash_")

    def test_no_headers_returns_none(self):
        assert default_identify_consumer({}) is None

    def test_empty_api_key_falls_through(self):
        headers = {"x-api-key": "", "authorization": "Bearer tok"}
        result = default_identify_consumer(headers)
        assert result is not None
        assert result.startswith("hash_")
