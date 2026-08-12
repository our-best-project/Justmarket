"""crawler ingestion 的純函式契約測試。"""

from eventsignal.ingestion import (
    APPROVED_CRAWLERS,
    SPIDER_ALIASES,
    _resolve_spider,
    extract_related_tickers,
    make_article_id,
    normalize_article,
)


def test_default_spiders_resolve_against_registry():
    """P1-03 的回歸測試。

    這條測試曾經寫 assert set(APPROVED_CRAWLERS) == {"cnyes_finance", ...}——
    斷言的是**設定檔裡的錯名字**而不是 registry 的真名字，於是預設排程一啟動就
    KeyError，測試卻是綠的。改為驗證「預設設定的每個名字都解析得到 registry」，
    這才是使用者真正踩到的路徑。
    """
    default_spiders = ["cnyes", "twse_mops_finance"]      # flows.py / compose 的預設
    for name in default_spiders:
        assert _resolve_spider(name) in APPROVED_CRAWLERS
    # 歷史別名也要能解析（既有部署的 .env 還寫著舊名）
    for alias, target in SPIDER_ALIASES.items():
        assert _resolve_spider(alias) == target
        assert target in APPROVED_CRAWLERS


def test_article_id_is_stable_and_source_scoped():
    assert make_article_id("cnyes", "https://example.test/a") == make_article_id(
        "cnyes", "https://example.test/a"
    )
    assert make_article_id("cnyes", "https://example.test/a") != make_article_id(
        "mops", "https://example.test/a"
    )


def test_related_tickers_only_uses_title_contract():
    index = {
        "names": ["台積電"],
        "name_to_ticker": {"台積電": "2330"},
        "codes": {"2330", "2454"},
    }
    assert extract_related_tickers("台積電與聯發科(2454)法說", index) == ["2330", "2454"]


def test_normalize_article_preserves_runtime_contract():
    row = normalize_article(
        {
            "title": " 台積電   法說 ",
            "url": "https://example.test/news/1",
            "content": "<p>營收成長</p>",
            "published_at": "2026-07-28T09:00:00+08:00",
        },
        source="測試來源",
        source_prefix="test",
        source_type="media",
        ticker_index={
            "names": ["台積電"],
            "name_to_ticker": {"台積電": "2330"},
            "codes": {"2330"},
        },
    )
    assert row is not None
    assert row["title"] == "台積電 法說"
    assert row["content"] == "營收成長"
    assert row["related_tickers"] == '["2330"]'
    assert row["status"] == "pending"
