"""鉅亨網台股新聞 spider（合併 cnyes / cnyes_finance 後的唯一入口）。

公開 API：https://api.cnyes.com/media/api/v1/newslist/category/tw_stock_news
API 的 ``content`` 欄位即整篇 HTML 全文（實測最長逾 3000 字），故不需另抓文章頁。

抓取範圍以 ``since`` 參數控制，一次性回填與每日增量共用同一條路徑：
- 回填半年：``-a since=2026-01-28``（抓到該日 00:00 為止）。
- 每日增量：不帶 since，預設抓最近 ``days`` 天（含重疊，避免漏接）。
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import scrapy

from news_crawler.items import ArticleItem


class CnyesSpider(scrapy.Spider):
    name = "cnyes"
    source = "鉅亨網"
    source_prefix = "cnyes"
    source_type = "media"
    content_scope = "full"
    allowed_domains = ["api.cnyes.com", "news.cnyes.com"]

    api_url = "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock_news"
    article_url = "https://news.cnyes.com/news/id/{news_id}"
    timezone = ZoneInfo("Asia/Taipei")
    page_limit = 50
    max_pages_per_day = 20  # 單日安全上限，避免異常翻頁失控
    min_content_chars = 40
    max_content_chars = 20000

    custom_settings = {
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "AUTOTHROTTLE_ENABLED": True,
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(
        self,
        since: str = "",
        days: int | str = 2,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        today = datetime.now(self.timezone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self._today = today
        since = str(since or "").strip()
        if since:
            self._since = datetime.strptime(since, "%Y-%m-%d").replace(
                tzinfo=self.timezone
            )
        else:
            self._since = today - timedelta(days=max(1, int(days)) - 1)
        self._seen_ids: set[str] = set()

    async def start(self):
        day = self._today
        while day >= self._since:
            yield self._request(day, page=1)
            day -= timedelta(days=1)

    def _request(self, day: datetime, page: int) -> scrapy.Request:
        query = urlencode(
            {
                "page": page,
                "limit": self.page_limit,
                "startAt": int(day.timestamp()),
                "endAt": int((day + timedelta(days=1)).timestamp()),
            }
        )
        return scrapy.Request(
            f"{self.api_url}?{query}",
            callback=self.parse_api,
            cb_kwargs={"day": day, "page": page},
            headers={"Accept": "application/json"},
        )

    def parse_api(self, response: scrapy.http.Response, day: datetime, page: int):
        try:
            items = json.loads(response.text).get("items", {})
        except (json.JSONDecodeError, AttributeError):
            self.logger.exception("CNYES JSON 解析失敗 day=%s page=%s", day.date(), page)
            return
        records = items.get("data", []) or []

        for record in records:
            news_id = str(record.get("newsId") or "").strip()
            title = self._clean(record.get("title"))
            content = self._clean(record.get("content"))
            published_at = self._published_at(record.get("publishAt"))
            if (
                not news_id
                or news_id in self._seen_ids
                or len(title) < 4
                or len(content) < self.min_content_chars
                or not published_at
            ):
                continue
            self._seen_ids.add(news_id)
            yield ArticleItem(
                title=title,
                url=self.article_url.format(news_id=news_id),
                content=content[: self.max_content_chars],
                published_at=published_at,
                source_record_id=news_id,
            )

        last_page = int(items.get("last_page") or 1)
        if page < min(last_page, self.max_pages_per_day):
            yield self._request(day, page + 1)

    @staticmethod
    def _clean(value) -> str:
        # API 的 content 為雙重 HTML escape（&lt;p&gt;…），先還原兩次再去標籤。
        text = html.unescape(html.unescape(str(value or "")))
        return " ".join(re.sub(r"<[^>]+>", " ", text).split())

    def _published_at(self, value) -> str | None:
        try:
            return datetime.fromtimestamp(int(value), self.timezone).isoformat()
        except (TypeError, ValueError, OSError):
            return None
