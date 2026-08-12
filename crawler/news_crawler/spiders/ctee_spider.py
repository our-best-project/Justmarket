"""工商時報 session-required spider。

2026-07-27 live recon：Browser 與 plain HTTP 都回 403。沒有使用者授權的
Playwright storage state 時明確 fail-closed，不用空輸出假裝成功。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

import scrapy
from scrapy.exceptions import CloseSpider
from scrapy_playwright.page import PageMethod

from news_crawler.items import ArticleItem


class CteeSpider(scrapy.Spider):
    name = "ctee"
    source = "工商時報"
    source_prefix = "ctee"
    source_type = "media"
    content_scope = "summary_only"
    allowed_domains = ["www.ctee.com.tw", "ctee.com.tw"]
    start_url = "https://www.ctee.com.tw/livenews"

    max_articles = 20
    max_content_chars = 6000
    article_pattern = re.compile(r"/(?:news|article)/\d+", re.IGNORECASE)

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": True},
    }

    def __init__(self, access_context_ref: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.access_context_ref = str(access_context_ref or "").strip()
        self._seen: set[str] = set()

    async def start(self):
        if not self.access_context_ref:
            raise CloseSpider("ctee_requires_authorized_browser_session")
        yield scrapy.Request(
            self.start_url,
            callback=self.parse_list,
            meta={
                "playwright": True,
                "playwright_context": "ctee_authorized",
                "playwright_context_kwargs": {
                    "storage_state": self.access_context_ref,
                },
                "playwright_page_methods": [
                    PageMethod("wait_for_load_state", "domcontentloaded"),
                    PageMethod("wait_for_timeout", 2000),
                ],
            },
        )

    def parse_list(self, response):
        scheduled = 0
        for href in response.css(
            'a[href*="/news/"]::attr(href), a[href*="/article/"]::attr(href)'
        ).getall():
            url = self._canonical_url(urljoin(response.url, href))
            if (
                not self.article_pattern.search(urlsplit(url).path)
                or url in self._seen
            ):
                continue
            self._seen.add(url)
            yield scrapy.Request(
                url,
                callback=self.parse_article,
                meta={
                    "playwright": True,
                    "playwright_context": "ctee_authorized",
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "domcontentloaded")
                    ],
                },
            )
            scheduled += 1
            if scheduled >= self.max_articles:
                break

    def parse_article(self, response):
        title = self._first(
            response,
            (
                'meta[property="og:title"]::attr(content)',
                "h1::text",
                "title::text",
            ),
        )
        published_at = self._published_at(response)
        content = self._content(response)
        if len(title) < 4 or len(content) < 40 or not published_at:
            return
        yield ArticleItem(
            title=title,
            url=self._canonical_url(response.url),
            content=content,
            published_at=published_at,
        )

    def _content(self, response) -> str:
        for selector in (
            ".article-content",
            ".post-content",
            ".entry-content",
            ".news-content",
            "article",
        ):
            paragraphs = []
            for paragraph in response.css(selector).css("p"):
                text = self._clean(" ".join(paragraph.xpath(".//text()").getall()))
                if len(text) >= 2 and not text.startswith(("延伸閱讀", "相關新聞")):
                    paragraphs.append(text)
            content = "\n\n".join(dict.fromkeys(paragraphs))
            if len(content) >= 40:
                return content[: self.max_content_chars]
        return ""

    def _published_at(self, response) -> str | None:
        for selector in (
            'meta[property="article:published_time"]::attr(content)',
            'meta[name="pubdate"]::attr(content)',
            "time[datetime]::attr(datetime)",
            "time::text",
            ".date::text",
        ):
            parsed = self._parse_datetime(response.css(selector).get())
            if parsed:
                return parsed.isoformat()
        for raw in response.css('script[type="application/ld+json"]::text').getall():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for row in payload if isinstance(payload, list) else [payload]:
                if isinstance(row, dict):
                    parsed = self._parse_datetime(
                        row.get("datePublished") or row.get("dateModified")
                    )
                    if parsed:
                        return parsed.isoformat()
        return None

    @staticmethod
    def _parse_datetime(value) -> datetime | None:
        text = CteeSpider._clean(value)
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return (
                parsed
                if parsed.tzinfo
                else parsed.replace(tzinfo=timezone(timedelta(hours=8)))
            )
        except ValueError:
            pass
        for pattern in (
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(text, pattern).replace(
                    tzinfo=timezone(timedelta(hours=8))
                )
            except ValueError:
                continue
        return None

    @staticmethod
    def _clean(value) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _first(response, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            value = response.css(selector).get()
            if value:
                return CteeSpider._clean(value)
        return ""

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
