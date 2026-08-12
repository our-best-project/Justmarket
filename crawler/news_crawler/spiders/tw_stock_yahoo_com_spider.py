# 政策註記：tw.stock.yahoo.com robots.txt 對一長串具名 User-agent（含
# anthropic-ai / Claude-Web / ClaudeBot，甚至 Scrapy 本身）整批 Disallow: /，
# 與 User-agent: * 的一般規則分開列出。本專案為學術課程測試、非商用，經使用者
# 知情決定後仍納入本站；技術上僅沿用既有 policy.py 的 Chrome UA（robots 檢查因此
# 落在 User-agent: * 規則，未觸及對 Claude/Scrapy 的具名封鎖），不加代理輪換、
# header 指紋隨機化、CAPTCHA 繞過等任何新的反偵測手段。另外，robots.txt 也明確
# Disallow 了本站用來做「看更多」無限捲動的內部 API（``/_td-stock``），故本 spider
# 不使用該 API，改用站方主動列在 robots.txt 的 ``Sitemap:`` 來源。
"""tw.stock.yahoo.com 財經新聞 spider。

翻頁機制：改用 Yahoo 官方的 Google 新聞 Sitemap
（``news-sitemap-index.xml`` → 每天一份 ``news-sitemap-{date}.xml``，
站方在 robots.txt 主動列為 Sitemap 來源，合規可爬），每份已含
title/url/發布時間，不需再猜列表頁選擇器。

已知限制（如實記錄，非抓取邏輯問題）：Google News Sitemap 規格只保留約
2～3 天內的文章；經人工用瀏覽器操作（滾動、監看網路請求）驗證，
``/news/`` 列表頁本身在首次載入後不會再觸發任何「載入更多」請求
——也就是說即使不考慮 robots.txt，這個列表頁本來就沒有更深的公開
翻頁管道。故本 spider 目前無法回填到半年前，只能回填到 sitemap 涵蓋的範圍。

財經關鍵字過濾（``_is_finance_relevant``）沿用舊版不動。
"""

import json
import re
from datetime import UTC, datetime, timedelta, timezone

import scrapy

from news_crawler.items import ArticleItem


