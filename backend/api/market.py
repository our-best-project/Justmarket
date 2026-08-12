"""大盤總覽端點：GET /market/overview —— 首頁首屏的「大盤脈搏」。

【2026-08-08 起接真資料】資料源 market_index_daily（Yahoo chart API 每日盤後
upsert，見 app/market_index/）＋ chip_data（漲跌家數，LAG 重算）。
本檔曾長期是「形狀真、數字假」的 walking skeleton——當時全庫沒有指數日資料；
market_index 模組上線後那個前提已消失，mock 卻留了下來（工程審查 P3-09）。

1/5/20 日報酬用「交易日」收盤計算（取近 21 根收盤），不是日曆日。
⚠️ 不要拿個股 TaiwanStockPrice 當 TAIEX 用——那是兩種東西。
"""
from datetime import date as _date
from datetime import datetime

from fastapi import APIRouter, Query

from backend.core.cache import TTLCache, cache_ttl_seconds
from backend.db.session import get_pooled_conn

router = APIRouter(prefix="/market", tags=["market"])

_PERIOD_LABELS = {1: "1 日", 5: "5 日", 20: "20 日"}

# 漲跌家數：與 /market/breadth 同一套 LAG 重算（chip_data.return_1d 是前向報酬，
# 最新日永遠 NULL，不能用）。這裡只需要三個 count，抽輕量版。
_OVERVIEW_BREADTH_SQL = """
with px as (
    select ticker, date, close,
           lag(close) over (partition by ticker order by date) as prev
    from chip_data
    where date <= %(d)s and date > %(d)s - 20
),
day as (
    select close / nullif(prev, 0) - 1 as chg
    from px where date = %(d)s and prev is not null and close > 0
)
select count(*) filter (where chg > 0) as advancers,
       count(*) filter (where chg < 0) as decliners,
       count(*) filter (where chg = 0) as unchanged
from day
"""


@router.get("/overview")
def market_overview(
    windows: str = Query("1,5,20", description="交易日視窗，逗號分隔（例：1,5,20）"),
):
    """首頁大盤脈搏。牛熊與 bar 位置由 periods[].change_pct 正負與大小決定。"""
    wanted = []
    for token in windows.split(","):
        token = token.strip()
        if token.isdigit() and int(token) in _PERIOD_LABELS:
            wanted.append(int(token))
    if not wanted:
        wanted = [1, 5, 20]

    with get_pooled_conn() as conn, conn.cursor() as cur:
        # 近 21 根收盤（20 日報酬需要 21 根），正序
        cur.execute(
            "select date, close, change_pct from ("
            "  select date, close, change_pct from market_index_daily"
            "  where index_code = 'TAIEX' order by date desc limit 21"
            ") t order by date",
        )
        rows = cur.fetchall()
        b = None
        if rows:
            cur.execute(_OVERVIEW_BREADTH_SQL, {"d": rows[-1]["date"]})
            b = cur.fetchone()

    if not rows:
        # 資料表空（新環境尚未跑過 market_index 批次）——照實說，不編數字
        return {"index_code": "TAIEX", "index_name": "臺灣加權指數",
                "close": None, "as_of": None, "session_state": "closed",
                "periods": [], "breadth": None,
                "source": {"provider": "market_index_daily 尚無資料",
                           "dataset": "market_index_daily", "mode": "unavailable"}}

    closes = [r["close"] for r in rows]
    latest = rows[-1]
    periods = []
    for w in wanted:
        if w == 1:
            # 單日直接用批次算好的 change_pct（避免收盤價相除的二次取整）
            change = latest["change_pct"] or 0.0
            anchor = rows[-2]["date"] if len(rows) >= 2 else latest["date"]
        elif len(closes) > w and closes[-1 - w]:
            change = (closes[-1] / closes[-1 - w] - 1) * 100
            anchor = rows[-1 - w]["date"]
        else:
            continue        # 歷史不足這個視窗就略過，不硬給
        periods.append({"trading_days": w, "label": _PERIOD_LABELS[w],
                        "change_pct": round(change, 2),
                        "anchor_date": anchor.isoformat()})

    return {
        "index_code": "TAIEX",
        "index_name": "臺灣加權指數",
        "close": round(latest["close"], 2),
        "as_of": f"{latest['date'].isoformat()}T13:30:00+08:00",   # 台股收盤時間
        "session_state": "closed",                                  # 盤後資料，一律已收盤
        "periods": periods,
        "breadth": ({"advancers": b["advancers"], "decliners": b["decliners"],
                     "unchanged": b["unchanged"]} if b and b["advancers"] is not None else None),
        "source": {"provider": "Yahoo Finance（每日盤後批次）",
                   "dataset": "market_index_daily + chip_data",
                   "mode": "real"},
    }


