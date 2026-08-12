"""事件端點：GET /events/today、/events/{id}、/events/{id}/timeline（04_API_v2 故事 A/B/C/E）

T19：把 T04 的 mock 資料換成真的查 DB。簽名與回傳形狀不變（照原檔 docstring 的規劃）。

【契約對照】
- `importance_stars`(DB) → `stars`(API)：04_API_v2 §3 明列的改名
- `members`(DB) → `source_links`(API)：投影成 url 字串陣列（§3）
- 排序：重要性降序，同星等比 `market_validation`（§5 慣例）
- 空狀態：查無資料回 `[]` + HTTP 200（非 404）（§1 慣例）
- 查無此 id：回 **404**（原 mock 版會 fallback 回第一筆假資料，舊檔 :144 自己註明待修）

【verify_state 的 COALESCE 說明】
`EventSummary` 把 verify_state 定為必填，但 DDL 允許 null，且**只有 market_validation.py
會寫它**（需要 chip_data）。沒有籌碼資料時 DB 全是 null → Pydantic 會 ValidationError。
這裡 `COALESCE(verify_state, 'observing')`：契約附錄 §I 定義 observing＝「D0、資料未齊、
分數尚未成立 → market_validation 回 null → 前端顯示『驗證中』」，正是我們的狀態，
所以這個預設在語意上正確、不是掩蓋。
（根治：DDL 加 `DEFAULT 'observing'`，或 schema 改 `verify_state: VerifyState = observing`。）

【只回「可渲染」的事件】EventSummary 必填 title/categories/stars 等 LLM 與評分段欄位，
WHERE 會濾掉還沒跑到那步的事件——那不是錯誤，是還沒輪到。
"""
import json
from datetime import date as Date

from fastapi import APIRouter, HTTPException, Query

from backend.db.session import get_pooled_conn
from backend.schemas.event import EventDetail, EventSummary, Timeline

router = APIRouter(prefix="/events", tags=["events"])

# EventSummary 需要的欄位；未跑完 LLM/評分的事件缺這些，不可渲染
RENDERABLE = "title IS NOT NULL AND categories IS NOT NULL AND importance_stars IS NOT NULL"

SUMMARY_COLS = """
    event_id, title, source_count, related_tickers,
    occurred_at_iso, occurred_at_text, categories,
    importance_stars AS stars,
    market_validation,
    status,
    COALESCE(verify_state, 'observing') AS verify_state
"""

# 同星等比 market_validation（§5）；NULLS LAST 否則沒籌碼資料的會被壓到底
ORDER_IMPORTANCE = ("ORDER BY importance_stars DESC, market_validation DESC NULLS LAST, "
                    "occurred_at_iso DESC")


@router.get("/today", response_model=list[EventSummary])
def events_today(
    limit: int = Query(5, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    category: str | None = None,
    sort: str = "importance",
    date: Date | None = Query(
        None,
        description="⚠️ 非契約欄位，僅供 demo：指定日期（預設今天）。"
                    "凍結樣本沒有『今天』的資料，不帶此參數會正確地回 []。",
    ),
):
    """故事 A：今日 5 大事件；故事 E：`?category=政策` 篩選。"""
    target = date or Date.today()
    sql = f"SELECT {SUMMARY_COLS} FROM events WHERE {RENDERABLE} AND occurred_at_iso::date = %s"
    params: list = [target]
    if category:
        sql += " AND categories @> %s::jsonb"
        # json.dumps 而非手拼 f'["{x}"]'——名稱含引號/反斜線時手拼會產生
        # 非法 JSON，jsonb cast 直接 500（P3-01）
        params.append(json.dumps([category], ensure_ascii=False))
    sql += f" {ORDER_IMPORTANCE} LIMIT %s OFFSET %s"
    params += [limit, offset]

    with get_pooled_conn() as conn:
        return conn.execute(sql, params).fetchall()


@router.get("/{event_id}", response_model=EventDetail)
def event_detail(event_id: str):
    """故事 B：事件詳情。查無此 id → 404（不再 fallback 回假資料）。"""
    sql = f"""
        SELECT {SUMMARY_COLS},
               summary, expected_direction, direction_confidence, confidence_note,
               COALESCE(importance_reasons, '[]'::jsonb) AS importance_reasons,
               validation_breakdown,
               COALESCE(chip_evidence, '[]'::jsonb)      AS chip_evidence,
               -- members → source_links：只投影 url（§3）
               COALESCE(
                   (SELECT jsonb_agg(m->>'url') FROM jsonb_array_elements(members) m),
                   '[]'::jsonb
               ) AS source_links
        FROM events WHERE event_id = %s
    """
    with get_pooled_conn() as conn:
        row = conn.execute(sql, [event_id]).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    if row["title"] is None or row["stars"] is None:
        # 事件存在但還沒跑完 LLM／評分 —— 不是查無此 id，是還沒就緒
        raise HTTPException(status_code=404,
                            detail=f"event {event_id} not ready (LLM/scoring pending)")
    return row


@router.get("/{event_id}/timeline", response_model=Timeline)
def event_timeline(event_id: str):
    """故事 C：事件時間線（MVP 弱版）。

    契約 SD §2.6：**同公司同類別依日期排列，不宣稱因果**。
    照字面實作：取該事件的第一個 ticker，撈「同 ticker 且 category 有交集」的事件依日期排。
    """
    with get_pooled_conn() as conn:
        base = conn.execute(
            "SELECT event_id, related_tickers, categories FROM events WHERE event_id = %s",
            [event_id],
        ).fetchone()
        if base is None:
            raise HTTPException(status_code=404, detail=f"event {event_id} not found")

        tickers = base["related_tickers"] or []
        cats = base["categories"] or []
        if not tickers or not cats:
            return {"event_id": event_id, "timeline": []}

        nodes = conn.execute(
            f"""
            SELECT occurred_at_iso::date AS date, title, event_id,
                   (event_id = %s) AS is_current
            FROM events
            WHERE {RENDERABLE}
              AND related_tickers @> %s::jsonb
              AND EXISTS (
                  SELECT 1 FROM jsonb_array_elements_text(categories) c
                  WHERE c = ANY(%s)
              )
            ORDER BY occurred_at_iso
            """,
            [event_id, json.dumps([tickers[0]], ensure_ascii=False), cats],
        ).fetchall()
    return {"event_id": event_id, "timeline": nodes}
