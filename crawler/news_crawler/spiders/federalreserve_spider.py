"""Federal Reserve 新聞稿 spider。

官方 ``ne-press.json`` 列表（新到舊排序，實測含逾十年歷史）→ 跟進明細頁取全文。
抓取範圍以 ``since`` 控制，一次性回填與每日增量共用同一路徑：
- 回填半年：``-a since=2026-01-28``。
- 每日增量：不帶 since，預設抓最近 ``days`` 天。
列表既然是新到舊排序，掃到早於 since 的那筆即可停止，不需讀完整份 json。
"""

import html
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import scrapy

from news_crawler.items import ArticleItem


class FederalreserveSpider(scrapy.Spider):
    name = "federalreserve"
    source_prefix = "federalreserve"
    allowed_domains = ["www.federalreserve.gov"]
    start_urls = ["https://www.federalreserve.gov/json/ne-press.json"]

    source = "Federal Reserve Press Releases"
    source_type = "official"
    content_scope = "full"

    eastern_tz = ZoneInfo("America/New_York")
    taipei_tz = ZoneInfo("Asia/Taipei")
    max_content_chars = 20000

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1.0,
        "CONCURRENT_REQUESTS": 1,
        "USER_AGENT": (
            "Mozilla/5.0 (compatible; FederalReservePressCrawler/1.0; "
            "+https://www.federalreserve.gov/newsevents/pressreleases.htm)"
        ),
    }

    def __init__(self, since: str = "", days: int | str = 2, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = datetime.now(self.taipei_tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = str(since or "").strip()
        if since:
            self._since = datetime.strptime(since, "%Y-%m-%d").replace(
                tzinfo=self.taipei_tz
            )
        else:
            self._since = today - timedelta(days=max(1, int(days)) - 1)

    def parse(self, response):
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            return

        for item in data:
            title_raw = item.get("t")
            date_raw = item.get("d")
            path_raw = item.get("l")
            if not title_raw or not date_raw or not path_raw:
                continue

            published = self._parse_date(str(date_raw).strip())
            if not published:
                continue
            # 列表新到舊排序：掃到早於 since 即可停止整份掃描。
            if published < self._since:
                break

            title = html.unescape(str(title_raw)).strip()
            if not title:
                continue
            url = response.urljoin(str(path_raw).strip())
            if not url.startswith("https://www.federalreserve.gov/"):
                continue

            yield scrapy.Request(
                url,
                callback=self.parse_detail,
                meta={"title": title, "published_at": published.isoformat()},
            )

    def _parse_date(self, date_str) -> datetime | None:
        try:
            dt = datetime.strptime(date_str, "%m/%d/%Y %I:%M:%S %p")
        except ValueError:
            return None
        return dt.replace(tzinfo=self.eastern_tz)

    def parse_detail(self, response):
        title = response.meta["title"]
        published_at = response.meta["published_at"]
        content = self._extract_content(response)
        if not content or len(content) < 50 or content.strip() == title.strip():
            return

        article = ArticleItem()
        article["title"] = title
        article["url"] = response.url
        article["content"] = content[: self.max_content_chars]
        article["published_at"] = published_at
        article["source_record_id"] = None
        yield article

    def _extract_content(self, response):
        article_html = response.css("div#article").get()
        if article_html:
            return self._clean_text(article_html)

        content_html = response.css("div#content").get()
        if content_html:
            content_html = re.sub(
                r"<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>",
                " ",
                content_html,
                flags=re.S | re.I,
            )
            return self._clean_text(content_html)

        paragraphs = response.css("p::text").getall()
        text = " ".join(p.strip() for p in paragraphs if p.strip())
        return self._clean_text(text)

    def _clean_text(self, html_text):
        if not html_text:
            return ""
        text = html.unescape(re.sub(r"<[^>]+>", " ", html_text))
        return re.sub(r"\s+", " ", text).strip()
