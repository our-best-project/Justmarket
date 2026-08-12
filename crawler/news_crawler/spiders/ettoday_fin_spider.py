"""ETtoday 財經 spider。

改用 ETtoday 全站的「日期＋分類」封存頁：
``https://www.ettoday.net/news/news-list-{YYYY-MM-DD}-17.htm``
（``17`` 是財經分類代碼，探測自該站導覽列連結）——每個日期一頁，
直接列出當天所有財經分類文章（含 finance.ettoday.net 的 permalink），
不需要走「看更多」之類的前端翻頁。經實測 2026-01-28（半年前）該日期
封存頁仍正常回應，故可回填半年。

``robots.txt``：www.ettoday.net／finance.ettoday.net 皆對
``User-agent: ClaudeBot`` 明確 ``Allow: /``，沒有 AI 使用限制聲明。

（本 spider 原版沒有獨立的 ``_is_finance_relevant()`` 關鍵字過濾——財經
範圍是靠只收 ``finance.ettoday.net`` 網域＋財經分類封存頁本身做到的，
沿用不動。）
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urldefrag, urljoin

import scrapy

from news_crawler.items import ArticleItem


class EttodayFinSpider(scrapy.Spider):
    name = "ettoday_fin"
    source_prefix = "ettoday_fin"
    source = "ETtoday財經"
    source_type = "media"
    content_scope = "full"

    allowed_domains = ["finance.ettoday.net", "ettoday.net", "www.ettoday.net"]

    archive_url = "https://www.ettoday.net/news/news-list-{date}-17.htm"
    finance_article_pattern = re.compile(r"finance\.ettoday\.net/news/\d+")

    custom_settings = {
        "DOWNLOAD_DELAY": 1.0,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1.0,
        "AUTOTHROTTLE_MAX_DELAY": 5.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "CONCURRENT_REQUESTS": 2,
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "X-Purpose": "academic course exercise, non-commercial",
        },
    }

    max_content_chars = 6000
    _TZ8 = timezone(timedelta(hours=8))

    def __init__(self, since: str = "", days: int | str = 2, **kwargs):
        super().__init__(**kwargs)
        today = datetime.now(self._TZ8).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = str(since or "").strip()
        if since:
            self._since = datetime.strptime(since, "%Y-%m-%d").replace(
                tzinfo=self._TZ8
            )
        else:
            self._since = today - timedelta(days=max(1, int(days)) - 1)
        self._today = today
        self._seen = set()

    async def start(self):
        day = self._today
        while day >= self._since:
            yield scrapy.Request(
                self.archive_url.format(date=day.strftime("%Y-%m-%d")),
                callback=self.parse_archive_day,
                cb_kwargs={"day": day},
            )
            day -= timedelta(days=1)

    def parse_archive_day(self, response, day):
        for href in response.css("a::attr(href)").getall():
            full_url = self._normalize_url(response.url, href)
            if not self.finance_article_pattern.search(full_url):
                continue
            if full_url in self._seen:
                continue
            self._seen.add(full_url)
            yield scrapy.Request(full_url, callback=self.parse_article)

    def _jsonld_article(self, response):
        """回傳第一個 Article 類的 JSON-LD 物件（ETtoday 的可信 title/日期來源）。"""
        for block in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(block)
            except (ValueError, TypeError):
                continue
            for obj in data if isinstance(data, list) else [data]:
                if isinstance(obj, dict) and "Article" in str(obj.get("@type") or ""):
                    return obj
        return {}

    def parse_article(self, response):
        if response.status != 200:
            return

        ld = self._jsonld_article(response)

        # ── 標題：og:title → JSON-LD headline → <title> ──
        title = response.css('meta[property="og:title"]::attr(content)').get()
        if not title:
            title = ld.get("headline")
        if not title:
            title = response.css("title::text").get()
        if not title:
            return
        title = re.sub(
            r"\s*\|\s*ETtoday(?:財經雲|新聞雲|money)?(?:\s*\|\s*ETtoday(?:財經雲|新聞雲))?\s*$",
            "",
            title,
        ).strip()
        if not title:
            return

        # ── 內文：只從文章正文容器取，逐段連同內嵌 <a>/<strong> 文字一起取，
        #    絕不 fallback 到全頁 <p>（會混入導覽/相關新聞/廣告）──
        content_parts = []
        for container in (
            'div[itemprop="articleBody"]',
            "div.story",
            "article.story",
            "div.article-content",
        ):
            paragraphs = response.css(f"{container} p")
            if not paragraphs:
                continue
            for paragraph in paragraphs:
                text = " ".join(
                    t.strip() for t in paragraph.css("::text").getall() if t.strip()
                )
                if len(text) > 10:
                    content_parts.append(text)
            if content_parts:
                break
        if not content_parts:
            return

        content = "\n".join(content_parts)
        content = re.sub(r"記者[^：\n]*[：:]?", "", content)
        content = re.sub(r"原文網址[^\n]*", "", content)
        content = re.sub(r"關鍵字[^\n]*", "", content)
        content = re.sub(r"※[^\n]*", "", content)
        content = re.sub(r"點我[^\n]*", "", content)
        content = re.sub(r"快加入[^\n]*", "", content)
        content = re.sub(r"追蹤[^\n]*", "", content)
        content = re.sub(r"按讚[^\n]*", "", content)
        content = re.sub(r"分享[^\n]*", "", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        if len(content) < 40:
            return
        if len(content) > self.max_content_chars:
            content = content[: self.max_content_chars]

        # ── 發布時間：JSON-LD datePublished → pubdate/published_time meta →
        #    <time> 可見文字（datetime 屬性在 ETtoday 是壞值，不採用）。抓不到就跳過，
        #    不對整頁猜日期（避免張冠李戴）──
        published_raw = ld.get("datePublished")
        if not published_raw:
            for selector in (
                'meta[name="pubdate"]::attr(content)',
                'meta[property="article:published_time"]::attr(content)',
                'meta[itemprop="datePublished"]::attr(content)',
            ):
                published_raw = response.css(selector).get()
                if published_raw:
                    break
        if not published_raw:
            time_text = response.css("time.date::text, time::text").get()
            if time_text and re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", time_text):
                published_raw = time_text.strip()
        published_at = self._parse_datetime(published_raw) if published_raw else None
        if not published_at:
            return
        if published_at < self._since:
            return

        match = re.search(r"/news/(\d+)", response.url)

        item = ArticleItem()
        item["title"] = title
        item["url"] = response.url
        item["content"] = content
        item["published_at"] = published_at.isoformat()
        item["source_record_id"] = match.group(1) if match else None
        yield item

    def _normalize_url(self, base_url, href):
        full_url = urljoin(base_url, href)
        normalized, _ = urldefrag(full_url)
        return normalized

    def _parse_datetime(self, dt_str):
        dt_str = (dt_str or "").strip()
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            dt = None
            for pattern in (
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M",
                "%Y年%m月%d日 %H:%M",
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z",
            ):
                try:
                    dt = datetime.strptime(dt_str, pattern)
                    break
                except (ValueError, TypeError):
                    continue
            if dt is None:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._TZ8)
        return dt
