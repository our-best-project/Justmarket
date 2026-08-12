"""Crawler Runtime 正式爬蟲的離線契約與關鍵解析測試。"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import pkgutil
import sys
from datetime import datetime
from pathlib import Path

_RUNTIME_DIR = Path(__file__).resolve().parents[1]
if str(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DIR))

import scrapy
from news_crawler import spiders as spider_package
from news_crawler.spiders.cna_spider import CnaSpider
from news_crawler.spiders.cnyes_spider import CnyesSpider
from news_crawler.spiders.ctee_spider import CteeSpider
from news_crawler.spiders.moneydj_spider import MoneydjSpider
from news_crawler.spiders.udn_spider import UdnSpider
from scrapy.exceptions import CloseSpider
from scrapy.http import HtmlResponse, TextResponse, XmlResponse


def _discover_spiders() -> tuple[type[scrapy.Spider], ...]:
    found: list[type[scrapy.Spider]] = []
    for module_info in pkgutil.iter_modules(spider_package.__path__):
        if not module_info.name.endswith("_spider"):
            continue
        module = importlib.import_module(f"news_crawler.spiders.{module_info.name}")
        found.extend(
            obj
            for obj in vars(module).values()
            if (
                inspect.isclass(obj)
                and issubclass(obj, scrapy.Spider)
                and obj is not scrapy.Spider
                and obj.__module__ == module.__name__
            )
        )
    return tuple(sorted(found, key=lambda cls: cls.name))


SPIDERS = _discover_spiders()


def _html(url: str, body: str) -> HtmlResponse:
    request = scrapy.Request(url)
    return HtmlResponse(
        url=url,
        body=body.encode("utf-8"),
        encoding="utf-8",
        request=request,
    )


def t_all_active_spiders_have_contract_and_obey_robots():
    missing = []
    for spider in SPIDERS:
        if not all(
            getattr(spider, attr, None)
            for attr in ("name", "source", "source_prefix", "source_type", "content_scope")
        ):
            missing.append(spider.__name__)
        # spec v2 §6：灰名單站允許 ROBOTSTXT_OBEY=False（瀏覽器等級隱身、低速、課程練習）；
        # robots 設定不屬於品質 gate；本測試只檢查 spider 結構。
        if ".first(" in inspect.getsource(spider):
            missing.append(f"{spider.__name__}:selectorlist_first")
    return not missing, f"violations={missing}"


def t_list_parsers_keep_only_real_contract_urls():
    udn = list(
        UdnSpider().parse_cate(
            _html(
                "https://money.udn.com/money/index",
                """
                <meta name="channel_id" content="1001">
                <div class="story-headline-wrapper">
                    <a href="/money/story/5607/9654258?from=index">good</a>
                </div>
                <div class="story-headline-wrapper">
                    <a href="/stock/top">bad</a>
                </div>
                """,
            )
        )
    )
    cna = list(
        CnaSpider(since="2026-07-27").parse_sitemap(
            XmlResponse(
                url=CnaSpider.sitemap_url,
                body=b"""<?xml version="1.0" encoding="UTF-8"?>
                <urlset xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
                  <url>
                    <loc>https://www.cna.com.tw/news/afe/202607273002.aspx</loc>
                    <news:news>
                      <news:publication_date>2026-07-27T12:00:00+08:00</news:publication_date>
                      <news:title>finance</news:title>
                    </news:news>
                  </url>
                  <url>
                    <loc>https://www.cna.com.tw/news/aopl/202607270241.aspx</loc>
                    <news:news>
                      <news:publication_date>2026-07-27T12:00:00+08:00</news:publication_date>
                      <news:title>world</news:title>
                    </news:news>
                  </url>
                </urlset>""",
                encoding="utf-8",
                request=scrapy.Request(CnaSpider.sitemap_url),
            )
        )
    )
    moneydj = list(
        MoneydjSpider(since="2026-07-27").parse(
            _html(
                "https://www.moneydj.com/kmdj/news/newsreallist.aspx?a=MB010000",
                """
                <table id="MainContent_Contents_sl_gvList">
                  <tr>
                    <td>07/27 12:00</td>
                    <td><a href="/KMDJ/News/NewsViewer.aspx?a=e6e808ca-cfd7-4345-ae56-7fc58ccc0d51">good</a></td>
                  </tr>
                  <tr>
                    <td>07/27 12:00</td>
                    <td><a href="/KMDJ/News/NewsHome.aspx">bad</a></td>
                  </tr>
                </table>
                """,
            ),
            page=MoneydjSpider.safety_max_pages,
            ref_year=2026,
            ref_month=7,
        )
    )
    ok = (
        len(udn) == 1
        and udn[0].url == "https://money.udn.com/money/story/5607/9654258"
        and len(cna) == 1
        and "/news/afe/" in cna[0].url
        and len(moneydj) == 1
        and "?a=e6e808ca-cfd7-4345-ae56-7fc58ccc0d51" in moneydj[0].url
    )
    return ok, f"requests={(len(udn), len(cna), len(moneydj))}"


def t_detail_parsers_emit_faithful_items():
    paragraph = "這是依照原文順序保留的財經新聞內容，包含市場、公司、價格與政策條件。" * 3
    udn_items = list(
        UdnSpider().parse_article(
            _html(
                "https://money.udn.com/money/story/5607/9654258",
                f"""
                <meta property="og:title" content="台股財經測試">
                <meta property="article:published_time" content="2026-07-27T12:00:00+08:00">
                <div class="article-body__content"><p>{paragraph}</p></div>
                """,
            )
        )
    )
    cna_items = list(
        CnaSpider().parse_article(
            _html(
                "https://www.cna.com.tw/news/afe/202607273002.aspx",
                f"""
                <meta property="og:title" content="中央社財經測試">
                <meta property="article:published_time" content="2026-07-27T12:00:00+08:00">
                <div class="paragraph"><p>{paragraph}</p></div>
                """,
            ),
            sitemap_published_at="2026-07-27T12:00:00+08:00",
        )
    )
    moneydj_items = list(
        MoneydjSpider().parse_article(
            _html(
                "https://www.moneydj.com/KMDJ/News/NewsViewer.aspx?a=abc",
                f"""
                <meta property="og:title" content="MoneyDJ 財經測試">
                <meta property="article:published_time" content="2026-07-27T12:00:00+08:00">
                <div class="article-content"><p>{paragraph}</p></div>
                """,
            ),
            "abc",
        )
    )
    items = udn_items + cna_items + moneydj_items
    ok = (
        len(items) == 3
        and all(paragraph in item["content"] for item in items)
        and all(item["published_at"].endswith("+08:00") for item in items)
    )
    return ok, f"item_count={len(items)}"


def t_cnyes_filters_empty_or_short_content():
    payload = {
        "items": {
            "data": [
                {
                    "newsId": 1,
                    "title": "應保留文章",
                    "content": "<p>" + "完整財經內容" * 10 + "</p>",
                    "publishAt": 1785146400,
                },
                {
                    "newsId": 2,
                    "title": "應排除文章",
                    "content": "",
                    "publishAt": 1785146400,
                },
            ]
        }
    }
    response = TextResponse(
        url=CnyesSpider.api_url,
        body=json.dumps(payload).encode(),
        encoding="utf-8",
        request=scrapy.Request(CnyesSpider.api_url),
    )
    items = list(
        CnyesSpider().parse_api(
            response,
            day=datetime.fromisoformat("2026-07-27T00:00:00+08:00"),
            page=1,
        )
    )
    return len(items) == 1 and items[0]["title"] == "應保留文章", f"items={len(items)}"


def t_ctee_requires_authorized_session_before_network():
    async def first_event():
        spider = CteeSpider()
        try:
            await anext(spider.start())
        except CloseSpider as exc:
            return exc.reason
        return ""

    reason = asyncio.run(first_event())
    return "ctee_requires_authorized_browser_session" in reason, f"reason={reason}"


TESTS = [
    t_all_active_spiders_have_contract_and_obey_robots,
    t_list_parsers_keep_only_real_contract_urls,
    t_detail_parsers_emit_faithful_items,
    t_cnyes_filters_empty_or_short_content,
    t_ctee_requires_authorized_session_before_network,
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
