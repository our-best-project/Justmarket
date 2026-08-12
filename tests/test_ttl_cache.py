"""app/core/cache.py 的 TTLCache 單元測試。純函式，不需 DB、不需 psycopg。"""

import pytest

from eventsignal.core.cache import TTLCache


class FakeClock:
    """可手動推進的時鐘 —— 測 TTL 不必真的 sleep，測試才跑得快又穩定。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def counting(value):
    """回傳 (compute, calls)。calls[0] 記錄 compute 被實際呼叫幾次。"""
    calls = [0]

    def compute():
        calls[0] += 1
        return value

    return compute, calls


def test_first_call_computes():
    cache = TTLCache(ttl_seconds=60, clock=FakeClock())
    compute, calls = counting("v1")
    assert cache.get_or_compute("k", compute) == "v1"
    assert calls[0] == 1


def test_second_call_within_ttl_uses_cache():
    clock = FakeClock()
    cache = TTLCache(ttl_seconds=60, clock=clock)
    compute, calls = counting("v1")
    cache.get_or_compute("k", compute)
    clock.advance(59)
    assert cache.get_or_compute("k", compute) == "v1"
    assert calls[0] == 1


def test_call_after_ttl_recomputes():
    clock = FakeClock()
    cache = TTLCache(ttl_seconds=60, clock=clock)
    compute, calls = counting("v1")
    cache.get_or_compute("k", compute)
    clock.advance(61)
    cache.get_or_compute("k", compute)
    assert calls[0] == 2


def test_different_keys_are_isolated():
    cache = TTLCache(ttl_seconds=60, clock=FakeClock())
    assert cache.get_or_compute("a", lambda: "A") == "A"
    assert cache.get_or_compute("b", lambda: "B") == "B"
    # a 的快取沒有被 b 覆蓋
    assert cache.get_or_compute("a", lambda: "changed") == "A"


def test_zero_ttl_disables_cache():
    cache = TTLCache(ttl_seconds=0, clock=FakeClock())
    compute, calls = counting("v1")
    cache.get_or_compute("k", compute)
    cache.get_or_compute("k", compute)
    assert calls[0] == 2


def test_exception_is_not_cached():
    """失敗不進快取，否則一次 DB 故障會被凍結整個 TTL。"""
    cache = TTLCache(ttl_seconds=60, clock=FakeClock())

    def boom():
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        cache.get_or_compute("k", boom)
    assert cache.get_or_compute("k", lambda: "ok") == "ok"


def test_clear_empties_cache():
    cache = TTLCache(ttl_seconds=60, clock=FakeClock())
    compute, calls = counting("v1")
    cache.get_or_compute("k", compute)
    cache.clear()
    cache.get_or_compute("k", compute)
    assert calls[0] == 2
