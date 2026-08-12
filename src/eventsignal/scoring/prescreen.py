"""篩選層：決定「哪些文章值得進管線、哪些事件值得花一次 LLM 呼叫」。

【為什麼要有這層】
爬蟲擴到 6 家來源後產能暴增（2026-08-01 實測：每小時 1,437 篇、24 小時 22,178 篇），
遠超下游消化能力——待 LLM 的事件累積到 5,277 個，而 LLM 段有 3 秒節流＋免費層
RPM 上限，全跑要 4.4 小時且成本可觀。本模組把 LLM 呼叫量收斂到約 1/4。

【兩道關卡，依成本由低到高擺放】
  關卡 1 prescreen_article()  article 級、純規則、零成本 → 擺在**向量化之前**
      現況浪費：dedup 的週期性過濾雖已濾掉 5,405 篇，卻發生在向量化「之後」，
      等於先付了 embedding 成本才丟掉。判定沿用同一份純函式，只是提前呼叫。
  關卡 2 select_events_for_llm()  event 級、擺在分群之後 / LLM 之前 ★核心省錢處

【關卡 2 的規則怎麼來的——用 1,656 筆已知星等事件回測，不是拍腦袋】
    來源數>=2                 送 16.9%｜5★ 召回 100%｜4★ 召回  84%
    預篩分數前 280 名          送 16.9%｜5★ 召回  93%｜4★ 召回  84%
    >=2 家 或 含官方來源 ←採用  送 24.4%｜5★ 召回 100%｜4★ 召回 100%

  兩個反直覺、決定了設計走向的實測發現：
  1. **不能用「有無 ticker」當條件**。score_impact 把無個股代號者視為總經/政策層級
     給滿分，用 ticker 過濾等於精準砍掉影響力最大的那批（實測誤殺 2 個 5★）。
  2. **單一來源也可能是 4★**。被漏掉的高星等事件全是 MOPS／Fed 官方公告，
     靠「權威 100 × 影響 100」得分——故補上 source_type='official' 這條必送規則。

【未達門檻的事件不會消失】
維持 status='clustered'，只是這輪不送 LLM。若之後有其他媒體跟進、source_count
上升，下一輪就自動符合條件被撈進來——自我修正，不需額外機制。成本只是一次
SQL 掃描，不產生 LLM 呼叫。超過 TTL 仍未達標才進終態 'deferred'（見 migration
2026-08-01_articles_status_prescreen.sql）。

用法：
    python -m eventsignal.scoring.prescreen --dry-run     # 影子模式：只印決策不寫 DB
    python -m eventsignal.scoring.prescreen --backtest    # 對已知星等事件回測召回率
"""
from __future__ import annotations

# ---------------- 參數外置（與 importance.CONFIG 同慣例） ----------------
CONFIG = {
    # 關卡 1
    "min_content_chars": 100,      # 內文短於此 → 不值得向量化（實測 176 篇）
    # 關卡 2
    # ⚠️ 兩個門檻並存，缺一不可（P2-07 修正的配套）：
    # source_count 語意統一為「不同來源數」後，只用它當門檻會讓必送率從 27.1%
    # 掉到 3.7%、漏掉 1,659 個 4★+ 事件（實測 production 16,651 事件）。
    # 原本 100% 召回的回測是在「篇數」語意下做的，所以篇數門檻必須保留——
    # 兩個條件取聯集，召回不變，同時讓評分引擎拿到正確的來源數。
    "min_sources": 2,              # 幾家「獨立來源」就必送
    "min_articles": 2,             # 或幾「篇」報導就必送（保回測召回率）
    "official_source_types": ("official",),   # 這些 source_type 單篇也必送
    # 關卡 3
    "ttl_days": 7,                 # 逾期未達門檻 → 'deferred'
    # 預算（None = 不設上限，靠必送規則自然收斂到約 24%）
    "daily_llm_budget": None,
}


