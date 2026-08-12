"""V3 Demo 端點：GET /demo/bootstrap —— 一次回傳首頁要的全部資料。

【為什麼是一個大端點而不是沿用 /events/today】
frontend/ 前端只呼叫 bootstrap 與 market/overview 兩個端點，
一次載入、之後純前端切換。這與既有的 /events/today + /events/{id} + /timeline 分離式
契約是兩套不同架構 —— 這裡不動既有端點，另開 demo 專用端點，兩者並存。

【契約來源】
形狀由 frontend/src/types.ts 的 MarketEvent／EventCatalog 定義，與 /events、/tickers 那組分離式契約不同。

【與 DB 的三個形狀落差，都在這裡吸收】
1. related_tickers：DB 是字串陣列 ["2330"]，前端要 [{ticker,name}]（app.js:343 讀 .ticker/.name）
   → LEFT JOIN tickers 補中文名。查不到 name 就用 ticker 當 name（不讓前端拿到 undefined）。
2. sources：events.members 有 url/title/source/article_id/has_unique_detail，
   缺前端要的 source_type 與 published_at → LEFT JOIN articles ON article_id 補。
3. timeline：既有 /timeline 端點回 is_current，前端要 current，且要嵌在事件物件裡 → 這裡改名並內嵌。

【目前吐不出來的欄位（誠實標註，不是遺漏）】
- deck：全鏈路沒有此欄位。已與使用者確認改前端讓它可選（app.js 加 null 檢查），API 不提供。
- market_reaction：邏輯已實作，但沙盒 chip_data 是 0 筆 → 實際一律回 null。
  ⚠️ _market_reaction() 從未跑過真實資料，屬未驗證程式碼。
- market_validation / validation_breakdown / chip_evidence：DB 欄位在，但 1925 筆全空
  → 每張卡都會顯示「觀察中」。這是同一條線，不是這裡的 bug。
"""
import json
from datetime import date as Date
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from backend.core.cache import TTLCache, cache_ttl_seconds
from backend.db.session import get_pooled_conn
from backend.services.image_picker import pick_image

router = APIRouter(prefix="/demo", tags=["demo"])

# 資料每日盤後才更新（見下方 BOOTSTRAP_SQL 的凍結樣本說明），但原本每個請求都
# 完整重算：主查詢 1 次 + 每個事件各 1 次 timeline、1 次 market_reaction，
# limit=12 時是 25 次 DB 來回。TTL 內直接回上次結果，完全不碰 DB。
_CACHE = TTLCache(ttl_seconds=cache_ttl_seconds())

# 與 events.py:33 同義：未跑完 LLM/評分的事件缺這些欄位，不可渲染
RENDERABLE = ("e.title IS NOT NULL AND e.categories IS NOT NULL "
              "AND e.importance_stars IS NOT NULL")

# Bootstrap 保留跨日事件供完整事件流與詳情頁使用，但先按事件日排序；
# 首頁再依 meta.edition_date 精準篩選今日事件。
# bootstrap 與單一事件詳情共用同一份欄位定義——兩邊形狀必須一致，
# 否則「從搜尋點進詳情」與「從首頁點進詳情」會渲染出不同結果。
EVENT_COLUMNS = """
    e.event_id,
    e.title,
    e.summary,
    e.occurred_at_iso::date::text             AS date,
    e.occurred_at_iso,
    e.occurred_at_text,
    e.status,
    e.categories,
    e.expected_direction,
    e.related_tickers                         AS raw_tickers,
    e.importance_stars                        AS stars,
    e.market_validation,
    COALESCE(e.verify_state, 'observing')     AS verify_state,
    e.source_count,
    COALESCE(e.industries, '[]'::jsonb)         AS industries,
    COALESCE(e.importance_reasons, '[]'::jsonb) AS importance_reasons,

    -- 落差 1：["2330"] → [{{"ticker":"2330","name":"台積電"}}]
    COALESCE((
        SELECT jsonb_agg(jsonb_build_object('ticker', s.t, 'name', COALESCE(tk.name, s.t)))
        FROM jsonb_array_elements_text(e.related_tickers) AS s(t)
        LEFT JOIN tickers tk ON tk.ticker = s.t
    ), '[]'::jsonb) AS related_tickers,

    -- 落差 2：members ⋈ articles 補 source_type / published_at
    COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
                   'source',            m->>'source',
                   'title',             m->>'title',
                   'url',               m->>'url',
                   'has_unique_detail', COALESCE((m->>'has_unique_detail')::boolean, false),
                   -- 前端 sourceTypeLabels 對未知值有 fallback，但 null 會顯示成空白
                   'source_type',       COALESCE(a.source_type, 'media'),
                   -- 前端直接 new Date()，null 會變 Invalid Date → 退回事件時間
                   'published_at',      COALESCE(a.published_at, e.occurred_at_iso)
               ) ORDER BY COALESCE(a.published_at, e.occurred_at_iso))
        FROM jsonb_array_elements(e.members) AS m
        LEFT JOIN articles a ON a.article_id = m->>'article_id'
    ), '[]'::jsonb) AS sources

"""

