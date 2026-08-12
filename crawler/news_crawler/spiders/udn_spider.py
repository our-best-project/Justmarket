# 政策註記：money.udn.com（聯合報系）robots.txt 對 User-agent: Claude / ClaudeBot
# 具名 Disallow: /，並附版權聲明「不得用於 LLM / AI 相關用途」。本專案為學術課程
# 測試、非商用，經使用者知情決定後仍納入本站；技術上僅沿用既有 policy.py 的
# Chrome UA（robots 檢查因此落在 User-agent: * 規則，未觸及該站對 Claude 的具名封鎖），
# 不加代理輪換、header 指紋隨機化、CAPTCHA 繞過等任何新的反偵測手段。
"""經濟日報（money.udn.com）財經新聞 spider。

翻頁機制：UDN 分類頁（``/money/cate/{cate_id}``）用「看更多」AJAX 載入更多文章，
實際 endpoint 是 ``/money/get_article/{moreId}/{channel_id}/{cate_id}/{sub_id}``
（探測自該頁 inline script；``moreId`` 從 2 開始遞增，需要帶 ``Referer`` 指向分類頁，
否則會被導回首頁）。分類頁本身是多個子分類（sub_id）組成的雜誌式版面，逐一對每個
sub_id 遞增 moreId 翻頁，直到該子分類回傳 ``<!--N-->``（無更多）或該頁文章日期已早於
``since`` 為止。實測子分類 7307 可翻到約 2026-06-04（約 2 個月前），並非半年——這是
UDN 該 AJAX 資料源本身的深度限制，不是程式邏輯問題。

``/money/get_article/*``、``/money/cate/*``、``/money/story/*`` 均不在 UDN
robots.txt 的 ``Disallow`` 清單內（只擋 ``/srank/*``、``/money/preview/*`` 等）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

import scrapy

from news_crawler.items import ArticleItem


class UdnSpider(scrapy.Spider):
    name = "udn"
    source = "經濟日報"
    source_prefix = "udn"
    source_type = "media"
    content_scope = "full"
    allowed_domains = ["money.udn.com", "udn.com"]

    cate_id = "10846"  # 要聞：混合數個財經子分類的版面
    cate_url = f"https://money.udn.com/money/cate/{cate_id}"
    more_article_url = "https://money.udn.com/money/get_article/{more_id}/{channel_id}/{cate_id}/{sub_id}"

    max_content_chars = 6000
    article_pattern = re.compile(r"/money/story/\d+/\d+$")
    safety_max_pages_per_sub = 2000  # 防爆上限；實測正常情況遠不會碰到

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "AUTOTHROTTLE_ENABLED": True,
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
        self._seen: set[str] = set()

    async def start(self):
        yield scrapy.Request(self.cate_url, callback=self.parse_cate)

    def parse_cate(self, response):
        channel_id = response.css('meta[name="channel_id"]::attr(content)').get() or "1001"
        sub_ids = sorted(
            set(response.css("a.story__read::attr(data-sub)").getall())
        )
        # 分類頁初次載入（moreId=1 等效）已內嵌的項目，一併解析、不浪費第一批資料。
        yield from self._parse_fragment(response, response)

        for sub_id in sub_ids:
            if not sub_id:
                continue
            yield self._more_request(
                more_id=2, channel_id=channel_id, sub_id=sub_id, referer=response.url
            )

    def _more_request(self, more_id: int, channel_id: str, sub_id: str, referer: str):
        url = self.more_article_url.format(
            more_id=more_id, channel_id=channel_id, cate_id=self.cate_id, sub_id=sub_id
        )
        return scrapy.Request(
            url,
            callback=self.parse_more,
            headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"},
            cb_kwargs={
                "more_id": more_id,
                "channel_id": channel_id,
                "sub_id": sub_id,
                "referer": referer,
            },
        )

    def parse_more(self, response, more_id: int, channel_id: str, sub_id: str, referer: str):
        body = response.text
        no_more = body.lstrip().startswith("<!--N-->")
        reached_since = yield from self._parse_fragment(response, response)

        if no_more or reached_since:
            return
        if more_id >= self.safety_max_pages_per_sub:
            self.logger.warning(
                "udn sub_id=%s 已達防爆上限 %s 頁，停止翻頁", sub_id, self.safety_max_pages_per_sub
            )
            return
        yield self._more_request(
            more_id=more_id + 1, channel_id=channel_id, sub_id=sub_id, referer=referer
        )

    def _parse_fragment(self, response, selector_source):
        """解析 story-headline-wrapper 區塊，yield 明細頁 Request；回傳是否已碰到 since 邊界。"""
        reached_since = False
        for wrapper in selector_source.css(".story-headline-wrapper"):
            href = wrapper.css("a::attr(href)").get()
            time_text = wrapper.css("time::text").get()
            if not href:
                continue
            list_dt = self._parse_datetime(time_text)
            if list_dt and list_dt < self._since:
                reached_since = True
                continue
            url = self._canonical_url(urljoin(response.url, href))
            if not self.article_pattern.search(urlsplit(url).path) or url in self._seen:
                continue
            self._seen.add(url)
            yield scrapy.Request(
                url,
                callback=self.parse_article,
                cb_kwargs={
                    "list_published_at": list_dt.isoformat() if list_dt else "",
                },
            )
        return reached_since

    def parse_article(self, response, list_published_at: str = ""):
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
            url=self._canonical_url(
                self._first(
                    response,
                    ('link[rel="canonical"]::attr(href)',),
                )
                or response.url
            ),
            content=content,
            published_at=published_at,
        )

    def _content(self, response) -> str:
        selectors = (
            ".article-body__content",
            ".article-content__editor",
            ".story_body_content",
            "#story_body_content",
            "article",
        )
        for selector in selectors:
            paragraphs = self._paragraphs(response.css(selector))
            if len(paragraphs) >= 40:
                return self._clip(paragraphs)
        return ""

    @staticmethod
    def _paragraphs(nodes) -> str:
        values = []
        seen = set()
        for node in nodes:
            for paragraph in node.css("p"):
                text = " ".join(paragraph.xpath(".//text()").getall())
                text = " ".join(text.split())
                if (
                    len(text) < 2
                    or text in seen
                    or text.startswith(("延伸閱讀", "推薦閱讀", "相關新聞"))
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
            "time::text",
        ):
            parsed = self._parse_datetime(response.css(selector).get())
            if parsed:
                return parsed.isoformat()

        for raw in response.css('script[type="application/ld+json"]::text').getall():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rows = payload if isinstance(payload, list) else [payload]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                parsed = self._parse_datetime(
                    row.get("datePublished") or row.get("dateModified")
                )
                if parsed:
                    return parsed.isoformat()
        return None

    def _parse_datetime(self, value) -> datetime | None:
        text = str(value or "").strip()
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
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M",
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
    def _first(response, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            value = response.css(selector).get()
            if value:
                return " ".join(value.split())
        return ""

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
