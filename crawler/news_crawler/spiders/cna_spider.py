"""中央社財經 spider。

CNA 官方清單頁「看更多」翻頁走的是 ``/cna2018api/api/*``——這條路徑在
CNA 自家 robots.txt 明確 ``Disallow``，本 spider 遵守 ``ROBOTSTXT_OBEY``，
故不使用該 API。改用 CNA 在 robots.txt 中主動列為 ``Sitemap:`` 來源之一的
Google 新聞 Sitemap（合規、且已含 title/url/發布時間，不需再猜列表頁選擇器），
過濾 ``/news/afe/`` 路徑取得財經類文章清單，再逐篇進明細頁抓全文。

已知限制（如實記錄，非抓取邏輯問題）：Google News Sitemap 規格只保留約
3～4 天內的文章，CNA 官網本身也沒有提供其他合規的深度翻頁管道——故本
spider 目前無法回填到半年前，只能回填到 sitemap 涵蓋的範圍。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import scrapy

from news_crawler.items import ArticleItem


class CnaSpider(scrapy.Spider):
    name = "cna"
    source = "中央社財經"
    source_prefix = "cna"
    source_type = "media"
    content_scope = "full"
    allowed_domains = ["www.cna.com.tw", "cna.com.tw"]

    sitemap_url = "https://www.cna.com.tw/GoogleNewsSitemap_fromRemote_cfp.xml"
    article_pattern = re.compile(r"/news/afe/\d+\.aspx$")
    article_id_pattern = re.compile(r"/news/afe/(\d+)\.aspx$")
    timezone = ZoneInfo("Asia/Taipei")
    max_content_chars = 6000

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "AUTOTHROTTLE_ENABLED": True,
    }

    def __init__(self, since: str = "", days: int | str = 2, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = datetime.now(self.timezone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = str(since or "").strip()
        if since:
            self._since = datetime.strptime(since, "%Y-%m-%d").replace(
                tzinfo=self.timezone
            )
        else:
            self._since = today - timedelta(days=max(1, int(days)) - 1)
        self._seen: set[str] = set()

    async def start(self):
        yield scrapy.Request(self.sitemap_url, callback=self.parse_sitemap)

    def parse_sitemap(self, response):
        response.selector.remove_namespaces()
        for url_node in response.xpath("//url"):
            loc = (url_node.xpath("./loc/text()").get() or "").strip()
            if not loc or not self.article_pattern.search(urlsplit(loc).path):
                continue
            if loc in self._seen:
                continue
            published = self._parse_datetime(
                url_node.xpath(".//publication_date/text()").get()
            )
            if not published or published < self._since:
                continue
            title = " ".join(
                (url_node.xpath(".//title/text()").get() or "").split()
            )
            self._seen.add(loc)
            yield scrapy.Request(
                loc,
                callback=self.parse_article,
                cb_kwargs={
                    "sitemap_title": title,
                    "sitemap_published_at": published.isoformat(),
                },
            )

    def parse_article(
        self,
        response,
        sitemap_title: str = "",
        sitemap_published_at: str = "",
    ):
        title = sitemap_title or self._first(
            response,
            (
                'meta[property="og:title"]::attr(content)',
                "h1::text",
                "title::text",
            ),
        )
        content = self._content(response)
        if len(title) < 4 or len(content) < 40 or not sitemap_published_at:
            return

        match = self.article_id_pattern.search(response.url)
        yield ArticleItem(
            title=title,
            url=response.url,
            content=content,
            published_at=sitemap_published_at,
            source_record_id=match.group(1) if match else None,
        )

    def _content(self, response) -> str:
        for selector in (
            "div.paragraph",
            "div.article-box",
            ".centralContent",
            "article",
        ):
            text = self._paragraphs(response.css(selector))
            if len(text) >= 40:
                return self._clip(text)
        return ""

    @staticmethod
    def _paragraphs(nodes) -> str:
        paragraphs = []
        seen = set()
        for node in nodes:
            for paragraph in node.css("p"):
                text = " ".join(paragraph.xpath(".//text()").getall())
                text = " ".join(text.split())
                if (
                    len(text) < 2
                    or text in seen
                    or text.startswith(("延伸閱讀", "相關新聞", "更多新聞"))
                ):
                    continue
                seen.add(text)
                paragraphs.append(text)
        return "\n\n".join(paragraphs)

    @staticmethod
    def _parse_datetime(value) -> datetime | None:
        text = " ".join(str(value or "").split())
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def _clip(self, text: str) -> str:
        if len(text) <= self.max_content_chars:
            return text
        end = text.rfind("\n\n", 0, self.max_content_chars + 1)
        return text[: end if end > 4000 else self.max_content_chars].rstrip()

    @staticmethod
    def _first(response, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            value = response.css(selector).get()
            if value:
                return " ".join(value.split())
        return ""