# 整期全撈（2026-08-12 改）：舊版跨日期取前 12 件，「本期全部事件」名不符實——
# 8/11 有 20 個可搜事件、首頁完整列表卻只有 12 個。現在先用 EDITION_DAY_SQL 定位
# 收盤日，再回傳該日全部可渲染事件；limit 只是防爆安全上限，正常整期遠低於此。
# 無個股的總經/政策事件也要進來——舊的 tickers>0 濾網是護 demo_v3 detail 頁的
# leadTicker.ticker 裸取，該處已改為條件渲染，濾網功成身退。
BOOTSTRAP_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM events e
WHERE {RENDERABLE}
  AND e.occurred_at_iso::date = ANY(%(days)s)
ORDER BY e.importance_stars DESC,
         e.market_validation DESC NULLS LAST,
         e.occurred_at_iso DESC
LIMIT %(limit)s
"""

# 「事件怎麼走」＝以本事件為錨點、往回 60 天的同 ticker＋同類別事件串。
# 舊版三個坑（2026-08-02 亞馬遜事件實測）：不限時間窗＋ORDER BY ASC 會撈出
# 「最古老的 6 筆」（半年前的一月舊聞）、且本事件自己不保證入列——時間軸與
# 事件毫無相關。新版：本事件必含、其餘取事件日（含）前 60 天內最近的 5 筆。
TIMELINE_SQL = f"""
WITH cur AS (SELECT occurred_at_iso FROM events WHERE event_id = %(eid)s)
SELECT to_char(e.occurred_at_iso, 'MM/DD') AS date,
       e.title,
       (e.event_id = %(eid)s) AS current
FROM events e, cur
WHERE {RENDERABLE}
  AND (e.event_id = %(eid)s
       OR (e.related_tickers @> %(ticker)s::jsonb
           -- 防資料中毒：LLM 偶發把整段號碼區間當股號（實測有事件標了 601 檔，
           -- 2330 連號到 2999），任何代號落在區間內的股票時間軸都被它污染。
           -- 「關於 20+ 檔股票」的事件＝大盤級，不屬於任何個股的故事線 → 排除。
           AND jsonb_array_length(e.related_tickers) <= 20
           AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(e.categories) c
                       WHERE c = ANY(%(cats)s))
           AND e.occurred_at_iso <= cur.occurred_at_iso
           AND e.occurred_at_iso >= cur.occurred_at_iso - interval '60 days'))
