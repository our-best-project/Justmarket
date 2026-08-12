"""Scrapy Item，欄位對齊 articles 契約（03_SD §1.1 / 04_API_v2 §4.1）。

spider 只需填「抓得到的原始欄位」（title/url/content/published_at）；
API/公告來源若沒有單篇 permalink，可額外填 source_record_id 作為穩定資料列身分。
契約補齊（source_type/content_scope/status/related_tickers 等）交給 persistence。
"""

import scrapy


class ArticleItem(scrapy.Item):
    title = scrapy.Field()
    url = scrapy.Field()
    content = scrapy.Field()
    published_at = scrapy.Field()  # 期望 ISO8601 字串
    source_record_id = scrapy.Field()  # 選填：來源 API 的穩定 record identity