# ---------------- 關卡 1：進場濾網（article 級，純函式） ----------------
def prescreen_article(title: str | None, content: str | None) -> tuple[bool, str | None]:
    """單篇文章要不要進管線。回 (保留?, 淘汰理由)。

    純函式、零外部相依（periodic_type 已確認只 import re/collections，不碰 DB/模型），
    故可安全地在向量化前逐篇呼叫。

    誠實標註：這裡用的是**單篇版** periodic_type（純關鍵字），抓不到批次版
    periodic_types 的第四層「資料驅動模板」（同模板跨 >=4 天，需整批才能算）。
    實測對既有 5,405 筆 periodic 的覆蓋率 75.2%——本關卡先省掉大宗 embedding
    成本，漏網的由 dedup 聚類段的批次層接手（分層防禦，同一套規則來源）。
    """
    from eventsignal.clustering.dedup import periodic_type

    t = (title or "").strip()
    if not t:
        return False, "無標題"

    ptype = periodic_type(t)
    if ptype is not None:
        return False, f"週期性欄目（{ptype}）"

    if len((content or "").strip()) < CONFIG["min_content_chars"]:
        return False, f"內文過短（<{CONFIG['min_content_chars']} 字）"

    return True, None


def apply_prescreen_pending(conn, dry_run: bool = False) -> tuple[int, int]:
    """對 status='pending' 的文章套用關卡 1，淘汰者標 'filtered'。回 (保留數, 淘汰數)。

    擺在向量化之前呼叫——被擋下的文章不會付 embedding 成本
    （dedup 段的週期過濾仍在，作為聚類前的第二道保險；同一份純函式，判定一致）。
    """
    from psycopg.rows import tuple_row

    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT article_id, title, content FROM articles WHERE status='pending'")
        rows = cur.fetchall()

    rejected: list[str] = []
    reasons: dict[str, int] = {}
    for aid, title, content in rows:
        keep, why = prescreen_article(title, content)
        if not keep:
            rejected.append(aid)
            reasons[why] = reasons.get(why, 0) + 1

    if rejected and not dry_run:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET status='filtered', updated_at=now() "
                "WHERE article_id = ANY(%s) AND status='pending'",
                (rejected,),
            )
    if reasons:
        detail = "、".join(f"{k}×{v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1]))
        print(f"[篩選層·關卡1] pending {len(rows)} 篇 → 淘汰 {len(rejected)}（{detail}）"
              + ("｜dry-run 未寫入" if dry_run else ""))
    return len(rows) - len(rejected), len(rejected)


# ---------------- 關卡 2：LLM 配額分配（event 級） ----------------
def pre_llm_score(source_count: int, members: list[dict] | None,
                  related_tickers: list[str] | None,
                  heat_sources_24h: int | float | None = None) -> float:
    """LLM 之前就算得出來的重要性分數（0–100），用於超出預算時排序。

    直接呼叫既有的 score_importance，**不另立一套啟發式**——四維中的
    廣度(30%)＋權威(25%)＋影響(30%) 共 85% 權重不需 LLM，只有類型(15%) 需要。

    ⚠️ 這是最終分數的**下界**：categories=None 會讓 score_impact 的總經(100)/
       產業(75) 升級路徑失效、類型維度固定在 default 50。所以它只可用來
       「排序決定誰先送」，不可用來做「低於某分就不送」的上切——那是必送規則的職責。
    """
    from eventsignal.scoring.importance import score_importance

    return score_importance(
        source_count, members or [], None,          # status=None → 走純 members 字串的權威分級
        related_tickers or [], None,                # categories=None → 類型維度取 default
        heat_sources_24h=heat_sources_24h,
    ).total


# 必送條件：來源數達標 **或** 任一成員文章來自官方（MOPS／TWSE／Fed…）
# 兩者取聯集是實測 4-5★ 零誤殺的關鍵——缺任一條都會漏掉高星等事件。
#
# official 必送的例外——MOPS 制式公告黑名單（2026-08-02 實測）：
# 「代子公司公告」316 個、「財報董事會召開日期」222 個、「受邀參加法說」94 個
# 事件全是單來源官方樣板文，命中 official 必送等於白花 ~630 次 LLM 呼叫，
# 而它們的資訊量固定（星等實測全在 2-3★）。黑名單只擋「單來源＋標題是樣板」：
# 若有第二家媒體跟進（source_count>=2），照樣入選——重要的樣板公告不會漏。
_BOILERPLATE_TITLE_RE = (
    r"(受邀參加|應邀參加).*(法人說明會|法說)"
    r"|董事會召開日期"
    r"|代子公司.*公告"
    r"|公告本公司.*(更名|名稱變更)"
)
_MUST_SEND_SQL = """
SELECT e.event_id, e.source_count, e.members, e.related_tickers
FROM events e
WHERE e.title IS NULL                                  -- 尚未經 LLM
  AND EXISTS (SELECT 1 FROM articles a
              WHERE a.event_id = e.event_id AND a.status = 'clustered')
  AND (
        COALESCE(e.source_count, 0) >= %s
     OR jsonb_array_length(COALESCE(e.members, '[]'::jsonb)) >= %s
     OR (EXISTS (SELECT 1 FROM articles a
                 WHERE a.event_id = e.event_id AND a.source_type = ANY(%s))
         AND e.representative_title !~ %s)
  )
"""


def select_events_for_llm(conn, budget: int | None = None) -> list[str]:
    """挑出這輪值得送 LLM 的 event_id。budget=None 用 CONFIG 的預設。

    conn 必須是 psycopg 連線（本模組與 importance.fetch_heat_sources 都是 psycopg v3；
    summarize.run_db_step 持有的是 SQLAlchemy Connection，不可直接傳進來——
    正確做法是由 run.py 呼叫本函式後把 id 清單傳給 summarize）。
    """
    from psycopg.rows import tuple_row

    budget = budget if budget is not None else CONFIG["daily_llm_budget"]

    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(_MUST_SEND_SQL,
                    (CONFIG["min_sources"], CONFIG["min_articles"],
                     list(CONFIG["official_source_types"]),
                     _BOILERPLATE_TITLE_RE))
        rows = cur.fetchall()

    if budget is None or len(rows) <= budget:
        return [r[0] for r in rows]

    # 超出預算才需要排序：用預篩分數決定誰先送（下界分數，僅供排序）
    from eventsignal.scoring.importance import fetch_heat_sources

    ranked = sorted(
        rows,
        key=lambda r: pre_llm_score(r[1] or 1, r[2], r[3], fetch_heat_sources(conn, r[0])),
        reverse=True,
    )
    return [r[0] for r in ranked[:budget]]


# ---------------- 關卡 3：TTL 回收 ----------------
def expire_stale_events(conn, ttl_days: int | None = None,
                        exclude_event_ids: list[str] | None = None) -> int:
    """逾期仍未達門檻的 clustered 文章 → 'deferred'，避免無限累積。回傳筆數。

    exclude_event_ids：本輪已被 select_events_for_llm 選中的事件，**必須排除**。
    ⚠️ 2026-08-11 事故：早期版本沒有這個參數，docstring 宣稱「達標事件不受影響」
    但 SQL 只看「未 LLM 過＋逾 7 天」——backlog 裡達標的老事件照樣被踢。
    實測 8/10 排程：篩選層選中 1,105 個事件，隨後 TTL 把它們的文章一併標成
    deferred，run_db_step 一個都撈不到，整輪 LLM 段空轉（成功 0、失敗 0）。
    """
    ttl = ttl_days if ttl_days is not None else CONFIG["ttl_days"]
    exclude = list(exclude_event_ids or [])
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE articles a SET status = 'deferred', updated_at = now()
               WHERE a.status = 'clustered'
                 AND a.event_id IS NOT NULL
                 AND a.event_id != ALL(%s)
                 AND COALESCE(a.published_at, a.fetched_at) < now() - make_interval(days => %s)
                 AND EXISTS (SELECT 1 FROM events e
                             WHERE e.event_id = a.event_id AND e.title IS NULL)""",
            (exclude, ttl),
        )
        return cur.rowcount


# ---------------- CLI（影子模式 / 回測） ----------------
def _connect():
    from eventsignal.db.session import get_conn
    return get_conn()


def _dry_run() -> None:
    """影子模式：只印決策，不寫 DB。"""
    from psycopg.rows import tuple_row

    with _connect() as conn:
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("""SELECT count(*) FROM events e WHERE e.title IS NULL
                           AND EXISTS (SELECT 1 FROM articles a
                                       WHERE a.event_id = e.event_id AND a.status='clustered')""")
            pending = cur.fetchone()[0]

        chosen = select_events_for_llm(conn)
        print(f"待 LLM 事件 {pending} 個 → 本輪選中 {len(chosen)} 個"
              f"（{len(chosen) / pending * 100:.1f}%）" if pending else "沒有待處理事件")

        if not chosen:
            return
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("""SELECT representative_title, source_count FROM events
                           WHERE event_id = ANY(%s) ORDER BY source_count DESC NULLS LAST
                           LIMIT 8""", (chosen[:400],))
            print("\n選中的（來源數最高的 8 個）:")
            for title, sc in cur.fetchall():
                print(f"   {sc:>3} 篇  {(title or '')[:38]}")
            cur.execute("""SELECT representative_title, source_count FROM events e
                           WHERE e.title IS NULL AND NOT (e.event_id = ANY(%s))
                             AND EXISTS (SELECT 1 FROM articles a
                                         WHERE a.event_id=e.event_id AND a.status='clustered')
                           LIMIT 8""", (chosen,))
            print("\n被略過的（抽樣 8 個，人工檢查是否確實不重要）:")
            for title, sc in cur.fetchall():
                print(f"   {sc:>3} 篇  {(title or '')[:38]}")


def _backtest() -> None:
    """對已有星等的事件回測必送規則的召回率（驗收基準：4★/5★ 皆 100%）。"""
    from psycopg.rows import tuple_row

    with _connect() as conn, conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT e.importance_stars,
                          COALESCE(e.source_count,0) >= %s
                       OR EXISTS (SELECT 1 FROM articles a
                                  WHERE a.event_id = e.event_id AND a.source_type = ANY(%s))
                   FROM events e WHERE e.importance_stars IS NOT NULL""",
            (CONFIG["min_sources"], list(CONFIG["official_source_types"])),
        )
        rows = cur.fetchall()

    total = len(rows)
    sent = sum(1 for _, keep in rows if keep)
    print(f"回測 {total} 筆已知星等事件｜必送規則選中 {sent} ({sent / total * 100:.1f}%)\n")
    ok = True
    for star in (5, 4, 3, 2, 1):
        grp = [k for s, k in rows if s == star]
        if not grp:
            continue
        recall = sum(grp) / len(grp) * 100
        flag = ""
        if star >= 4 and recall < 100:
            flag, ok = "  ⚠️ 高星等誤殺", False
        print(f"   {star}★  召回 {sum(grp):>4}/{len(grp):<4} {recall:>5.1f}%{flag}")
    print("\n驗收:", "通過（4★/5★ 零誤殺）" if ok else "未通過——門檻需重新檢視")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="篩選層：進場濾網 + LLM 配額分配")
    ap.add_argument("--dry-run", action="store_true", help="影子模式：只印決策不寫 DB")
    ap.add_argument("--backtest", action="store_true", help="對已知星等事件回測召回率")
    ap.add_argument("--expire", action="store_true", help="執行 TTL 回收（會寫 DB）")
    ap.add_argument("--filter-pending", action="store_true",
                    help="對 pending 文章套用關卡 1（向量化前先擋週期性/過短，會寫 DB）")
    args = ap.parse_args()

    if args.filter_pending:
        with _connect() as conn:
            kept, rej = apply_prescreen_pending(conn)
            conn.commit()
            print(f"保留 {kept}、淘汰 {rej}")
    elif args.backtest:
        _backtest()
    elif args.expire:
        with _connect() as conn:
            n = expire_stale_events(conn)
            conn.commit()
            print(f"TTL 回收：{n} 篇 clustered → deferred")
    else:
        _dry_run()


if __name__ == "__main__":
    main()
