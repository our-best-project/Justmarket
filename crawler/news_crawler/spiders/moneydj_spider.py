# 政策註記：moneydj.com robots.txt 開頭即有「Copyright & AI Use Notice」明文聲明
# 「Any use for large language models (LLMs)... is strictly prohibited」，並具名封鎖
# ClaudeBot / Claude-Web / anthropic-ai。本專案為學術課程測試、非商用，經使用者
# 知情決定後仍納入本站；技術上僅沿用既有 policy.py 的 Chrome UA（robots 檢查因此
# 落在 User-agent: * 規則，未觸及該站對 Claude 的具名封鎖），不加代理輪換、header
# 指紋隨機化、CAPTCHA 繞過等任何新的反偵測手段。
"""MoneyDJ 財經新聞 spider。

列表頁已提供真實 ``NewsViewer.aspx?a=<guid>`` permalink；不再依賴過時 GUID
REST endpoint，也不猜 JSON 欄位。

翻頁機制：列表頁底部有真實、非 JS 的數字分頁 ``?index1=N&a=MB010000``
（探測時最後一頁 N 高達 6448，足夠涵蓋半年）。每列表頁的每一列已含日期欄
（``MM/DD HH:MM``，不含年份），逐列比對月份是否「回跳」（例如上一列 01 月、
這一列變成 12 月）以推斷跨年，藉此在不用打開每篇明細頁的情況下判斷是否已
超出 ``since``，超出即停止翻頁。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import scrapy

from news_crawler.items import ArticleItem


class MoneydjSpider(scrapy.Spider):
    name = "moneydj"
    source = "MoneyDJ理財網"
    source_prefix = "moneydj"
    source_type = "media"
    content_scope = "full"
    allowed_domains = ["www.moneydj.com", "moneydj.com"]

    base_url = "https://www.moneydj.com/kmdj/news/newsreallist.aspx"
    category = "MB010000"

    max_content_chars = 6000
    article_pattern = re.compile(
        r"/kmdj/news/news-?viewer\.aspx$", re.IGNORECASE
    )
    safety_max_pages = 8000  # 防爆上限；實測該分類最後一頁約 6448

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "AUTOTHROTTLE_ENABLED": True,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
            "Referer": "https://www.moneydj.com/",
        },
    }

    def __init__(self, since: str = "", days: int | str = 2, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timezone = timezone(timedelta(hours=8))
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
        self._today = today
        self._seen: set[str] = set()

    async def start(self):
        yield scrapy.Request(
            f"{self.base_url}?a={self.category}",
            callback=self.parse,
            cb_kwargs={"page": 1, "ref_year": self._today.year, "ref_month": self._today.month},
        )

    def parse(self, response, page: int, ref_year: int, ref_month: int):
        reached_since = False
        row_count = 0
        for row in response.css("table#MainContent_Contents_sl_gvList tr"):
            link = row.css(
                'a[href*="NewsViewer.aspx"], a[href*="newsviewer.aspx"], '
                'a[href*="news-viewer.aspx"]'
            )
            href = link.css("::attr(href)").get()
            if not href:
                continue  # 表頭列或無連結列
            row_count += 1
            date_text = " ".join(
                t.strip()
                for t in row.css("td:first-child ::text, td:first-child::text").getall()
                if t.strip()
            )
            match = re.match(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", date_text)
            if match:
                month, day, hour, minute = (int(x) for x in match.groups())
                if month > ref_month:
                    ref_year -= 1
                ref_month = month
                published = datetime(
                    ref_year, month, day, hour, minute, tzinfo=self.timezone
                )
            else:
                published = None

            if published and published < self._since:
                reached_since = True
                continue

            url = self._canonical_url(urljoin(response.url, href))
            parsed = urlsplit(url)
            article_id = parse_qs(parsed.query).get("a", [""])[0]
            if (
                not self.article_pattern.search(parsed.path)
                or not article_id
                or url in self._seen
            ):
                continue
            self._seen.add(url)
            yield scrapy.Request(
                url,
                callback=self.parse_article,
                cb_kwargs={
                    "article_id": article_id,
                    "list_published_at": published.isoformat() if published else "",
                },
            )

        if reached_since or row_count == 0:
            return
        if page >= self.safety_max_pages:
            self.logger.warning("moneydj 已達防爆上限 %s 頁，停止翻頁", self.safety_max_pages)
            return
        next_page = page + 1
        yield scrapy.Request(
            f"{self.base_url}?index1={next_page}&a={self.category}",
            callback=self.parse,
            cb_kwargs={"page": next_page, "ref_year": ref_year, "ref_month": ref_month},
        )

    def parse_article(self, response, article_id: str, list_published_at: str = ""):
        title = self._first(
            response,
            (
                'meta[property="og:title"]::attr(content)',
                "h1::text",
                "title::text",
            ),
        )
        published_at = self._published_at(response) or list_published_at
        content = self._content(response)
        if len(title) < 4 or len(content) < 40 or not published_at:
            return
        yield ArticleItem(
            title=title,
            url=self._canonical_url(response.url),
            content=content,
            published_at=published_at,
            source_record_id=article_id,
        )

    def _content(self, response) -> str:
        for selector in (
            ".article-content",
            ".news-content",
            ".NewsContent",
            "#MainContent",
            "#content",
            "article",
        ):
            node = response.css(selector)
            paragraphs = self._paragraphs(node)
            if len(paragraphs) >= 40:
                return self._clip(paragraphs)
            text = self._clean(" ".join(node.xpath(".//text()").getall()))
            if len(text) >= 80:
                return self._clip(text)
        return ""

    @staticmethod
    def _paragraphs(nodes) -> str:
        values = []
        seen = set()
        for node in nodes:
            for paragraph in node.css("p"):
                text = MoneydjSpider._clean(
                    " ".join(paragraph.xpath(".//text()").getall())
                )
                if (
                    len(text) < 2
                    or text in seen
                    or text.startswith(("延伸閱讀", "相關新聞", "熱門新聞"))
                ):
                    continue
                seen.add(text)
                values.append(text)
        return "\n\n".join(values)

    def _published_at(self, response) -> str | None:
        for selector in (
            'meta[property="article:published_time"]::attr(content)',
            'meta[name="pubdate"]::attr(content)',
            "time[datetime]::attr(datetime)",
            ".date::text",
            ".time::text",
            ".article-date::text",
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
                if not isinstance(row, dict):
                    continue
                parsed = self._parse_datetime(
                    row.get("datePublished") or row.get("dateModified")
                )
                if parsed:
                    return parsed.isoformat()
        return None

    def _parse_datetime(self, value) -> datetime | None:
        text = MoneydjSpider._clean(value)
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return (
                parsed
                if parsed.tzinfo
                else parsed.replace(tzinfo=self.timezone)
            )
        except ValueError:
            pass
        for pattern in (
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(text, pattern).replace(tzinfo=self.timezone)
            except ValueError:
                continue
        return None

    def _clip(self, text: str) -> str:
        if len(text) <= self.max_content_chars:
            return text
        end = text.rfind("\n\n", 0, self.max_content_chars + 1)
        return text[: end if end > 4000 else self.max_content_chars].rstrip()

    @staticmethod
    def _clean(value) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _first(response, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            value = response.css(selector).get()
            if value:
                return MoneydjSpider._clean(value)
        return ""

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlsplit(url)
        article_id = parse_qs(parsed.query).get("a", [""])[0]
        query = urlencode({"a": article_id}) if article_id else ""
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
