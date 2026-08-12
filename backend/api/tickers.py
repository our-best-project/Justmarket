"""代號端點：GET /tickers/search、/tickers/{ticker}/events（04_API_v2 故事 D）

T19：把 T04 的 mock 資料換成真的查 DB。簽名與回傳形狀不變。

⚠️ 路由順序：`/search` 必須放在 `/{ticker}/events` 前面，否則 "search" 會被
   當成 ticker 吃掉（保留原檔的註記）。
"""
import json

from fastapi import APIRouter, Query

from backend.db.session import get_pooled_conn
from backend.schemas.event import EventSummary, TickerHint

router = APIRouter(prefix="/tickers", tags=["tickers"])

RENDERABLE = "title IS NOT NULL AND categories IS NOT NULL AND importance_stars IS NOT NULL"

SUMMARY_COLS = """
    event_id, title, source_count, related_tickers,
    occurred_at_iso, occurred_at_text, categories,
    importance_stars AS stars,
    market_validation,
    status,
    COALESCE(verify_state, 'observing') AS verify_state
"""


@router.get("/search", response_model=list[TickerHint])
def search_tickers(q: str, limit: int = Query(10, ge=1, le=50)):
    """故事 D 搜尋自動完成：?q=台積 → [{ticker:"2330", name:"台積電"}]。

    照 03_SD §1.4 的搜尋條件：`name ILIKE '%q%' OR ticker LIKE 'q%'`。
    排序：代號前綴命中優先（打「2330」應該先看到 2330 本身），其次依代號。
    """
    if not q.strip():
        return []
    with get_pooled_conn() as conn:
        return conn.execute(
            """
            SELECT ticker, name FROM tickers
            WHERE name ILIKE %s OR ticker LIKE %s
            ORDER BY (ticker LIKE %s) DESC, ticker
            LIMIT %s
            """,
            [f"%{q}%", f"{q}%", f"{q}%", limit],
        ).fetchall()


@router.get("/{ticker}/events", response_model=list[EventSummary])
def ticker_events(ticker: str, range: str = "today",
                  limit: int = Query(20, ge=1, le=100),
                  offset: int = Query(0, ge=0, le=10_000)):
    """故事 D 公司事件頁：搜「台積電」→ 今天 3 件事（不是 23 篇新聞）。

    查無資料回 [] + 200（契約 §1），前端顯示「今天尚無達標事件」。

    `range`：契約只寫 `?range=today`。這裡支援 today / week / month / all，
    預設 today —— 凍結樣本沒有「今天」的資料，所以 demo 時用 `?range=all`。
    """
    windows = {"today": "AND occurred_at_iso::date = CURRENT_DATE",
               "week": "AND occurred_at_iso >= CURRENT_DATE - INTERVAL '7 days'",
               "month": "AND occurred_at_iso >= CURRENT_DATE - INTERVAL '30 days'",
               "all": ""}
    window = windows.get(range, windows["today"])

    with get_pooled_conn() as conn:
        return conn.execute(
            f"""
            SELECT {SUMMARY_COLS} FROM events
            WHERE {RENDERABLE} AND related_tickers @> %s::jsonb {window}
            ORDER BY importance_stars DESC, market_validation DESC NULLS LAST,
                     occurred_at_iso DESC
            LIMIT %s OFFSET %s
            """,
            [json.dumps([ticker]), limit, offset],
        ).fetchall()
