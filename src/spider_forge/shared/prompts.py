"""模型提示與資料結構常數。

純字串／dict 常數，從原本 graph.py 逐字搬移，未改動任何內容。
"""

from __future__ import annotations

DEFAULT_TARGET_SCHEMA = {
    "fields": {
        "title": {"type": "string", "required": True},
        "url": {"type": "url", "required": True},
        "content": {
            "type": "string",
            "required": True,
            "mode": "faithful_excerpt",
            "max_chars": 6000,
        },
        "published_at": {"type": "iso8601_tz", "required": True},
        "source_record_id": {"type": "string", "required": False},
    },
    "source_type": "media",
    "content_scope": "summary_only",
}

CODE_SYSTEM = (
    "你是資深 Scrapy 工程師。只輸出單一 Python 檔的完整程式碼（放在 ```python 圍欄內），"
    "不要解說、不要 selector 猜測清單、不要要求使用者補 HAR。只能依 EvidencePack 實作。"
    "程式碼保持精簡，最多 180 行；共用 headers 只定義一次，禁止長篇註解與重複 fallback。"
)

_SPIDER_CONTRACT = """【硬性契約】
1. 單一檔案、一個 scrapy.Spider 子類別；name/source_prefix 均為 "{source_prefix}"。
2. allowed_domains 必須涵蓋實際請求 host；限速與最多 2 個列表分頁（低速由專案 AutoThrottle 維持）。
   constraints.max_pages 只限制列表翻頁請求，不是整支爬蟲的 response 數；
   禁止用 CLOSESPIDER_PAGECOUNT 實作此限制，以免列表頁占掉文章明細額度。
   付費牆 / CAPTCHA / 登入牆一律不繞——遇到就讓它自然失敗，不要用假資料或繞道硬過。
3. class 屬性 source="{site_name}"、source_type="{source_type}"、
   content_scope="{content_scope}"。
4. 在同一檔案內定義 ArticleItem(scrapy.Item)，欄位為 title、url、content、
   published_at、source_record_id；只 yield ArticleItem，不引用專案內其他模組。
5. 必填 title/url/content/published_at；published_at 必須是含時區 ISO8601。
   媒體 content 是清除廣告/導覽後、保持原文順序與措辭的忠實摘錄，最多
   {max_content_chars} 字；禁止改寫成模型摘要。無真實內文或發布時間就跳過，
   禁止以 title 或目前時間偽造。
6. url 是人工可查證入口。沒有單篇 permalink 的 API/公告另填 source_record_id，
   禁止用假 query 或 fragment 冒充唯一文章 URL。
7. 不寫資料庫、不讀 secrets、不 import 任何專案內模組。
8. 只能使用 EvidencePack 中有 response body 的結構化來源（JSON/RSS/Atom）；
   不得依 URL 名稱猜 JSON path。
   若 browser 被擋但 plain HTTP=200，沿用 EvidencePack 的 safe_request_headers，
   用 Scrapy HTTP/HTML，不得硬切 Playwright。
9. 嚴格遵守 EvidencePack.request.validation 的 URL pattern、排除規則、時效與數量。
   Scrapy SelectorList 沒有 .first()；用 .get()、getall() 或索引。
   只有 access_assessment 為 browser_required_http_blocked 或
   browser_session_required 時，才可 import/use scrapy_playwright。
   requirements 含 browser_transport 時，入口與明細的每一個必要 request 都必須
   設 meta.playwright=True；禁止讓 start_urls 產生未啟用 Playwright 的入口 request。
   候選會由 scrapy runspider 獨立執行，不會載入 crawler runtime settings，因此必須
   在 custom_settings 自帶 scrapy-playwright 的 DOWNLOAD_HANDLERS 與 TWISTED_REACTOR。
   只需要 response DOM 時設 meta.playwright=True 即可；沒有互動需求時禁止
   playwright_include_page，避免 page 生命週期洩漏。
10. permalink 的 path 大小寫、連字號與 query parameter 名稱必須逐字沿用
    EvidencePack 的 observed detail URL；禁止把 ?a= 改成 ?b= 或自行改寫 URL 形式。
11. 日期補 IANA 時區時使用 Python 標準庫 zoneinfo.ZoneInfo；不要為此新增 pytz 依賴。"""

_STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string", "enum": ["api", "dom", "hybrid"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "chosen_api": {"type": "string"},
    },
    "required": ["strategy", "confidence", "reason"],
}

_DIAGNOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "failure_type": {"type": "string"},
        "evidence": {"type": "string"},
        "suggested_fix": {"type": "string"},
        "error_signature": {"type": "string"},
    },
    "required": ["failure_type", "suggested_fix", "error_signature"],
}
