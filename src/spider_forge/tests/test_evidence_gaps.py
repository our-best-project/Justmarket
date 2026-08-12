"""spec v2 §2：EvidencePack 補三缺口——翻頁機制、published_at 格式/時區、可重播 headers。

跑法（從 /）：
    python -m spider_forge.tests.test_evidence_gaps
"""

from __future__ import annotations

from spider_forge.shared import evidence as evidence_stage
from spider_forge.shared.prompts import DEFAULT_TARGET_SCHEMA

# ════════════════════════ published_at 格式/時區分類 ════════════════════════


def t_classify_datetime_covers_known_formats():
    cases = {
        "2026-07-27T12:00:00+08:00": ("iso8601_tz", True),
        "2026-07-27T12:00:00Z": ("iso8601_tz", True),
        "2026-07-27T12:00:00": ("iso8601_naive", False),
        "2026-07-27": ("date_only", False),
        "115/07/27": ("roc_year", False),
        "Mon, 20 Jul 2026 10:00:00 +0100": ("rfc822", True),
        "3 小時前": ("relative", False),
        "1785146400": ("epoch_seconds", True),
        "1785146400000": ("epoch_millis", True),
        None: ("missing", None),
        "": ("missing", None),
    }
    bad = {
        raw: evidence_stage._classify_datetime(raw)
        for raw, expected in cases.items()
        if evidence_stage._classify_datetime(raw) != expected
    }
    return not bad, f"mismatches={bad}"


# ════════════════════════ 翻頁機制偵測 ════════════════════════


def t_detect_pagination_query_param():
    result = evidence_stage._detect_pagination(
        chosen_api="https://example.com/api/news?page=1&size=20",
        entry_url="https://example.com/news",
        api_body="",
        entry_html="",
    )
    ok = result["type"] == "query_param" and result["param"] == "page"
    return ok, f"pagination={result}"


def t_detect_pagination_cursor_from_body():
    result = evidence_stage._detect_pagination(
        chosen_api="https://example.com/api/news",
        entry_url="https://example.com/news",
        api_body='{"items":[{"title":"x"}],"next_cursor":"abc123"}',
        entry_html="",
    )
    ok = result["type"] == "cursor" and result["marker"] == "next_cursor"
    return ok, f"pagination={result}"


def t_detect_pagination_next_link_from_html():
    result = evidence_stage._detect_pagination(
        chosen_api="",
        entry_url="https://example.com/news",
        api_body="",
        entry_html='<head><link rel="next" href="/news?p=2"></head>',
    )
    ok = (
        result["type"] == "next_link"
        and result["example_url"] == "https://example.com/news?p=2"
    )
    return ok, f"pagination={result}"


def t_detect_pagination_none_when_no_signal():
    result = evidence_stage._detect_pagination(
        chosen_api="https://example.com/api/news",
        entry_url="https://example.com/news",
        api_body='{"items":[{"title":"x"}]}',
        entry_html="<html><body>no pager</body></html>",
    )
    ok = result["type"] == "none_detected"
    return ok, f"pagination={result}"


# ════════════════════════ published_at 樣本蒐集 ════════════════════════


def t_probe_published_at_from_feed_and_detail_flags_naive():
    probe = evidence_stage._probe_published_at(
        feed_candidates=[
            {"feed_items": [{"published_at": "Mon, 20 Jul 2026 10:00:00 +0100"}]}
        ],
        detail_samples=[
            {
                "body_excerpt": (
                    '<meta property="article:published_time" content="2026-07-27T12:00:00">'
                )
            }
        ],
        api_body="",
    )
    formats = {row["format"] for row in probe["raw_samples"]}
    ok = (
        "rfc822" in formats
        and "iso8601_naive" in formats
        and probe["needs_timezone_completion"] is True  # 有 naive 值 → 提醒補時區
    )
    return ok, f"formats={sorted(formats)} needs_tz={probe['needs_timezone_completion']}"