# ───────────────────────────────────────────────────────────────────────────
# GET /market/global —— 各國大盤指數（前端「今日大局」GLOBAL PULSE 面板真資料）
#
# 回傳形狀對齊前端 GlobalMarket（web/.../src/types.ts:100-119）。
# 資料源 Yahoo chart API，由 backend.market_index.daily_batch 每日盤後 upsert 進兩張表：
#   market_indices（靜態主檔）＋ market_index_daily（每日收盤）。
# return5d/return20d/series20 一律用「交易日」即時算，不落地（呼應本檔頂 README:36 精神）。
# ───────────────────────────────────────────────────────────────────────────

_SOURCE_NAME = "Yahoo Finance"

# market_index_daily 由 backend.market_index.daily_batch 每日盤後 upsert，盤中不會變
# → 每個請求都重查兩張表再重算 5d/20d 報酬沒有意義。TTL 內直接回上次結果。
_CACHE = TTLCache(ttl_seconds=cache_ttl_seconds())

# 每指數取最近 21 個交易日（20 日報酬需 21 根收盤），正序回傳
_RECENT_SQL = """
select index_code, date, close, change_pct
from (
  select index_code, date, close, change_pct,
         row_number() over (partition by index_code order by date desc) as rn
  from market_index_daily
) t
where rn <= 21
order by index_code, date
"""


def _pct(newer: float, older: float) -> float:
    """交易日報酬 %。older 無效時回 0（避免除零）。"""
    if not older:
        return 0.0
    return (newer / older - 1) * 100


def _session_state(timezone_str: str) -> str:
    """依當地時間粗判 open/preopen/closed（簡化版：不含各國實際時段/夏令時）。

    zoneinfo 在無系統 tz 庫的環境（Windows 本地）會失敗 → 優雅降級為 'closed'。
    正式判定日後細修。
    """
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(timezone_str))
        if now.weekday() >= 5:          # 週末
            return "closed"
        if 9 <= now.hour < 16:
            return "open"
        if 7 <= now.hour < 9:
            return "preopen"
        return "closed"
    except Exception:
        return "closed"


def _status(latest: _date) -> str:
    """資料新鮮度：4 日內 ready（含週末假期緩衝）、10 日內 stale、更舊 unavailable。"""
    delta = (_date.today() - latest).days
    if delta <= 4:
        return "ready"
    if delta <= 10:
        return "stale"
    return "unavailable"


def _empty(meta: dict) -> dict:
    """該指數尚無每日資料時的骨架列（status=unavailable）。"""
    return {
        "id": meta["index_code"], "index": meta["index_code"], "name": meta["name"],
        "country": meta["country"], "currency": meta["currency"],
        "timezone": meta["timezone"], "session": "closed",
        "tradeDate": "", "asOf": "", "delay": "EOD",
        "change1d": 0.0, "return5d": 0.0, "return20d": 0.0,
        "mapX": float(meta["map_x"]), "mapY": float(meta["map_y"]),
        "series20": [], "source": _SOURCE_NAME, "status": "unavailable",
    }