ORDER BY e.occurred_at_iso DESC NULLS LAST
LIMIT 6
"""

# 取事件日（含）之前 6 個交易日 + 之後 5 個交易日。
REACTION_SQL = """
WITH cols AS (
    SELECT date, close, volume_ratio_vs_avg20,
           foreign_net, trust_net, dealer_net,
           return_1d, return_3d, return_5d, foreign_consecutive_days
    FROM chip_data WHERE ticker = %s
)
(SELECT * FROM cols WHERE date <= %s ORDER BY date DESC LIMIT 6)
UNION ALL
(SELECT * FROM cols WHERE date >  %s ORDER BY date      LIMIT 5)
"""


def _timeline(conn, event_id: str, raw_tickers: list, cats: list) -> list[dict]:
    if not raw_tickers or not cats:
        return []
    rows = conn.execute(
        TIMELINE_SQL,
        {"eid": event_id, "ticker": json.dumps([raw_tickers[0]]), "cats": cats},
    ).fetchall()
    rows.reverse()  # SQL 取「最近優先」湊滿 6 筆，展示時轉回時間順序（舊→新，結尾是本事件）
    return rows


def _market_reaction(conn, ticker: str, event_date: Date) -> dict | None:
    """個股走勢序列。chip_data 無資料時回 None（前端 app.js:138 有對應文案）。"""
    rows = conn.execute(REACTION_SQL, [ticker, event_date, event_date]).fetchall()
    rows = sorted(rows, key=lambda r: r["date"])
    if not rows:
        return None

    before = [i for i, r in enumerate(rows) if r["date"] <= event_date]
    if not before:
        return None
    anchor_idx = before[-1]
    anchor = rows[anchor_idx]
    if not anchor["close"]:
        return None

    def _f(v, default=0.0):
        return float(v) if v is not None else default

    return {
        "ticker": ticker,
        "event_index": anchor_idx,
        "event_date": event_date.isoformat(),
        "as_of": rows[-1]["date"].isoformat(),
        # 前端對 return_1d/3d 有 null 檢查（app.js:161-162），可以照實傳 null
        "return_1d": _f(anchor["return_1d"], None) if anchor["return_1d"] is not None else None,
        "return_3d": _f(anchor["return_3d"], None) if anchor["return_3d"] is not None else None,
        "return_5d": _f(anchor["return_5d"], None) if anchor["return_5d"] is not None else None,
        # 這兩個前端「沒有」null 檢查（app.js:163-164 直接 .toFixed()/Math.abs()）→ 必須給值
        "volume_ratio": _f(anchor["volume_ratio_vs_avg20"], 1.0),
        "foreign_consecutive_days": anchor["foreign_consecutive_days"] or 0,
        "series": [
            {
                "date": r["date"].isoformat(),
                "close": round(_f(r["close"]), 2),
                "volume_ratio": _f(r["volume_ratio_vs_avg20"], 1.0),
                "foreign_net": int(r["foreign_net"] or 0),
                "trust_net": int(r["trust_net"] or 0),
                "dealer_net": int(r["dealer_net"] or 0),
            }
            for r in rows
        ],
    }


# 搜尋：比對事件標題、摘要、股票代號與公司名稱。
# 三個刻意的設計取捨：
#  1. 排除 occurred_at_iso IS NULL 的事件——它們在列表上顯示不出時間、看起來像壞掉，
#     且無法計算市場驗證分數（rescore_pending_events 要求非空）。
#  2. 不做 market_reaction／timeline——搜尋是列表視圖不畫圖表，省下每筆兩次 DB 往返。
#  3. 公司名稱走 tickers 表反查，使用者搜「台積電」也能命中只標了 2330 的事件。
SEARCH_SQL = f"""
WITH hit AS (
    SELECT DISTINCT e.event_id
    FROM events e
    LEFT JOIN LATERAL jsonb_array_elements_text(e.related_tickers) AS t(code) ON true
    LEFT JOIN tickers k ON k.ticker = t.code
    WHERE {RENDERABLE}
      AND e.occurred_at_iso IS NOT NULL
      AND (e.title ILIKE %(q)s OR e.summary ILIKE %(q)s
           OR t.code ILIKE %(q)s OR k.name ILIKE %(q)s)
)
SELECT
    e.event_id, e.title, e.summary,
    e.occurred_at_iso::date::text AS date, e.occurred_at_iso, e.occurred_at_text,
    e.status, e.categories, e.expected_direction,
    e.related_tickers AS raw_tickers,
    e.importance_stars AS stars, e.market_validation,
    COALESCE(e.verify_state, 'observing') AS verify_state,
    e.source_count,
    COALESCE(e.industries, '[]'::jsonb) AS industries,
    COALESCE(e.importance_reasons, '[]'::jsonb) AS importance_reasons,
    COALESCE((
        SELECT jsonb_agg(jsonb_build_object('ticker', s.t, 'name', COALESCE(tk.name, s.t)))
        FROM jsonb_array_elements_text(e.related_tickers) AS s(t)
        LEFT JOIN tickers tk ON tk.ticker = s.t
    ), '[]'::jsonb) AS related_tickers
