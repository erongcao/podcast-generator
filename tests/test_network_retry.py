"""Tests for the retry decorator."""
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from network_retry import retry_on_transient


def make_response(status_code, json_data=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def test_success_first_attempt_no_retry():
    @retry_on_transient(max_attempts=3, base_delay=0.01)
    def fn():
        return make_response(200)
    assert fn().status_code == 200


def test_retry_on_429_then_success():
    calls = [0]

    @retry_on_transient(max_attempts=3, base_delay=0.01)
    def fn():
        calls[0] += 1
        if calls[0] < 2:
            return make_response(429)
        return make_response(200)

    with patch("time.sleep"):
        resp = fn()
    assert resp.status_code == 200
    assert calls[0] == 2


def test_retry_on_500_then_success():
    calls = [0]

    @retry_on_transient(max_attempts=3, base_delay=0.01)
    def fn():
        calls[0] += 1
        if calls[0] < 3:
            return make_response(500)
        return make_response(200)

    with patch("time.sleep"):
        resp = fn()
    assert calls[0] == 3


def test_max_attempts_exhausted_returns_last_response():
    """After max retries, the last response is returned (for HTTP errors)."""
    calls = [0]

    @retry_on_transient(max_attempts=3, base_delay=0.01)
    def fn():
        calls[0] += 1
        return make_response(503)

    with patch("time.sleep"):
        resp = fn()
    assert resp.status_code == 503
    assert calls[0] == 3


def test_retry_on_connection_error():
    calls = [0]

    @retry_on_transient(max_attempts=3, base_delay=0.01)
    def fn():
        calls[0] += 1
        if calls[0] < 2:
            raise requests.ConnectionError("network down")
        return make_response(200)

    with patch("time.sleep"):
        resp = fn()
    assert resp.status_code == 200
    assert calls[0] == 2


def test_max_attempts_exhausted_raises_last_exception():
    calls = [0]

    @retry_on_transient(max_attempts=3, base_delay=0.01)
    def fn():
        calls[0] += 1
        raise requests.Timeout("timed out")

    with patch("time.sleep"):
        with pytest.raises(requests.Timeout):
            fn()
    assert calls[0] == 3


def test_no_retry_on_non_retryable_status():
    """4xx (other than 429) should not retry."""
    calls = [0]

    @retry_on_transient(max_attempts=3, base_delay=0.01)
    def fn():
        calls[0] += 1
        return make_response(404)

    with patch("time.sleep"):
        resp = fn()
    assert resp.status_code == 404
    assert calls[0] == 1


def test_no_retry_on_non_retryable_exception():
    calls = [0]

    @retry_on_transient(max_attempts=3, base_delay=0.01)
    def fn():
        calls[0] += 1
        raise ValueError("not a network error")

    with patch("time.sleep"):
        with pytest.raises(ValueError):
            fn()
    assert calls[0] == 1


def test_exponential_backoff_timing():
    """Verify backoff delays grow exponentially (with jitter)."""
    sleeps = []

    @retry_on_transient(max_attempts=4, base_delay=1.0, backoff_factor=2.0, jitter=0)
    def fn():
        return make_response(500)

    with patch("time.sleep", side_effect=lambda d: sleeps.append(d)):
        fn()

    # 3 retries between 4 attempts → delays should be 1, 2, 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_backoff_capped_at_max_delay():
    sleeps = []

    @retry_on_transient(max_attempts=5, base_delay=1.0, backoff_factor=10.0,
                        max_delay=5.0, jitter=0)
    def fn():
        return make_response(500)

    with patch("time.sleep", side_effect=lambda d: sleeps.append(d)):
        fn()

    # 1, 10→5 (cap), 100→5 (cap), 1000→5 (cap)
    assert sleeps == [1.0, 5.0, 5.0, 5.0]