def _build_global() -> list[dict]:
    """實際查 DB 組出各國指數列表。由 market_global() 經快取呼叫。

    ⚠️ _session_state() 依當地時間判斷 open/preopen/closed，會被快取凍結最多
    TTL 秒。60 秒的誤差對「開盤/收盤」這種以小時為單位的狀態沒有實質影響。
    """
    with get_pooled_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from market_indices order by display_order")
        metas = cur.fetchall()
        cur.execute(_RECENT_SQL)
        recent = cur.fetchall()

    by_code: dict[str, list[dict]] = {}
    for row in recent:  # 已按 (index_code, date) 正序
        by_code.setdefault(row["index_code"], []).append(row)

    out = []
    for meta in metas:
        series = by_code.get(meta["index_code"], [])
        if not series:
            out.append(_empty(meta))
            continue
        closes = [s["close"] for s in series]
        latest = series[-1]
        out.append({
            "id": meta["index_code"],
            "index": meta["index_code"],
            "name": meta["name"],
            "country": meta["country"],
            "currency": meta["currency"],
            "timezone": meta["timezone"],
            "session": _session_state(meta["timezone"]),
            "tradeDate": latest["date"].isoformat(),
            "asOf": latest["date"].isoformat(),
            "delay": "EOD",
            "change1d": round(latest["change_pct"] or 0.0, 2),
            "return5d": round(_pct(closes[-1], closes[-6]), 2) if len(closes) >= 6 else 0.0,
            "return20d": round(_pct(closes[-1], closes[-21]), 2) if len(closes) >= 21 else 0.0,
            "mapX": float(meta["map_x"]),
            "mapY": float(meta["map_y"]),
            "series20": [{"date": s["date"].isoformat(), "value": s["close"]}
                         for s in series[-20:]],
            "source": _SOURCE_NAME,
            "status": _status(latest["date"]),
        })
    return out


@router.get("/global")
def market_global():
    """各國大盤指數列表，供前端 GLOBAL PULSE 面板。回 GlobalMarket[]。"""
    return _CACHE.get_or_compute("global", _build_global)


# ─────────────────────────────────────────────────────────────
# 台股盤面廣度與產業表現（供前端「今日大局」的台股區塊與產業區塊）
#
# ⚠️ 資料陷阱：chip_data.return_1d 是**前向報酬**（close[t+1]/close[t]-1），
#    最新交易日永遠是 NULL——拿它算「今日漲跌家數」會全部得到 0。
#    當日漲跌必須用前一交易日收盤比，故下面用 LAG 自行計算。
#
# 漲跌停判準用 ±9.5%：台股單日限制 10%，但收盤價經跳動單位取整後常落在
# 9.5~10% 之間，抓死 10% 會漏掉大部分真正的漲跌停。
# ⚠️ 成交值一律除 1e8（億元）。原本三處都寫 /1e9 卻標示「億」——billion 是 10 億，
# 不是 1 億，於是畫面上的市場總成交值小了 10 倍（1,111.9 億 vs 實際 11,119.1 億）。
# 判斷依據：市場總額不可能小於前 12 大個股的合計 3,739 億。
# turnoverVs20d 因為分子分母同錯所以比值正確，只有絕對值是錯的——這種錯最難察覺，
# 所以回傳欄位一律改名為 turnoverE，不再出現 Billion 字樣。
_BREADTH_SQL = """
with px as (
    select ticker, date, close, volume,
           lag(close) over (partition by ticker order by date) as prev
    from chip_data
    where date <= %(d)s and date > %(d)s - 20      -- 只需前一交易日，20 天窗足以跨連假
),
day as (
    select ticker, close, volume, close / nullif(prev, 0) - 1 as chg
    from px where date = %(d)s and prev is not null and close > 0
)
select
    count(*) filter (where chg > 0)        as advancers,
    count(*) filter (where chg = 0)        as unchanged,
    count(*) filter (where chg < 0)        as decliners,
    count(*) filter (where chg >= 0.095)   as limit_up,
    count(*) filter (where chg <= -0.095)  as limit_down,
    sum(close * volume) / 1e8              as turnover_e,          -- 億元。1e9 是 billion，不是億
    percentile_cont(0.5) within group (order by chg) as median_return,
    count(*)                               as covered
from day
"""

_INDUSTRY_SQL = """
with px as (
    select ticker, date, close, volume,
           lag(close) over (partition by ticker order by date) as prev
    from chip_data
    where date <= %(d)s and date > %(d)s - 20
),
day as (
    select ticker, close, volume, close / nullif(prev, 0) - 1 as chg
    from px where date = %(d)s and prev is not null and close > 0
)
select k.industry                                as name,
       count(*) filter (where d.chg > 0)         as advancers,
       count(*) filter (where d.chg = 0)         as unchanged,
       count(*) filter (where d.chg < 0)         as decliners,
       avg(d.chg)                                as return_1d,
       sum(d.close * d.volume) / 1e8             as turnover_e
from day d join tickers k on k.ticker = d.ticker
where k.industry is not null
group by k.industry
having count(*) >= 3            -- 成分不足 3 檔的產業，平均數不具代表性
order by avg(d.chg) desc
"""