def t_probe_published_at_uses_explicit_source_timezone():
    probe = evidence_stage._probe_published_at(
        feed_candidates=[],
        detail_samples=[
            {
                "body_excerpt": (
                    '<time datetime="2026-07-08" itemprop="datePublished">'
                    "08 July 2026</time>"
                )
            }
        ],
        api_body="",
        source_timezone="Australia/Sydney",
    )
    ok = (
        probe["needs_timezone_completion"] is True
        and probe["source_timezone"] == "Australia/Sydney"
        and "Australia/Sydney" in probe["note"]
        and "台灣" not in probe["note"]
    )
    return ok, f"timezone={probe['source_timezone']} note={probe['note']}"


def t_probe_published_at_no_sample_is_honest():
    probe = evidence_stage._probe_published_at(
        feed_candidates=[], detail_samples=[], api_body="{}"
    )
    ok = probe["dominant_format"] == "no_sample" and "禁止用現在時間偽造" in probe["note"]
    return ok, f"dominant={probe['dominant_format']}"


# ════════════════════════ collect_evidence 端到端帶三缺口欄位 ════════════════════════


def t_collect_evidence_populates_three_gaps():
    original_fetch = evidence_stage._fetch_sample

    def fake_fetch(url, **kwargs):
        # detail 頁回帶 naive published_time 的 html
        return {
            "requested_url": url,
            "final_url": url,
            "status": 200,
            "body_excerpt": (
                '<meta property="article:published_time" content="2026-07-27T09:30:00">'
                "<article>full text</article>"
            ),
        }

    evidence_stage._fetch_sample = fake_fetch
    try:
        result = evidence_stage.collect_evidence(
            {
                "site_url": "https://example.com/news",
                "site_name": "Example",
                "source_prefix": "example",
                "target_schema": DEFAULT_TARGET_SCHEMA,
                "sample_urls": [],
                "access_mode": "public",
                "constraints": {"max_pages": 2},
                "validation": {
                    "allowed_domains": ["example.com"],
                    "article_url_patterns": [r"/news/\d+$"],
                    "excluded_url_patterns": [],
                },
                "strategy_detail": {
                    "strategy": "api",
                    "chosen_api": "https://example.com/api/news?page=1",
                },
                "recon_report": {
                    "http_status": 200,
                    "api_candidates": [
                        {
                            "method": "GET",
                            "url": "https://example.com/api/news?page=1",
                            "body_excerpt": (
                                '{"items":[{"title":"x","url":"/news/1"}],"next_page":2}'
                            ),
                            "json_shape": "object keys=items",
                            "article_record_count": 1,
                        }
                    ],
                    "http_entry_sample": {
                        "status": 200,
                        "safe_request_headers": {"User-Agent": "recon-ua", "Accept": "text/html"},
                        "link_samples": [
                            {"url": "https://example.com/news/1", "text": "One"},
                        ],
                    },
                },
            }
        )
    finally:
        evidence_stage._fetch_sample = original_fetch
    pack = result["evidence_pack"]
    pagination = pack["pagination"]
    probe = pack["published_at_probe"]
    replay = pack["replay_headers"]
    ok = (
        pagination["type"] == "query_param"
        and pagination["param"] == "page"
        and probe["dominant_format"] == "iso8601_naive"
        and probe["needs_timezone_completion"] is True
        and replay["entry"] == {"User-Agent": "recon-ua", "Accept": "text/html"}
    )
    return ok, (
        f"pagination={pagination['type']}/{pagination.get('param')} "
        f"date={probe['dominant_format']} replay_entry={bool(replay['entry'])}"
    )


TESTS = [
    t_classify_datetime_covers_known_formats,
    t_detect_pagination_query_param,
    t_detect_pagination_cursor_from_body,
    t_detect_pagination_next_link_from_html,
    t_detect_pagination_none_when_no_signal,
    t_probe_published_at_from_feed_and_detail_flags_naive,
    t_probe_published_at_uses_explicit_source_timezone,
    t_probe_published_at_no_sample_is_honest,
    t_collect_evidence_populates_three_gaps,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            ok, detail = test()
        except Exception as exc:
            ok, detail = False, f"EXCEPTION {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {test.__name__}: {detail}")
        failed += not ok
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
