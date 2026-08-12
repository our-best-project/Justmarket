import re
from datetime import datetime
from zoneinfo import ZoneInfo

import scrapy
from scrapy.item import Field, Item


class ArticleItem(Item):
    title = Field()
    url = Field()
    content = Field()
    published_at = Field()
    source_record_id = Field()


class RbaMediaSpider(scrapy.Spider):
    name = "rba_media"
    source_prefix = "rba_media"
    source = "澳洲儲備銀行公告"
    source_type = "official"
    content_scope = "full"

    allowed_domains = ["www.rba.gov.au", "rba.gov.au"]

    custom_settings = {
        "DOWNLOAD_HANDLERS": {
            "http": (
                "scrapy_playwright.handler."
                "ScrapyPlaywrightDownloadHandler"
            ),
            "https": (
                "scrapy_playwright.handler."
                "ScrapyPlaywrightDownloadHandler"
            ),
        },
        "TWISTED_REACTOR": (
            "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
        ),
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "CONCURRENT_REQUESTS": 4,
        "DOWNLOAD_DELAY": 1.0,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1.0,
        "AUTOTHROTTLE_MAX_DELAY": 5.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
    }

    def start_requests(self):
        yield scrapy.Request(
            url="https://www.rba.gov.au/media-releases/",
            meta={"playwright": True},
            callback=self.parse_list,
        )

    def parse_list(self, response):
        article_links = response.css(
            'article.item.rss-mr-item '
            'a[href*="/media-releases/"]::attr(href)'
        ).getall()
        seen = set()
        for link in article_links:
            full_url = response.urljoin(link)
            if not re.search(
                r"/media-releases/\d{4}/mr-\d{2}-\d{2}\.html$",
                full_url,
            ):
                continue
            if re.search(
                r"/media-releases/?(?:index\.html)?$",
                full_url,
            ):
                continue
            if full_url in seen:
                continue
            seen.add(full_url)
            yield scrapy.Request(
                url=full_url,
                meta={"playwright": True},
                callback=self.parse_article,
            )

    def parse_article(self, response):
        title = response.css(
            "h1.page-title span.rss-mr-title::text"
        ).get()
        if not title:
            title = response.css("h1.page-title::text").get()
        if not title:
            return

        content_parts = response.css(
            "div.rss-mr-content *::text"
        ).getall()
        if not content_parts:
            return
        content = " ".join(
            part.strip() for part in content_parts if part.strip()
        )
        if len(content) < 40:
            return

        datetime_str = response.css(
            "time.rss-mr-date::attr(datetime)"
        ).get()
        if not datetime_str:
            return
        try:
            published_at = datetime.strptime(
                datetime_str,
                "%Y-%m-%d",
            ).replace(
                tzinfo=ZoneInfo("Australia/Sydney")
            ).isoformat()
        except ValueError:
            return

        source_record_id = response.css(
            "span.rss-mr-number::text"
        ).get()
        if source_record_id:
            source_record_id = source_record_id.strip()

        yield ArticleItem(
            title=title.strip(),
            url=response.url,
            content=content[:20000],
            published_at=published_at,
            source_record_id=source_record_id,
        )