FROM events e JOIN hit ON hit.event_id = e.event_id
ORDER BY e.importance_stars DESC,
         e.market_validation DESC NULLS LAST,
         e.occurred_at_iso DESC
LIMIT %(limit)s
"""


EVENT_DETAIL_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM events e
WHERE e.event_id = %s AND {RENDERABLE}
"""


@router.get("/events/{event_id}")
def event_detail(request: Request, event_id: str):
    """單一事件詳情（含時間軸與市場反應）。

    為什麼需要這支：前端原本只從 bootstrap 的那批（預設 12 筆）找事件，
    所以搜尋結果點進詳情頁是空的——而且任何被分享出去的事件連結也打不開。
    詳情頁本來就該是可深連結的，這支補上那個缺口。
    """
    origin = str(request.base_url).rstrip("/")
    with get_pooled_conn() as conn:
        row = conn.execute(EVENT_DETAIL_SQL, [event_id]).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="找不到這個事件")
        raw_tickers = row.pop("raw_tickers") or []
        cats = row["categories"] or []
        row["image"] = pick_image(row["event_id"], cats, origin=origin)
        row["timeline"] = _timeline(conn, row["event_id"], raw_tickers, cats)
        row["market_reaction"] = (
            _market_reaction(conn, raw_tickers[0], Date.fromisoformat(row["date"]))
            if raw_tickers and row["date"] else None
        )
    return row


@router.get("/search")
def search(request: Request,
           q: str = Query(..., min_length=1, max_length=40, description="關鍵字：公司名、股號或事件字詞"),
           limit: int = Query(30, ge=1, le=100)):
    """關鍵字搜尋事件。回傳形狀與 bootstrap 的 events 相容，前端可直接沿用列表渲染。"""
    keyword = q.strip()
    if not keyword:
        return {"query": q, "count": 0, "categories": ["全部"], "events": []}
    # ILIKE 的萬用字元與跳脫字元要中和，否則使用者輸入 % 會變成「比對全部」
    safe = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    origin = str(request.base_url).rstrip("/")

    with get_pooled_conn() as conn:
        events = conn.execute(SEARCH_SQL, {"q": f"%{safe}%", "limit": limit}).fetchall()
        for e in events:
            e.pop("raw_tickers", None)
            e["image"] = pick_image(e["event_id"], e["categories"] or [], origin=origin)
            e["timeline"] = []          # 列表視圖不需要，省下往返
            e["market_reaction"] = None  # 同上；點進詳情頁才會有

    seen: list[str] = []
    for e in events:
        for c in e["categories"] or []:
            if c not in seen:
                seen.append(c)
    return {"query": keyword, "count": len(events),
            "categories": ["全部", *seen], "events": events}


# 有已評分事件的日期清單（給前端完整事件流的日期選單）。
# 下限 2026-08-01：排程與回補是 8 月才補齊的，之前的日子覆蓋殘缺，
# 放進選單只會看到誤導性的稀疏列表（demo 主角 7/31 聯發科走搜尋/詳情頁，不受影響）。
AVAILABLE_DAYS_SQL = f"""
SELECT DISTINCT e.occurred_at_iso::date AS day
FROM events e
WHERE {RENDERABLE}
  AND e.occurred_at_iso::date <= current_date
  AND e.occurred_at_iso::date >= DATE '2026-08-01'
ORDER BY day DESC
LIMIT 30
"""


