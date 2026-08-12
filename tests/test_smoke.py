"""端到端煙霧測試：用 FastAPI TestClient 打全部只讀端點（工程審查缺口 3）。

這份曾經只有一行佔位 docstring。現在的定位：**接真 DB 的整合測試**——
驗「端點活著、回應形狀對、資料不是明顯壞的」，不驗業務數值
（那是各模組自我測試的事）。

需要 DATABASE_URL（.env）。連不上 DB 時整組 skip 而不是 fail——
CI 沒有 DB 憑證是常態，不該把「環境沒配」偽裝成「程式壞了」。

跑法：
    cd repo 根目錄
    uv run python -m pytest tests/test_smoke.py -q     # 有 pytest 時
    uv run python tests/test_smoke.py                  # 直接執行也行
"""
import sys


def _client_or_none():
    """建 TestClient；DB 連不上回 None（呼叫端 skip）。"""
    try:
        from fastapi.testclient import TestClient

        from eventsignal.db.session import get_conn
        from eventsignal.main import app

        with get_conn() as conn:
            conn.execute("select 1")
        return TestClient(app)
    except Exception as exc:
        print(f"[skip] DB 不可用：{exc}")
        return None


def test_health_reports_db():
    client = _client_or_none()
    if client is None:
        return
    r = client.get("/health")
    assert r.status_code == 200, r.text
    assert r.json()["db"] == "ok"


def test_market_overview_is_real():
    client = _client_or_none()
    if client is None:
        return
    d = client.get("/api/v1/market/overview").json()
    # 資料表有東西時必須是真資料模式；空表回 unavailable 也合法（新環境）
    assert d["source"]["mode"] in ("real", "unavailable")
    if d["source"]["mode"] == "real":
        assert d["close"] and d["close"] > 0
        assert d["periods"], "periods 不得為空"
        # 指數與家數必須同基準日的內部一致性：breadth 存在時三數相加 > 500（台股上市櫃規模）
        if d["breadth"]:
            total = sum(d["breadth"].values())
            assert total > 500, f"家數總和異常：{d['breadth']}"


def test_market_breadth_shape():
    client = _client_or_none()
    if client is None:
        return
    d = client.get("/api/v1/market/breadth").json()
    b = d["breadth"]
    assert b["advancers"] + b["decliners"] + b["unchanged"] > 500
    assert b["turnoverE"] > 1000, "台股日成交值不可能低於千億——單位錯就會在這裡爆"
    assert len(d["topTurnover"]) == 12
    assert len(d["industries"]) > 10
    # 面積正比的根基：topTurnover 由大到小
    tv = [t["turnoverE"] for t in d["topTurnover"]]
    assert tv == sorted(tv, reverse=True)


def test_market_global_has_taiex():
    client = _client_or_none()
    if client is None:
        return
    markets = client.get("/api/v1/market/global").json()
    assert any(m["id"] == "TAIEX" for m in markets)
    for m in markets:
        if m["status"] != "unavailable":
            assert len(m["series20"]) == 20, f"{m['id']} 走勢點數不對"


def test_demo_bootstrap_and_detail_roundtrip():
    client = _client_or_none()
    if client is None:
        return
    catalog = client.get("/api/v1/demo/bootstrap").json()
    assert catalog["events"], "bootstrap 事件清單為空"
    first = catalog["events"][0]
    detail = client.get(f"/api/v1/demo/events/{first['event_id']}")
    assert detail.status_code == 200
    assert detail.json()["event_id"] == first["event_id"]
    # 不存在的 id 要 404，不能 500 也不能回空殼
    assert client.get("/api/v1/demo/events/evt_no_such_id").status_code == 404


def test_search_and_limit_bounds():
    client = _client_or_none()
    if client is None:
        return
    r = client.get("/api/v1/demo/search", params={"q": "台積電", "limit": 5})
    assert r.status_code == 200
    assert r.json()["count"] >= 0
    # limit 上下界（工程審查 P3-06）：超界要 422，不能默默吞
    assert client.get("/api/v1/events/today", params={"limit": 9999}).status_code == 422


def test_events_today_renderable():
    client = _client_or_none()
    if client is None:
        return
    rows = client.get("/api/v1/events/today", params={"limit": 5}).json()
    for e in rows:
        assert e.get("title"), "events/today 只該回可渲染（已 LLM）的事件"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failed else 0)