# 20 日均量：用來判斷今天量能是放大還是萎縮
_TURNOVER_AVG_SQL = """
select avg(t) from (
    select date, sum(close * volume) / 1e8 as t
    from chip_data
    where date <= %(d)s and date > %(d)s - 40 and close > 0
    group by date order by date desc limit 20
) s
"""


# 成交值前 N 大個股，供前端「成交熱度」泡泡圖。
# 用成交「值」(close×volume) 而非成交「量」(股數)——不同價位的股票股數不可比，
# 100 元股票的一張與 1000 元股票的一張，市場投入的資金差 10 倍。
_TOP_TURNOVER_SQL = """
with px as (
    select ticker, date, close, volume,
           lag(close) over (partition by ticker order by date) as prev
    from chip_data
    where date <= %(d)s and date > %(d)s - 20
)
select p.ticker,
       coalesce(k.name, p.ticker)              as name,
       coalesce(k.industry, '')                as industry,
       p.close                                 as close,
       p.close * p.volume / 1e8                as turnover_e,
       p.close / nullif(p.prev, 0) - 1         as chg
from px p left join tickers k on k.ticker = p.ticker
where p.date = %(d)s and p.prev is not null and p.close > 0 and p.volume > 0
order by p.close * p.volume desc
limit %(n)s
"""

@router.get("/breadth")
def market_breadth():
    """台股盤面廣度＋產業表現。前端「今日大局」的台股區塊與產業區塊用。

    資料全部由 chip_data ＋ tickers.industry 聚合而來，不需外部來源。
    """
    with get_pooled_conn() as conn, conn.cursor() as cur:
        cur.execute("select max(date) as d from chip_data")
        row = cur.fetchone()
        latest = row["d"] if row else None
        if latest is None:
            return {"status": "unavailable", "breadth": None, "industries": []}

        cur.execute(_BREADTH_SQL, {"d": latest})
        b = cur.fetchone()
        cur.execute(_TURNOVER_AVG_SQL, {"d": latest})
        avg_row = cur.fetchone()
        cur.execute(_INDUSTRY_SQL, {"d": latest})
        industries = cur.fetchall()
        # 12 檔。前端泡泡面積嚴格正比於成交值，檔數一多尾端就會小到放不下股名；
        # 前 12 大的成交值差距約 3 倍，直徑差 1.7 倍，仍在看得出來的範圍內。
        cur.execute(_TOP_TURNOVER_SQL, {"d": latest, "n": 12})
        top = cur.fetchall()

        # 指數收盤與漲跌幅沿用既有的 market_index_daily（TAIEX）
        cur.execute(
            "select close, change_pct from market_index_daily "
            "where index_code = 'TAIEX' order by date desc limit 1")
        idx = cur.fetchone() or {}

    turnover = float(b["turnover_e"] or 0)
    avg20 = float(avg_row["avg"] or 0) if avg_row else 0
    return {
        "status": _status(latest),
        "breadth": {
            "indexClose": round(float(idx.get("close") or 0), 2),
            "indexChange1d": round(float(idx.get("change_pct") or 0), 2),
            "advancers": b["advancers"], "unchanged": b["unchanged"],
            "decliners": b["decliners"],
            "limitUp": b["limit_up"], "limitDown": b["limit_down"],
            "turnoverE": round(turnover, 1),
            # 量能比：今日成交值 ÷ 近 20 日均值；無均值時給 1.0（中性）不給 0
            "turnoverVs20d": round(turnover / avg20, 2) if avg20 else 1.0,
            "medianReturn": round(float(b["median_return"] or 0) * 100, 2),
            "source": "TWSE 收盤資料（FinMind）",
            "asOf": latest.isoformat(),
            "coveredTickers": b["covered"],
        },
        "industries": [{
            "id": i["name"], "name": i["name"],
            "advancers": i["advancers"], "unchanged": i["unchanged"],
            "decliners": i["decliners"],
            "return1d": round(float(i["return_1d"] or 0) * 100, 2),
            "turnoverE": round(float(i["turnover_e"] or 0), 2),
        } for i in industries],
        "topTurnover": [{
            "ticker": t["ticker"], "name": t["name"], "industry": t["industry"],
            "close": round(float(t["close"] or 0), 2),
            "turnoverE": round(float(t["turnover_e"] or 0), 1),   # 億元
            "change1d": round(float(t["chg"] or 0) * 100, 2),
        } for t in top],
    }