def _hydrate_events(conn, events, origin: str) -> None:
    """補齊 bootstrap/day 共用的衍生欄位：image / timeline / market_reaction。"""
    for e in events:
        raw_tickers = e.pop("raw_tickers") or []
        cats = e["categories"] or []

        e["image"] = pick_image(e["event_id"], cats, origin=origin)
        e["timeline"] = _timeline(conn, e["event_id"], raw_tickers, cats)
        # occurred_at_iso 是 LLM 輸出、可為 NULL（判斷不出事件時間）→ date 會是 None，
        # 沒有事件日就沒有「事件前後」的錨點，市場反應圖直接省略
        e["market_reaction"] = (
            _market_reaction(conn, raw_tickers[0], Date.fromisoformat(e["date"]))
            if raw_tickers and e["date"] else None
        )


@router.get("/day")
def events_by_day(request: Request, date: Date = Query(..., description="收盤日 YYYY-MM-DD")):
    """指定日期的全部已評分事件（完整事件流日期選單用），形狀與 bootstrap.events 相同。

    輕量版 hydrate：列表列用不到 timeline / market_reaction（點進詳情頁時
    /demo/events/{id} 會補完整版），跳過它們可省每事件 2 次 DB 來回——
    118 件的日子實測從 ~15 秒降到秒級。
    """
    origin = str(request.base_url).rstrip("/")
    with get_pooled_conn() as conn:
        events = conn.execute(BOOTSTRAP_SQL, {"days": [date], "limit": 300}).fetchall()
    for e in events:
        e.pop("raw_tickers", None)
        e["image"] = pick_image(e["event_id"], e["categories"] or [], origin=origin)
        e["timeline"] = []
        e["market_reaction"] = None
    return {"date": date.isoformat(), "count": len(events), "events": events}


def _build_bootstrap(limit: int, origin: str) -> dict:
    """實際查 DB 組出 bootstrap 回應。由 bootstrap() 經快取呼叫。

    ⚠️ meta.as_of 在 TTL 內會是「快取當下」的時間，最多落後 TTL 秒。可接受——
    該欄位表示資料整理時間，不是請求時間。
    """
    with get_pooled_conn() as conn:
        all_days = [next(iter(r.values())) for r in conn.execute(AVAILABLE_DAYS_SQL).fetchall()]
        days = all_days[:2]      # 首頁載最近兩日（今天/昨天）；其餘由 /demo/day 按需取
        if not days:
            return {"meta": {"edition_date": None,
                             "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
                             "total_events": 0, "total_sources": 0, "available_dates": []},
                    "categories": ["全部"], "events": []}
        edition_day = days[0]
        events = conn.execute(BOOTSTRAP_SQL, {"days": days, "limit": limit}).fetchall()
        _hydrate_events(conn, events, origin)

    # categories 從實際回傳的事件聚合，而不是寫死清單 —— 篩選 chip 不會出現空結果
    seen: list[str] = []
    for e in events:
        for c in e["categories"] or []:
            if c not in seen:
                seen.append(c)

    # 首屏要講的是收斂故事：「全天 N 篇報導 → M 個可驗證事件」。
    # total_sources 因此是收盤日的全天報導量（含被收斂與未達門檻的），不是事件成員數。
    with get_pooled_conn() as conn:
        day_articles = conn.execute(
            "SELECT count(*) AS n FROM articles WHERE published_at::date = %s",
            [edition_day],
        ).fetchone()["n"]

    return {
        "meta": {
            "edition_date": edition_day.isoformat(),
            # events 含「昨天」分頁用的前一收盤日；統計口徑維持「本期＝最新日」
            "total_events": sum(1 for e in events if e["date"] == edition_day.isoformat()),
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
            "total_sources": day_articles,
            "available_dates": [d.isoformat() for d in all_days],
        },
        "categories": ["全部", *seen],
        "events": events,
    }


@router.get("/bootstrap")
def bootstrap(request: Request, limit: int = Query(120, ge=1, le=300, description="整期事件數安全上限")):
    """Demo 專用：一次回傳 meta / categories / events（形狀見 mock_contract_v3）。"""
    # 前端在別的 port，圖片 URL 必須絕對；用請求自身的 base_url 推導，不寫死 host
    origin = str(request.base_url).rstrip("/")
    # origin 必須進快取鍵：圖片 URL 內嵌了它，不同來源不能共用同一份結果
    return _CACHE.get_or_compute((limit, origin), lambda: _build_bootstrap(limit, origin))
