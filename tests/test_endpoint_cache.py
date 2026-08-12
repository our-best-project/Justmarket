"""端點快取的整合測試。以替身取代建構函式，不連 DB、不需 TestClient。"""

from types import SimpleNamespace

import pytest

from eventsignal.api import demo, market


@pytest.fixture(autouse=True)
def clear_caches():
    demo._CACHE.clear()
    market._CACHE.clear()
    yield
    demo._CACHE.clear()
    market._CACHE.clear()


def _request(base_url="http://testserver/"):
    """bootstrap 只用到 request.base_url，不需要真的 Request 物件。"""
    return SimpleNamespace(base_url=base_url)


def test_bootstrap_second_request_skips_build(monkeypatch):
    calls = []
    monkeypatch.setattr(demo, "_build_bootstrap",
                        lambda limit, origin: calls.append((limit, origin)) or {"events": []})

    demo.bootstrap(_request(), limit=12)
    demo.bootstrap(_request(), limit=12)

    assert len(calls) == 1


def test_bootstrap_different_limit_rebuilds(monkeypatch):
    calls = []
    monkeypatch.setattr(demo, "_build_bootstrap",
                        lambda limit, origin: calls.append((limit, origin)) or {"events": []})

    demo.bootstrap(_request(), limit=12)
    demo.bootstrap(_request(), limit=30)

    assert len(calls) == 2


def test_bootstrap_different_origin_rebuilds(monkeypatch):
    """圖片 URL 內嵌 origin，不同來源不能共用同一份快取。"""
    calls = []
    monkeypatch.setattr(demo, "_build_bootstrap",
                        lambda limit, origin: calls.append((limit, origin)) or {"events": []})

    demo.bootstrap(_request("http://a.test/"), limit=12)
    demo.bootstrap(_request("http://b.test/"), limit=12)

    assert len(calls) == 2


def test_global_second_request_skips_build(monkeypatch):
    calls = []
    monkeypatch.setattr(market, "_build_global", lambda: calls.append(1) or [])

    market.market_global()
    market.market_global()

    assert len(calls) == 1