class TwStockYahooComSpider(scrapy.Spider):
    name = "tw_stock_yahoo_com"
    source_prefix = "tw_stock_yahoo_com"
    source = "tw.stock.yahoo.com"
    source_type = "media"
    content_scope = "full"

    allowed_domains = ["tw.stock.yahoo.com", "tw.news.yahoo.com"]
    sitemap_index_url = "https://tw.stock.yahoo.com/news-sitemap-index.xml"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS": 1,
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    }

    max_content_chars = 6000
    _boilerplate_patterns = (
        r"^加入為\s*Google\s*偏好來源",
        r"^將\s*Yahoo\s*設為首選來源",
        r"^在\s*Google\s*上查看更多我們的精彩報導",
        r"^(更多相關內容|相關新聞|延伸閱讀|推薦閱讀)$",
    )
    _finance_terms = (
        "股", "債", "基金", "ETF", "期貨", "匯率", "外匯", "美元", "日圓",
        "黃金", "原油", "央行", "利率", "通膨", "經濟", "景氣", "GDP",
        "財政", "金融", "銀行", "保險", "投資", "市場", "指數", "法人",
        "外資", "投信", "券商", "證交所", "上市", "上櫃", "營收", "獲利",
        "毛利", "EPS", "股利", "配息", "財報", "法說", "目標價", "供應鏈",
        "產業", "企業", "公司", "商場", "零售", "超市", "消費", "房市",
        "不動產", "關稅", "貿易", "出口", "進口", "製造", "半導體",
        "晶片", "AI", "伺服器", "台積電", "鴻海", "聯發科",
    )

    def __init__(self, since: str = "", days: int | str = 2, **kwargs):
        super().__init__(**kwargs)
        self._tz = timezone(timedelta(hours=8))
        today = datetime.now(self._tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = str(since or "").strip()
        if since:
            self._since = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=self._tz)
        else:
            self._since = today - timedelta(days=max(1, int(days)) - 1)
        self._seen = set()

    async def start(self):
        yield scrapy.Request(self.sitemap_index_url, callback=self.parse_sitemap_index)

    def parse_sitemap_index(self, response):
        response.selector.remove_namespaces()
        date_pattern = re.compile(r"news-sitemap-(\d{4}-\d{2}-\d{2})\.xml")
        for loc in response.xpath("//sitemap/loc/text()").getall():
            match = date_pattern.search(loc)
            if not match:
                continue
            day = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=self._tz)
            if day < self._since:
                continue
            yield scrapy.Request(loc, callback=self.parse_day_sitemap)

    def parse_day_sitemap(self, response):
        response.selector.remove_namespaces()
        for url_node in response.xpath("//url"):
            loc = (url_node.xpath("./loc/text()").get() or "").strip()
            if not loc or loc in self._seen:
                continue
            published = self._parse_datetime(
                url_node.xpath(".//publication_date/text()").get()
            )
            if not published or published < self._since:
                continue
            title = " ".join((url_node.xpath(".//title/text()").get() or "").split())
            self._seen.add(loc)
            yield scrapy.Request(
                loc,
                callback=self.parse_article,
                errback=self.errback,
                meta={"article_url": loc},
                cb_kwargs={
                    "sitemap_title": title,
                    "sitemap_published_at": published.isoformat(),
                },
            )

    def parse_article(self, response, sitemap_title: str = "", sitemap_published_at: str = ""):
        url = response.meta.get("article_url", response.url)

        title = sitemap_title or self._extract_title(response)
        content = self._extract_content(response)
        published_at = sitemap_published_at or self._extract_published_at(response)

        if not title or not content or not published_at:
            return

        if not self._is_finance_relevant(title, content):
            self.logger.debug("Skip non-finance article: %s", url)
            return

        yield ArticleItem(
            title=title.strip(),
            url=url,
            content=content.strip(),
            published_at=published_at,
            source_record_id=url,
        )

    def _extract_title(self, response):
        for sel in [
            'h1[data-test-locator="headline"]::text',
            'h1::text',
            'meta[property="og:title"]::attr(content)',
            'title::text',
        ]:
            val = response.css(sel).get()
            if val:
                return " ".join(val.split()).strip()
        return ""

    def _extract_content(self, response):
        for container_sel in [
            'div.caas-body',
            'article',
            'div[class*="article-body"]',
            'div[class*="content-body"]',
            'div[class*="post-content"]',
            'div[class*="news-content"]',
        ]:
            text = self._content_from_nodes(response.css(container_sel))
            if len(text) > 20:
                return text

        text = self._content_from_nodes(
            response.css('div[class*="content"], div[class*="article"]')
        )
        if len(text) > 20:
            return text

        text = self._content_from_nodes([response])
        if len(text) > 20:
            return text
        return ""

    def _content_from_nodes(self, nodes):
        paragraphs = []
        seen = set()
        for node in nodes:
            for paragraph in node.css("p"):
                text = " ".join(paragraph.xpath(".//text()").getall())
                text = " ".join(text.split()).strip()
                if (
                    not text
                    or text in seen
                    or any(re.search(pattern, text, re.I) for pattern in self._boilerplate_patterns)
                ):
                    continue
                seen.add(text)
                paragraphs.append(text)
        return self._clip_content("\n\n".join(paragraphs))

    def _clip_content(self, text):
        if len(text) <= self.max_content_chars:
            return text
        limit = self.max_content_chars
        paragraph_end = text.rfind("\n\n", 0, limit + 1)
        if paragraph_end >= int(limit * 0.7):
            return text[:paragraph_end].rstrip()
        sentence_end = max(text.rfind(mark, 0, limit + 1) for mark in "。！？")
        if sentence_end >= int(limit * 0.7):
            return text[: sentence_end + 1].rstrip()
        return text[:limit].rstrip()

    def _is_finance_relevant(self, title, content):
        sample = f"{title} {content[:1000]}"
        return any(term.lower() in sample.lower() for term in self._finance_terms)

    def _extract_published_at(self, response):
        val = response.css(
            'meta[property="article:published_time"]::attr(content)'
        ).get()
        if val:
            dt = self._parse_datetime(val)
            if dt:
                return dt.isoformat()

        val = response.css('meta[name="pubdate"]::attr(content)').get()
        if val:
            dt = self._parse_datetime(val)
            if dt:
                return dt.isoformat()

        for sel in [
            'time[datetime]::attr(datetime)',
            'time::attr(datetime)',
            'time::text',
        ]:
            val = response.css(sel).get()
            if val:
                dt = self._parse_datetime(val)
                if dt:
                    return dt.isoformat()

        for script in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(script)
            except json.JSONDecodeError:
                continue
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict):
                    for key in ("datePublished", "dateModified", "pubDate"):
                        if key in item:
                            dt = self._parse_datetime(item[key])
                            if dt:
                                return dt.isoformat()

        return None

    def _parse_datetime(self, val):
        if not val:
            return None
        val = str(val).strip()
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            pass
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
        ):
            try:
                dt = datetime.strptime(val, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                return dt
            except ValueError:
                continue
        return self._parse_relative_time(val)

    def _parse_relative_time(self, text):
        text = str(text).strip()
        now = datetime.now(UTC)
        m = re.search(r"(\d+)\s*小時前", text)
        if m:
            return now - timedelta(hours=int(m.group(1)))
        m = re.search(r"(\d+)\s*分鐘前", text)
        if m:
            return now - timedelta(minutes=int(m.group(1)))
        m = re.search(r"(\d+)\s*秒前", text)
        if m:
            return now - timedelta(seconds=int(m.group(1)))
        m = re.search(r"(\d+)\s*天前", text)
        if m:
            return now - timedelta(days=int(m.group(1)))
        if "昨天" in text:
            return now - timedelta(days=1)
        if "剛剛" in text:
            return now
        return None

    def errback(self, failure):
        self.logger.error("Request failed: %s", failure.request.url)
