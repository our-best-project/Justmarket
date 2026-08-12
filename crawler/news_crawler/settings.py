"""獨立 Scrapy runtime 設定。

此 package 不 import Graph、模型、資料庫或 ``app.*``。所有 crawl 一律只輸出 feed；
是否驗證、升版與寫入資料庫由外層 Spider Forge 控制面決定。
"""

from .policy import BROWSER_HEADERS, BROWSER_USER_AGENT

BOT_NAME = "news_crawler"
SPIDER_MODULES = ["news_crawler.spiders"]
NEWSPIDER_MODULE = "news_crawler.spiders"

# Runtime 保留固定瀏覽器 headers 與課程用途標記；不做代理、隨機 UA 或 CAPTCHA。
ROBOTSTXT_OBEY = False
USER_AGENT = BROWSER_USER_AGENT
DEFAULT_REQUEST_HEADERS = BROWSER_HEADERS

# 禮貌爬取 + 天然 rate limit
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 10.0
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_DELAY = 0.5

# 硬邊界：Scrapy runtime 永遠不寫 DB。Graph 驗證通過後由可信 ingestion 負責持久化。
ITEM_PIPELINES = {}

# 外層以 SPIDERFORGE_ALLOWED_DOMAINS 注入可信網域；middleware 會拒絕超出清單的請求。
DOWNLOADER_MIDDLEWARES = {
    "news_crawler.middlewares.AllowedDomainsMiddleware": 50,
}

# scrapy-playwright（JS 站用；純 HTTP 站不觸發 playwright，不影響效能）
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
FEED_EXPORT_ENCODING = "utf-8"
TELNETCONSOLE_ENABLED = False
