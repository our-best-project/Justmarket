import re
from datetime import datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import scrapy

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "X-Purpose": "academic course exercise, non-commercial",
}


class ArticleItem(scrapy.Item):
    title = scrapy.Field()
    url = scrapy.Field()
    content = scrapy.Field()
    published_at = scrapy.Field()
    source_record_id = scrapy.Field()


class BocPressSpider(scrapy.Spider):
    name = "boc_press"
    source_prefix = "boc_press"
    source = "加拿大央行新聞稿"
    source_type = "official"
    content_scope = "full"
    allowed_domains = ["www.bankofcanada.ca", "bankofcanada.ca"]
    start_urls = [
        "https://www.bankofcanada.ca/feed/"
        "?content_type=press%20press-releases"
        "&post_type%5B0%5D=post&post_type%5B1%5D=page"
    ]

    custom_settings = {
        "DOWNLOAD_DELAY": 1.5,
        "CONCURRENT_REQUESTS": 4,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1.0,
        "AUTOTHROTTLE_MAX_DELAY": 5.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
    }

    article_pattern = re.compile(
        r"/20\d{2}/\d{2}/[a-z0-9][a-z0-9-]+/?$"
    )
    excluded_pattern = re.compile(
        r"/press/press-releases/?$|/page/\d+/?$"
    )
    source_timezone = ZoneInfo("America/Toronto")

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                headers=HEADERS,
                callback=self.parse_feed,
            )

    def parse_feed(self, response):
        selector = response.selector
        selector.remove_namespaces()
        for node in selector.xpath("//item"):
            link = node.xpath("link/text()").get("")
            title = node.xpath("title/text()").get("")
            date_text = (
                node.xpath("date/text()").get("")
                or node.xpath("pubDate/text()").get("")
            )
            url = urljoin(response.url, link.strip())
            published_at = self._parse_date(date_text.strip())
            if (
                not title.strip()
                or not self.article_pattern.search(url)
                or self.excluded_pattern.search(url)
                or not published_at
                or self._too_old(published_at)
            ):
                continue
            yield scrapy.Request(
                url,
                headers=HEADERS,
                callback=self.parse_article,
                meta={
                    "title": title.strip(),
                    "published_at": published_at,
                },
            )

    def parse_article(self, response):
        title = response.meta.get("title", "").strip()
        published_at = response.meta.get("published_at", "")
        if not title or not published_at:
            return

        content = self._extract_content(response)
        if len(content) < 40:
            return
        yield ArticleItem(
            title=title,
            url=response.url,
            content=content[:20000],
            published_at=published_at,
            source_record_id="",
        )

    @staticmethod
    def _extract_content(response):
        containers = response.css("div.post-content")
        parts = BocPressSpider._text_blocks(containers)
        if not parts:
            containers = response.css(
                "div.cfct-widget-module-bochtml > div.cfct-mod-content"
            )
            parts = BocPressSpider._text_blocks(containers)
        return "\n\n".join(parts)

    @staticmethod
    def _text_blocks(containers):
        parts = []
        for node in containers.xpath(
            ".//p | .//h2 | .//h3 | .//h4 | "
            ".//blockquote | .//pre | .//li | .//tr"
        ):
            text = " ".join(node.xpath("string(.)").get("").split())
            if text:
                parts.append(text)
        return parts

    @classmethod
    def _parse_date(cls, text):
        if not text:
            return ""
        for parser in (
            lambda value: datetime.fromisoformat(value),
            lambda value: datetime.strptime(
                value,
                "%a, %d %b %Y %H:%M:%S %z",
            ),
        ):
            try:
                parsed = parser(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(
                        tzinfo=cls.source_timezone
                    )
                return parsed.isoformat()
            except ValueError:
                continue
        return ""

    @classmethod
    def _too_old(cls, published_at):
        parsed = datetime.fromisoformat(published_at)
        return parsed < (
            datetime.now(cls.source_timezone) - timedelta(days=365)
        )
