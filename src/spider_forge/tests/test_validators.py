"""P0-A 驗收：validator 必須消滅已知假成功（remediation brief §6 P0-A、§8）。

跑法（從 /）：
    python -m spider_forge.tests.test_validators
全部離線、用固定 fixture，不碰網路。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from spider_forge.shared.quality_rules import validate_items

_FIX = Path(__file__).parent / "fixtures"
_TW = timezone(timedelta(hours=8))

CNYES_CFG = {
    "allowed_domains": ["news.cnyes.com", "api.cnyes.com"],
    "article_url_patterns": [r"/news/id/\d+"],
    "excluded_url_patterns": [r"/news/cat/"],
    "min_content_chars": 40,
    "min_valid_items": 5,
}
UDN_CFG = {
    "allowed_domains": ["money.udn.com", "udn.com"],
    "article_url_patterns": [r"/story/"],
    "excluded_url_patterns": [r"/stock/top", r"/rank/"],
    "min_content_chars": 40,
    "min_valid_items": 5,
}


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


# ── 測試（每個回 (通過?, 說明)）──────────────────────────────

def t_positive_control_cnyes_good():
    r = validate_items(_load("cnyes_good.json"), CNYES_CFG)
    return r["pass"] is True and r["unique_valid_count"] >= 5, f"pass={r['pass']} unique={r['unique_valid_count']}"


def t_udn_wrong_content_rejected():
    r = validate_items(_load("udn_stock_index.json"), UDN_CFG)
    reasons = r["reject_reasons"]
    ok = r["pass"] is False and ("url_excluded_pattern" in reasons or "url_not_article" in reasons)
    return ok, f"pass={r['pass']} valid={r['valid_count']} reasons={reasons}"


def t_identical_garbage_rejected():
    garbage = [{"title": "首頁", "url": "not-a-url", "content": "", "published_at": "2026-07-26"}] * 3
    r = validate_items(garbage, CNYES_CFG)
    return r["pass"] is False and r["valid_count"] == 0, f"pass={r['pass']} valid={r['valid_count']}"


def t_foreign_domain_rejected():
    items = [{"title": "看起來像新聞的標題但網域不對", "url": "https://evil.example.com/news/id/1",
              "content": "x" * 80, "published_at": datetime.now(_TW).isoformat()}]
    r = validate_items(items, CNYES_CFG)
    return r["valid_count"] == 0 and "url_foreign_domain" in r["reject_reasons"], f"reasons={r['reject_reasons']}"


def t_cnyes_empty_content_quarantined():
    good = _load("cnyes_good.json")
    mixed = good + [
        {"title": "這是一則沒有內文的文章標題", "url": "https://news.cnyes.com/news/id/9990001",
         "content": "", "published_at": datetime.now(_TW).isoformat()},
        {"title": "另一則內文為空白的文章", "url": "https://news.cnyes.com/news/id/9990002",
         "content": "   ", "published_at": datetime.now(_TW).isoformat()},
    ]
    r = validate_items(mixed, CNYES_CFG)
    # 空 content 必須被標記、不得計入 valid
    ok = r["reject_reasons"].get("content_empty", 0) == 2 and r["valid_count"] == len(good)
    return ok, f"valid={r['valid_count']} (期望 {len(good)}) reasons={r['reject_reasons']}"


def t_cna_duplicates_dont_inflate():
    one = {"title": "中央社同一則重複灌水的新聞", "url": "https://www.cna.com.tw/news/afe/202607260001.aspx",
           "content": "y" * 80, "published_at": datetime.now(_TW).isoformat()}
    items = [dict(one) for _ in range(10)]  # 10 筆全同
    cfg = {"allowed_domains": ["www.cna.com.tw", "cna.com.tw"],
           "article_url_patterns": [r"/news/\w+/\d+\.aspx"], "min_content_chars": 40, "min_valid_items": 5}
    r = validate_items(items, cfg)
    # 10 筆相同 → unique 只有 1 → 不足門檻 → 不得 pass
    return r["pass"] is False and r["unique_valid_count"] == 1, f"pass={r['pass']} unique={r['unique_valid_count']}"


def t_unreasonable_date_rejected():
    items = [{"title": "一則宣稱發表於1785年的新聞", "url": "https://news.cnyes.com/news/id/1785001",
              "content": "z" * 80, "published_at": "1785-01-01T00:00:00+08:00"}]
    r = validate_items(items, CNYES_CFG)
    return r["valid_count"] == 0 and "date_year_out_of_range" in r["reject_reasons"], f"reasons={r['reject_reasons']}"


def t_naive_date_rejected():
    items = [{"title": "一則沒有時區的新聞日期", "url": "https://news.cnyes.com/news/id/1785002",
              "content": "z" * 80, "published_at": "2026-07-26"}]
    r = validate_items(items, CNYES_CFG)
    return r["valid_count"] == 0 and "date_naive_no_tz" in r["reject_reasons"], f"reasons={r['reject_reasons']}"


def t_none_fields_rejected():
    items = [{"title": None, "url": None, "content": None, "published_at": None}]
    r = validate_items(items, CNYES_CFG)
    return r["pass"] is False and r["valid_count"] == 0, f"pass={r['pass']} reasons={r['reject_reasons']}"


def t_duplicate_flood_rejected():
    now = datetime.now(_TW).isoformat()
    five = [
        {"title": f"第{i}則有效但被大量重複的新聞", "url": f"https://news.cnyes.com/news/id/{9000 + i}",
         "content": "有效新聞內文" * 20, "published_at": now}
        for i in range(5)
    ]
    r = validate_items(five * 20, CNYES_CFG)
    ok = r["pass"] is False and r["unique_valid_count"] == 5 and r["unique_ratio"] == 0.05
    return ok, f"pass={r['pass']} unique={r['unique_valid_count']} ratio={r['unique_ratio']}"


def t_soft_block_page_rejected():
    now = datetime.now(_TW).isoformat()
    items = [
        {"title": f"看似有效的第{i}則新聞", "url": f"https://news.cnyes.com/news/id/{9100 + i}",
         "content": "Access Denied. Your request was blocked. " + "x" * 100,
         "published_at": now}
        for i in range(5)
    ]
    r = validate_items(items, CNYES_CFG)
    ok = r["pass"] is False and r["reject_reasons"].get("content_soft_block") == 5
    return ok, f"pass={r['pass']} reasons={r['reject_reasons']}"


def t_overlong_media_excerpt_rejected():
    cfg = {**CNYES_CFG, "max_content_chars": 6000, "min_valid_items": 1}
    item = {
        "title": "超過媒體忠實摘錄上限的新聞",
        "url": "https://news.cnyes.com/news/id/9199",
        "content": "財" * 6001,
        "published_at": datetime.now(_TW).isoformat(),
    }
    r = validate_items([item], cfg)
    ok = r["pass"] is False and r["reject_reasons"].get("content_too_long") == 1
    return ok, f"pass={r['pass']} reasons={r['reject_reasons']}"


def t_query_cannot_masquerade_as_article_path():
    cfg = {**CNYES_CFG, "excluded_url_patterns": [], "min_valid_items": 1}
    item = {"title": "列表頁透過 query 偽裝文章",
            "url": "https://news.cnyes.com/news/cat/tw_stock?next=/news/id/9999",
            "content": "這是足夠長但網址仍然不是文章路徑的內容" * 5,
            "published_at": datetime.now(_TW).isoformat()}
    r = validate_items([item], cfg)
    ok = r["pass"] is False and r["reject_reasons"].get("url_not_article") == 1
    return ok, f"pass={r['pass']} reasons={r['reject_reasons']}"


def t_default_https_port_allowed():
    cfg = {**CNYES_CFG, "min_valid_items": 1}
    item = {"title": "帶預設 HTTPS port 的合法新聞",
            "url": "https://news.cnyes.com:443/news/id/9201",
            "content": "合法且長度足夠的新聞內容" * 10,
            "published_at": datetime.now(_TW).isoformat()}
    r = validate_items([item], cfg)
    return r["pass"] is True, f"pass={r['pass']} reasons={r['reject_reasons']}"


def t_moneydj_query_ids_remain_unique():
    cfg = {
        "allowed_domains": ["www.moneydj.com", "moneydj.com"],
        "article_url_patterns": [r"/kmdj/news/news-viewer\.aspx"],
        "canonical_query_params": ["a"],
        "min_content_chars": 40,
        "min_valid_items": 5,
    }
    now = datetime.now(_TW).isoformat()
    items = [
        {"title": f"MoneyDJ 第{i}則文章標題",
         "url": f"https://www.moneydj.com/kmdj/news/news-viewer.aspx?a=NEWS{i}",
         "content": "MoneyDJ 有效文章內文" * 10, "published_at": now}
        for i in range(5)
    ]
    r = validate_items(items, cfg)
    ok = r["pass"] is True and r["unique_valid_count"] == 5
    return ok, f"pass={r['pass']} unique={r['unique_valid_count']} reasons={r['reject_reasons']}"


def t_official_record_id_can_supply_identity():
    cfg = {
        "allowed_domains": ["mops.twse.com.tw"],
        "identity_field": "source_record_id",
        "min_content_chars": 40,
        "min_valid_items": 5,
    }
    now = datetime.now(_TW).isoformat()
    lookup_url = "https://mops.twse.com.tw/mops/#/web/t05sr01_1"
    items = [
        {
            "title": f"{1000 + i} 測試公司｜重大訊息公告",
            "url": lookup_url,
            "content": "官方重大訊息完整說明內容" * 10,
            "published_at": now,
            "source_record_id": f"mops:sii:1150726:{1000 + i}:1",
        }
        for i in range(5)
    ]
    r = validate_items(items, cfg)
    ok = r["pass"] is True and r["unique_valid_count"] == 5
    return ok, f"pass={r['pass']} unique={r['unique_valid_count']} reasons={r['reject_reasons']}"


def t_missing_official_record_id_rejected():
    cfg = {
        "allowed_domains": ["mops.twse.com.tw"],
        "identity_field": "source_record_id",
        "min_content_chars": 40,
        "min_valid_items": 1,
    }
    item = {
        "title": "缺少來源資料列身分的官方公告",
        "url": "https://mops.twse.com.tw/mops/#/web/t05sr01_1",
        "content": "官方重大訊息完整說明內容" * 10,
        "published_at": datetime.now(_TW).isoformat(),
    }
    r = validate_items([item], cfg)
    ok = (
        r["pass"] is False
        and r["reject_reasons"].get("identity_missing") == 1
        and r["rejected_samples"][0]["reasons"] == ["identity_missing"]
    )
    return ok, f"pass={r['pass']} reasons={r['reject_reasons']}"


TESTS = [
    t_positive_control_cnyes_good,
    t_udn_wrong_content_rejected,
    t_identical_garbage_rejected,
    t_foreign_domain_rejected,
    t_cnyes_empty_content_quarantined,
    t_cna_duplicates_dont_inflate,
    t_unreasonable_date_rejected,
    t_naive_date_rejected,
    t_none_fields_rejected,
    t_duplicate_flood_rejected,
    t_soft_block_page_rejected,
    t_overlong_media_excerpt_rejected,
    t_query_cannot_masquerade_as_article_path,
    t_default_https_port_allowed,
    t_moneydj_query_ids_remain_unique,
    t_official_record_id_can_supply_identity,
    t_missing_official_record_id_rejected,
]


def main() -> int:
    failed = 0
    for t in TESTS:
        try:
            ok, detail = t()
        except Exception as e:
            ok, detail = False, f"EXCEPTION {e}"
        print(f"[{'PASS' if ok else 'FAIL'}] {t.__name__}: {detail}")
        if not ok:
            failed += 1
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
