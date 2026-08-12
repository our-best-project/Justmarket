"""MOPS 重大訊息 spider（即時 + 歷史兩模式）。

採官方新版 JSON API。MOPS 沒有單篇 permalink，因此 url 是官方查詢頁，
source_record_id 才是公告的穩定唯一鍵。抓取範圍以 ``since`` 控制。

兩種模式：
- **即時模式**（不帶 ``companies``）：打 t05sr01_1 滾動清單，全市場最近公告。
  此端點硬上限約 201 筆／近 20 天，無法回填半年，但每日增量足夠。
- **歷史模式**（帶 ``companies=...``）：逐檔打歷史端點 t05st01
  （body 需 companyId/year/month/firstDay/lastDay，其中 all-company 查詢不支援，
  故只能按公司），再打 t05st01_detail 取全文。可回填半年，但範圍限指定股票。
  用法：``-a companies=2330,2454 -a since=2026-01-29``，
  或 ``-a companies=watchlist``（展開本檔頂端的 WATCHLIST 清單）。

歷史（回填）與即時（每日）各自獨立：每日增量不帶 companies＝全市場，watchlist 不影響它。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import scrapy

from news_crawler.items import ArticleItem

# ───────────────────────────────────────────────────────────────────────────
# 回填觀察清單（WATCHLIST）—— 只在「歷史回填」時生效，不影響每日增量。
#
# 用法：跑回填時傳 `-a companies=watchlist`（ingestion CLI 用 --companies watchlist），
# 就會只回填下面這些股票的半年歷史重大訊息。
#   - 每日增量（不帶 companies）一律走即時端點抓「全市場」，跟本清單無關。
#   - 要追蹤哪些就留哪些；不要的整行註解掉或刪掉即可。
#   - 完全清空 → 回填時等於沒指定公司，不會抓任何歷史（會提醒）。
# 代號後的名稱只是註解，程式只讀代號。
# ───────────────────────────────────────────────────────────────────────────
WATCHLIST = [
    # ── 1. 權值龍頭 & 經典 AI 老鬼 ──
    "2330",  # 台積電
    "2317",  # 鴻海
    "2454",  # 聯發科
    "2382",  # 廣達
    "3231",  # 緯創
    "3706",  # 神達
    # ── 2. 記憶體 & 漲價循環（PTT/論壇高熱度） ──
    "2408",  # 南亞科
    "2344",  # 華邦電
    "2337",  # 旺宏
    "3006",  # 晶豪科
    "2327",  # 國巨
    "6488",  # 環球晶
    # ── 3. CPO 矽光子 & 光通訊爆發股 ──
    "3450",  # 聯鈞
    "3081",  # 聯亞
    "4979",  # 華星光
    "3163",  # 波若威
    # ── 4. 重電電網、液冷散熱與 AI 基建 ──
    "2308",  # 台達電
    "3017",  # 奇鋐
    "3324",  # 雙鴻
    "1519",  # 華城
    "1503",  # 士電
    "1513",  # 中興電
    # ── 5. 先進封裝 CoWoS / 玻璃基板 / 測試探針（黑馬飆股族群） ──
    "3711",  # 日月光投控
    "6683",  # 雍智科技
    "6217",  # 中探針
    "8027",  # 鈦昇
    "3042",  # 晶技
    "5274",  # 信驊
    # ── 6. 低軌衛星 (SpaceX) & PCB 族群 ──
    "2313",  # 華通
    "3491",  # 昇達科
    "2383",  # 台光電
    "3037",  # 欣興
    # ── 7. 熱門金控、航運與題材股 ──
    "2881",  # 富邦金
    "2882",  # 國泰金
    "2891",  # 中信金
    "2886",  # 兆豐金
    "2884",  # 玉山金
    "2603",  # 長榮
    "6753",  # 龍德造船
    "2412",  # 中華電
]

class TwseMopsFinanceSpider(scrapy.Spider):
    name = "twse_mops_finance"
    source = "公開資訊觀測站 MOPS"
    source_prefix = "mops"
    source_type = "official"
    content_scope = "full"
    allowed_domains = ["mops.twse.com.tw", "openapi.twse.com.tw"]

    api_root = "https://mops.twse.com.tw/mops/api/"
    list_endpoint = api_root + "home_page/t05sr01_1"
    detail_endpoint = api_root + "t05sr01_1_detail"
    hist_list_endpoint = api_root + "t05st01"
    hist_detail_endpoint = api_root + "t05st01_detail"
    fallback_endpoint = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
    lookup_page = "https://mops.twse.com.tw/mops/#/frontend/t05st01"
    taipei_tz = timezone(timedelta(hours=8))
    valid_markets = {"sii", "otc", "rotc", "pub"}

    custom_settings = {
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "AUTOTHROTTLE_ENABLED": True,
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(
        self,
        markets: str = "sii,otc,rotc,pub",
        count: int | str = 200,
        since: str = "",
        days: int | str = 2,
        companies: str = "",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        requested = [value.strip() for value in markets.split(",") if value.strip()]
        self.markets = [value for value in requested if value in self.valid_markets]
        if not self.markets:
            raise ValueError(f"markets 至少要包含一個合法值：{sorted(self.valid_markets)}")
        self.count = min(200, max(1, int(count)))
        # 帶 companies 即進入歷史模式（逐檔回填），否則走即時全市場模式。
        # 特例：companies=watchlist → 展開為檔案內建的 WATCHLIST 清單。
        companies = str(companies or "").strip()
        if companies.lower() == "watchlist":
            self.companies = list(WATCHLIST)
        else:
            self.companies = [c.strip() for c in companies.split(",") if c.strip()]
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
        self._seen_record_ids: set[str] = set()
        self._pending_market_lists = len(self.markets)
        self._scheduled_details = 0

    def _before_since(self, roc_date) -> bool:
        """民國 YYYMMDD 是否早於 since（早於則跳過，不必抓明細）。"""
        digits = "".join(char for char in str(roc_date or "") if char.isdigit())
        if len(digits) != 7:
            return False
        try:
            day = datetime(
                int(digits[:3]) + 1911,
                int(digits[3:5]),
                int(digits[5:7]),
                tzinfo=self.taipei_tz,
            )
        except ValueError:
            return False
        return day < self._since

    async def start(self):
        if self.companies:
            # 歷史模式：逐檔 × 逐民國年（since 到今日跨越的年）查歷史清單。
            for company_id in self.companies:
                for roc_year in self._roc_years():
                    yield self._json_post(
                        self.hist_list_endpoint,
                        {
                            "companyId": company_id,
                            "year": str(roc_year),
                            "month": "all",
                            "firstDay": "",
                            "lastDay": "",
                        },
                        callback=self.parse_hist_list,
                        cb_kwargs={"company_id": company_id},
                    )
            return
        for market in self.markets:
            yield self._json_post(
                self.list_endpoint,
                {"count": self.count, "marketKind": market},
                callback=self.parse_list,
                cb_kwargs={"market": market},
            )

    def _roc_years(self) -> list[int]:
        today = datetime.now(self.taipei_tz)
        return list(range(self._since.year - 1911, today.year - 1911 + 1))

    def parse_hist_list(self, response: scrapy.http.Response, company_id: str):
        payload = self._payload(response, "歷史列表")
        if not payload:
            return
        for row in (payload.get("result", {}).get("data") or []):
            # row = [代號, 名稱, 發言日期, 發言時間, 主旨, {apiName, parameters}]
            if not isinstance(row, list) or len(row) < 6:
                continue
            roc_date = str(row[2] or "").strip()
            if self._before_since(roc_date):
                continue
            detail = row[5] if isinstance(row[5], dict) else {}
            params = detail.get("parameters") or {}
            market = str(params.get("marketKind") or "").strip()
            serial = str(params.get("serialNumber") or "").strip()
            enter_date = str(params.get("enterDate") or "").strip()
            if not all((market, serial, enter_date)):
                continue
            record_id = f"mops:{market}:{enter_date}:{company_id}:{serial}"
            if record_id in self._seen_record_ids:
                continue
            self._seen_record_ids.add(record_id)
            yield self._json_post(
                self.hist_detail_endpoint,
                {
                    "marketKind": market,
                    "companyId": company_id,
                    "serialNumber": int(serial) if serial.isdigit() else serial,
                    "enterDate": enter_date,
                },
                callback=self.parse_detail,
                cb_kwargs={
                    "market": market,
                    "record_id": record_id,
                    "list_record": {
                        "companyId": company_id,
                        "subject": str(row[4] or "").strip(),
                        "companyAbbreviation": str(row[1] or "").strip(),
                    },
                },
            )

    def parse_list(self, response: scrapy.http.Response, market: str):
        payload = self._payload(response, "列表")
        if payload:
            for record in (payload.get("result", {}).get("data") or []):
                params = (record.get("url") or {}).get("parameters") or {}
                company_id = str(params.get("companyId") or record.get("companyId") or "").strip()
                date = str(params.get("date") or "").strip()
                serial = str(params.get("serialNumber") or "").strip()
                record_id = f"mops:{market}:{date}:{company_id}:{serial}"
                if not all((company_id, date, serial)) or record_id in self._seen_record_ids:
                    continue
                if self._before_since(date):
                    continue
                self._seen_record_ids.add(record_id)
                self._scheduled_details += 1
                detail_params = {
                    "date": date,
                    "companyId": company_id,
                    "serialNumber": int(serial) if serial.isdigit() else serial,
                }
                yield self._json_post(
                    self.detail_endpoint,
                    detail_params,
                    callback=self.parse_detail,
                    cb_kwargs={
                        "market": market,
                        "record_id": record_id,
                        "list_record": record,
                    },
                )

        self._pending_market_lists -= 1
        if self._pending_market_lists == 0 and self._scheduled_details == 0:
            self.logger.info("MOPS 即時列表跨日為空，改抓 TWSE OpenAPI 最近公告")
            yield scrapy.Request(
                self.fallback_endpoint,
                callback=self.parse_openapi_fallback,
                headers={"Accept": "application/json"},
            )

    def parse_detail(
        self,
        response: scrapy.http.Response,
        market: str,
        record_id: str,
        list_record: dict,
    ):
        payload = self._payload(response, "明細")
        result = payload.get("result") if payload else None
        if not result or not result.get("data"):
            return

        fields = [str(value.get("main") or "").strip() for value in result.get("titles", [])]
        values = result["data"][0]
        row = dict(zip(fields, values))
        company_id = str(result.get("companyId") or list_record.get("companyId") or "").strip()
        company_name = str(
            result.get("companyAbbreviation") or list_record.get("companyAbbreviation") or ""
        ).strip()
        subject = self._clean(row.get("主旨") or list_record.get("subject"))
        description = self._clean_multiline(row.get("說明"))
        published_at = self._roc_datetime(row.get("發言日期"), row.get("發言時間"))
        if len(subject) < 4 or len(description) < 40 or not published_at:
            return

        facts = [
            f"公司：{company_id} {company_name}".strip(),
            f"符合條款：{self._clean(row.get('符合條款'))}",
            f"事實發生日：{self._clean(row.get('事實發生日'))}",
            "",
            description,
        ]
        title = f"{company_id} {company_name}｜{subject}".strip()
        yield ArticleItem(
            title=title,
            url=self._lookup_url(company_id, row.get("發言日期"), record_id),
            content="\n".join(facts).strip(),
            published_at=published_at,
            source_record_id=record_id,
        )

    def parse_openapi_fallback(self, response: scrapy.http.Response):
        try:
            records = json.loads(response.text)
        except json.JSONDecodeError as exc:
            self.logger.error("TWSE OpenAPI JSON 解析失敗：%s", exc)
            return
        if not isinstance(records, list):
            return

        for record in records:
            company_id = self._clean(record.get("公司代號"))
            company_name = self._clean(record.get("公司名稱"))
            subject = self._clean(record.get("主旨 "))
            description = self._clean_multiline(record.get("說明"))
            roc_date = record.get("發言日期")
            time_value = record.get("發言時間")
            if self._before_since(roc_date):
                continue
            published_at = self._roc_datetime(roc_date, time_value)
            if len(subject) < 4 or len(description) < 40 or not published_at:
                continue
            natural_key = "|".join(
                (company_id, self._clean(roc_date), self._clean(time_value), subject)
            )
            digest = hashlib.sha256(natural_key.encode("utf-8")).hexdigest()[:16]
            record_id = f"mops:openapi:{digest}"
            if record_id in self._seen_record_ids:
                continue
            self._seen_record_ids.add(record_id)
            facts = [
                f"公司：{company_id} {company_name}".strip(),
                f"符合條款：{self._clean(record.get('符合條款'))}",
                f"事實發生日：{self._clean(record.get('事實發生日'))}",
                "",
                description,
            ]
            yield ArticleItem(
                title=f"{company_id} {company_name}｜{subject}".strip(),
                url=self._lookup_url(company_id, roc_date, record_id),
                content="\n".join(facts).strip(),
                published_at=published_at,
                source_record_id=record_id,
            )

    def _json_post(self, url: str, body: dict, *, callback, cb_kwargs: dict) -> scrapy.Request:
        return scrapy.Request(
            url,
            method="POST",
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            callback=callback,
            cb_kwargs=cb_kwargs,
            dont_filter=True,
        )

    def _payload(self, response: scrapy.http.Response, label: str) -> dict | None:
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            self.logger.error("MOPS %s JSON 解析失敗：%s", label, exc)
            return None
        if payload.get("code") != 200:
            self.logger.warning("MOPS %s API 拒絕：%s", label, payload.get("message"))
            return None
        return payload

    def _lookup_url(self, company_id: str, roc_date, record_id: str) -> str:
        date = "".join(char for char in str(roc_date or "") if char.isdigit())
        query = urlencode(
            {
                "companyId": company_id,
                "year": date[:3],
                "month": date[3:5],
                "record": record_id,
            }
        )
        return f"{self.lookup_page}?{query}"

    @staticmethod
    def _clean(value) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _clean_multiline(value) -> str:
        lines = [" ".join(line.split()) for line in str(value or "").replace("\r\n", "\n").split("\n")]
        return "\n".join(line for line in lines if line)

    def _roc_datetime(self, roc_date, time_value) -> str | None:
        date = "".join(char for char in str(roc_date or "") if char.isdigit())
        clock = "".join(char for char in str(time_value or "") if char.isdigit()).zfill(6)
        if len(date) != 7 or len(clock) != 6:
            return None
        try:
            dt = datetime(
                int(date[:3]) + 1911,
                int(date[3:5]),
                int(date[5:7]),
                int(clock[:2]),
                int(clock[2:4]),
                int(clock[4:6]),
                tzinfo=self.taipei_tz,
            )
        except ValueError:
            return None
        return dt.isoformat()
