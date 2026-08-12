"""管線總調度：串起 爬蟲→向量化→去重→LLM→評分，靠 articles.status 當交接棒。

兩種模式：
    --db（預設）  讀寫資料庫：articles 進、events 出，每段推進 status
    --offline     純檔案版：讀凍結 JSON、產物寫 jsonl，完全不碰 DB
                  （沒有資料庫時仍可驗證聚類邏輯，也是重構時的比對基準）

status 交接棒：
    pending → vectorized → clustered → summarized → scored
    另有終端狀態 periodic＝被週期性過濾挑掉、不屬於任何事件

用法（在 backend/ 下）：
    python -m backend.pipeline                    # DB 模式，沿用向量快取
    python -m backend.pipeline --offline          # 離線檔案版
    python -m backend.pipeline --llm-limit 5      # DB 模式但 LLM 只處理 5 個事件（試跑省額度）
    python -m backend.pipeline --reembed          # 連向量化一起重跑（BGE-M3，CPU 約 40 分鐘）
"""
import argparse
import json
import os
import sys
from datetime import date

import numpy as np

from backend.clustering import OUT_DIR as CLU_OUT
from backend.clustering.dedup import assemble_events
from backend.embedding.bge_m3 import (
    OUT_DIR as EMB_OUT,
)
from backend.embedding.bge_m3 import (
    build_text,
    embed_articles,
    fetch_pending_articles,
)
from backend.embedding.bge_m3 import (
    save as save_embeddings,
)

# 事件寫回 events 表。ON CONFLICT 讓整段可重跑而不重複建事件。
UPSERT_EVENT_SQL = """
    INSERT INTO events (
        event_id, representative_title, source_count, members,
        related_tickers, embedding, created_at, updated_at
    ) VALUES (%s, %s, %s, %s, %s, %s, now(), now())
    ON CONFLICT (event_id) DO UPDATE SET
        representative_title = EXCLUDED.representative_title,
        source_count         = EXCLUDED.source_count,
        members              = EXCLUDED.members,
        related_tickers      = EXCLUDED.related_tickers,
        embedding            = EXCLUDED.embedding,
        updated_at           = now()
"""

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _to_article(rec: dict, fallback_id=None) -> dict:
    """把爬蟲/JSON 原始紀錄轉成聚類所需的文章 dict。

    id 是 assemble_events 全程組裝的鍵，None 會被 fail fast 拒絕——
    缺 newsId 的紀錄（上游去重刻意保留）由呼叫端給 fallback_id 補唯一 id。
    DB 模式一律有 article_id（PK），不會用到 fallback。
    """
    return {
        "id": rec.get("article_id") or rec.get("newsId") or fallback_id,
        "title": rec.get("title", ""),
        "content": rec.get("content", ""),
        "url": rec.get("url", ""),
        "date": rec.get("date"),
        # DB 版帶 source / related_tickers；離線 JSON 沒有，交給 assemble_events 補預設
        "source": rec.get("source"),
        "related_tickers": rec.get("related_tickers"),
    }


def _vec_literal(v: np.ndarray) -> str:
    """numpy 向量 → pgvector 的文字格式 '[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def event_centroid(event: dict, row_of: dict, vectors: np.ndarray):
    """事件向量＝成員文章向量的質心（正規化）。

    events.embedding 欄位一直存在卻沒有任何程式在填，補上之後
    pgvector 的語意搜尋才有東西可查。
    """
    idx = [row_of[m["article_id"]] for m in event["members"] if m["article_id"] in row_of]
    if not idx:
        return None
    c = vectors[idx].mean(axis=0)
    n = np.linalg.norm(c)
    return (c / n) if n > 0 else c


# ─────────────────────────────────────────────────────────────
# ① 爬蟲由 Prefect 在本程序之前呼叫 crawler；本檔只接 pending 之後
# ─────────────────────────────────────────────────────────────
def stage_crawler() -> None:
    print("[① crawler]  正式入口是 crawler；"
          "Prefect flow 已在呼叫本管線前完成抓取與 Neon ingestion")


def stage_embedding_db() -> tuple[list[dict], np.ndarray]:
    """DB 版：撈 status='vectorized' 的文章，配上 .npy 快取（以 article_id 對齊）。

    對齊刻意用 article_id（articles 的 PK，保證唯一），不用 newsId——
    後者缺值時多筆 None 會互相覆蓋，讓內容與 URL 張冠李戴。
    """
    from backend.db.session import get_conn

    npy_path = os.path.join(EMB_OUT, "embeddings.npy")
    meta_path = os.path.join(EMB_OUT, "meta.jsonl")
    if not (os.path.exists(npy_path) and os.path.exists(meta_path)):
        raise SystemExit("找不到向量快取，請先跑：python -m backend.embedding.bge_m3")

    vectors = np.load(npy_path)
    with open(meta_path, encoding="utf-8") as f:
        meta = [json.loads(line) for line in f if line.strip()]
    if len(meta) != len(vectors):
        raise SystemExit(f"快取不一致：meta {len(meta)} 筆 vs 向量 {len(vectors)} 列")

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT article_id, title, content, url, source, related_tickers, "
            "published_at::date::text AS date FROM articles WHERE status = 'vectorized'"
        ).fetchall()
    by_id = {r["article_id"]: r for r in rows}

    # 依 meta 順序（＝向量列序）取回文章，逐列對齊；對不到的略過不錯置
    articles, keep_rows, missing = [], [], 0
    for i, m in enumerate(meta):
        rec = by_id.get(m.get("article_id") or m.get("newsId"))
        if rec is None:
            missing += 1
            continue
        articles.append(_to_article(dict(rec)))
        keep_rows.append(i)

    # 舊版 bge_m3 在 DB 模式搭配 --limit 時，會把正式交接批次誤寫到
    # out_subset。正式快取完全對不到時，接回該批向量，避免重新跑模型。
    if not articles:
        subset_npy = os.path.join(EMB_OUT + "_subset", "embeddings.npy")
        subset_meta = os.path.join(EMB_OUT + "_subset", "meta.jsonl")
        if os.path.exists(subset_npy) and os.path.exists(subset_meta):
            subset_vectors = np.load(subset_npy)
            with open(subset_meta, encoding="utf-8") as f:
                subset_rows = [json.loads(line) for line in f if line.strip()]
            if len(subset_rows) == len(subset_vectors):
                subset_articles, subset_keep = [], []
                for i, m in enumerate(subset_rows):
                    rec = by_id.get(m.get("article_id") or m.get("newsId"))
                    if rec is not None:
                        subset_articles.append(_to_article(dict(rec)))
                        subset_keep.append(i)
                if subset_articles:
                    print("[② embedding] 正式快取無匹配，改接舊版 --limit 產生的 out_subset")
                    return subset_articles, subset_vectors[subset_keep]

    if not articles:
        raise SystemExit(
            "[② embedding] 沒有 status='vectorized' 的文章。\n"
            "  先確認 articles 表有資料，且已跑過向量化把 status 推進 vectorized。"
        )
    vectors = vectors[keep_rows]
    print(f"[② embedding] 沿用向量快取：{len(articles)} 篇 × {vectors.shape[1]} 維"
          + (f"（快取有 {missing} 篇不在 status='vectorized'，已略過）" if missing else ""))
    return articles, vectors


# ─────────────────────────────────────────────────────────────
# ② 向量化（embedding_Timyo/bge_m3）
# ─────────────────────────────────────────────────────────────
def stage_embedding(reembed: bool, batch: int) -> tuple[list[dict], np.ndarray]:
    """回傳 (articles, vectors)，逐列對應。預設沿用向量快取，不重算。"""
    raw = fetch_pending_articles()                       # 去重後的原始紀錄（含 content/url）
    if not raw:
        raise SystemExit("[② embedding] 沒有待向量化的文章，管線中止（請檢查資料來源）")
    # 缺 newsId 的紀錄（上游刻意保留，值為 None）不進索引：直接取下標會 KeyError，
    # 收進索引則多筆 None 互相覆蓋 → 快取路徑把別篇的 content/url 錯置過來
    raw_by_id = {r["newsId"]: r for r in raw if r.get("newsId") is not None}
    npy_path = os.path.join(EMB_OUT, "embeddings.npy")
    meta_path = os.path.join(EMB_OUT, "meta.jsonl")

    if not reembed and os.path.exists(npy_path) and os.path.exists(meta_path):
        vectors = np.load(npy_path)
        with open(meta_path, encoding="utf-8") as f:
            meta = [json.loads(line) for line in f]
        if len(meta) == len(vectors):
            # 依 meta（= 向量順序）取回 content/url，逐列對齊；
            # 對不回原始紀錄者（newsId 為 None 或資料已變）缺 content/url，只降級不錯置
            articles, orphans = [], 0
            for i, m in enumerate(meta):
                src = raw_by_id.get(m["newsId"])
                if src is None:
                    orphans += 1
                    src = {}
                articles.append(_to_article({**src, **m}, fallback_id=f"noid_{i}"))
            print(f"[② embedding] 沿用向量快取：{len(articles)} 篇 × {vectors.shape[1]} 維"
                  f"（--reembed 可強制重算）")
            if orphans:
                print(f"[② embedding] 警告：{orphans} 篇快取 meta 對不回原始資料"
                      f"（newsId 缺失或來源已變），該些文章缺 content/url → 代號/獨家數字偵測降級")
            if len(meta) != len(raw):
                print(f"[② embedding] 警告：快取 {len(meta)} 篇 ≠ 原始資料 {len(raw)} 篇，"
                      f"新增文章不在本次事件中（--reembed 可重算補齊）")
            return articles, vectors
        print(f"[② embedding] 快取與資料不一致（meta {len(meta)} vs 向量 {len(vectors)}），改為重算")

    print(f"[② embedding] 向量化 {len(raw)} 篇（BGE-M3，CPU 需時較長）...")
    vectors = embed_articles([build_text(r) for r in raw], batch_size=batch)
    save_embeddings(vectors, raw)                        # 更新 embeddings.npy + meta.jsonl
    return [_to_article(r, fallback_id=f"noid_{i}") for i, r in enumerate(raw)], vectors


# ─────────────────────────────────────────────────────────────
# ③ 去重聚類（clustering_Timyo/dedup）
# ─────────────────────────────────────────────────────────────
def stage_clustering(articles: list[dict], vectors: np.ndarray,
                     *, write_db: bool = False) -> list[dict]:
    # 週期過濾 + Agglo/雙閘門 + 事件物件組裝；filtered 是被週期規則丟掉的文章
    events, filtered = assemble_events(articles, vectors)
    multi = sum(1 for e in events if e["source_count"] > 1)
    flagged = sum(1 for e in events for m in e["members"] if m["has_unique_detail"])
    print(f"[③ clustering] {len(articles)} 篇 → {len(events)} 事件"
          f"（多篇 {multi}、單篇 {len(events) - multi}、週期性濾除 {len(filtered)} 篇）"
          f"  has_unique_detail={flagged}")

    os.makedirs(CLU_OUT, exist_ok=True)   # 聚類產物目錄（首次執行或被清掉時自建）

    # 被濾文章不屬於任何事件，若不另行交接，DB 落地後會永遠卡在 'vectorized'
    # 被每輪重撈。先寫出清單（含濾除原因），DB 就緒後改為 status 推進 'periodic'。
    if filtered:
        by_type: dict[str, int] = {}
        for f in filtered:
            by_type[f.periodic_type] = by_type.get(f.periodic_type, 0) + 1
        print("[③ clustering] 週期性濾除明細："
              + "、".join(f"{t} {c} 篇" for t, c in sorted(by_type.items(), key=lambda kv: -kv[1])))
        filtered_path = os.path.join(CLU_OUT, "periodic_filtered.jsonl")
        with open(filtered_path, "w", encoding="utf-8") as f:
            for item in filtered:
                f.write(json.dumps({"article_id": item.article_id,
                                    "periodic_type": item.periodic_type,
                                    "next_status": "periodic"}, ensure_ascii=False) + "\n")
        print(f"[③ clustering] 被濾文章交接清單寫出 → {filtered_path}")

    out_path = os.path.join(CLU_OUT, "event_objects.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"[③ clustering] 事件物件寫出 → {out_path}")

    if not write_db:
        return events

    # ── 寫入 DB：events 表 + articles 交接棒 ──────────────────────
    from backend.clustering.dedup import AGGLO_DISTANCE_THRESHOLD, DATE_GATE_DAYS
    from backend.db.session import get_conn

    row_of = {a["id"]: i for i, a in enumerate(articles)}
    no_vec = 0
    attached: list[tuple[str, bool]] = []   # (目標事件, 目標已 LLM 過?)
    batch_ids = [e["event_id"] for e in events]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for e in events:
                c = event_centroid(e, row_of, vectors)
                if c is None:
                    no_vec += 1
                # ── 增量靠攏（治「批次孤島」）：建新事件前，先看近期既有事件裡
                # 有沒有同一個故事——聚類只看得見本批文章，跨批跟進的報導若不
                # 靠攏，同故事會裂成多個事件（實測 87 組同標題重複、日月光 ×11）。
                # 判準與批內聚類同一套：cosine 距離 ≤ AGGLO_DISTANCE_THRESHOLD
                # 且日期落在 DATE_GATE_DAYS 內。排除本批 id——批內分不分由
                # agglomerative 說了算，靠攏只處理「跨批」。
                target = None
                if c is not None and e.get("date_start"):
                    cand = cur.execute(
                        """select e2.event_id, (e2.embedding <=> %s::vector) as dist,
                                  (e2.title is not null) as summarized, e2.members,
                                  (select max(coalesce(a.published_at, a.fetched_at))::date
                                   from articles a where a.event_id = e2.event_id) as last_seen
                           from events e2
                           where e2.embedding is not null
                             and e2.updated_at > now() - interval '30 days'
                             and e2.event_id != all(%s)
                           order by e2.embedding <=> %s::vector
                           limit 1""",
                        (_vec_literal(c), batch_ids, _vec_literal(c)),
                    ).fetchone()
                    if (cand and cand["dist"] is not None
                            and float(cand["dist"]) <= AGGLO_DISTANCE_THRESHOLD
                            and cand["last_seen"] is not None):
                        gap = abs((date.fromisoformat(e["date_start"]) - cand["last_seen"]).days)
                        if gap <= DATE_GATE_DAYS:
                            target = cand
                if target is not None:
                    old_ids = {m.get("article_id") for m in (target["members"] or [])}
                    new_members = [m for m in e["members"] if m["article_id"] not in old_ids]
                    if new_members:   # 空＝重跑同批，已併過，冪等跳過
                        mjson = json.dumps(new_members, ensure_ascii=False)
                        cur.execute(
                            """update events set
                                 members = members || %s::jsonb,
                                 source_count = (select count(distinct m->>'source')
                                                 from jsonb_array_elements(members || %s::jsonb) m),
                                 updated_at = now()
                               where event_id = %s""",
                            (mjson, mjson, target["event_id"]))
                        # 目標已 LLM 過 → 新成員直接標 summarized（避免 run_db_step 只憑
                        # 新文章的殘缺視角重寫摘要）；摘要沿用、廣度/熱度由重評更新
                        cur.execute(
                            "UPDATE articles SET event_id=%s, status=%s, updated_at=now() "
                            "WHERE article_id = ANY(%s) AND status='vectorized'",
                            (target["event_id"],
                             "summarized" if target["summarized"] else "clustered",
                             [m["article_id"] for m in new_members]))
                        attached.append((target["event_id"], target["summarized"]))
                    continue   # 不建新事件
                cur.execute(UPSERT_EVENT_SQL, (
                    e["event_id"],
                    e["representative_title"],
                    e["source_count"],
                    json.dumps(e["members"], ensure_ascii=False),
                    json.dumps(e["related_tickers"], ensure_ascii=False),
                    _vec_literal(c) if c is not None else None,
                ))
            # 回填 article.event_id 並推進交接棒（只動還在 vectorized 的，可重跑）
            for e in events:
                cur.execute(
                    "UPDATE articles SET event_id=%s, status='clustered', updated_at=now() "
                    "WHERE article_id = ANY(%s) AND status='vectorized'",
                    (e["event_id"], [m["article_id"] for m in e["members"]]),
                )
            # 被週期過濾的文章推進終端狀態，否則會永遠卡在 vectorized 被每輪重撈
            if filtered:
                cur.execute(
                    "UPDATE articles SET status='periodic', updated_at=now() "
                    "WHERE article_id = ANY(%s) AND status='vectorized'",
                    ([f.article_id for f in filtered],),
                )
        # 靠攏後 source_count/熱度變了 → 已 LLM 過的目標事件重評重要性
        rescored = 0
        for eid in {t for t, summarized in attached if summarized}:
            from backend.scoring.importance import score_and_update_importance
            score_and_update_importance(conn, eid)
            rescored += 1
        conn.commit()
    print(f"[③ clustering] 已寫入 DB：{len(events) - len(attached)} 事件 UPSERT、"
          f"{len(attached)} 個 cluster 靠攏至既有事件（重評星等 {rescored}）、"
          f"成員文章推進 clustered、{len(filtered)} 篇推進 periodic"
          + (f"（{no_vec} 個事件無向量）" if no_vec else ""))
    return events


# ─────────────────────────────────────────────────────────────
# ④ LLM 摘要（llm_MMJSUN）/ ⑤ 評分（scoring_Bright）—— TODO
# ─────────────────────────────────────────────────────────────
def stage_llm(limit: int | None = None, prescreen: bool = True) -> None:
    """④ 從 status='clustered' 接手，產摘要/分類/方向，推進 summarized。

    需要 LLM 金鑰；--llm-limit 可小量試跑避免燒額度。

    prescreen=True（預設）：先過篩選層（scoring_Bright/prescreen），只送
    「來源數>=2 或 含官方來源」的事件——實測 4★/5★ 召回 100%、LLM 呼叫量
    收斂到約 24%。未選中的事件維持 clustered，之後來源數上升會自動入選。
    --no-prescreen 可關閉（全量行為，同舊版）。
    """
    from backend.llm.summarize import run_db_step

    selected: list[str] | None = None
    if prescreen:
        from backend.db.session import get_conn
        from backend.scoring.prescreen import expire_stale_events, select_events_for_llm

        with get_conn() as conn:
            selected = select_events_for_llm(conn)
            # TTL 回收（P3-02）：超過 7 天仍未達門檻的 clustered 事件標 deferred，
            # 否則 backlog 無限累積，篩選層每輪都重掃一次死資料。
            # ⚠️ 必須排除本輪選中的事件——8/10 沒排除，1,105 個選中事件的文章
            # 被 TTL 先一步踢成 deferred，LLM 段整輪空轉（見 prescreen docstring）。
            expired = expire_stale_events(conn, exclude_event_ids=selected)
            conn.commit()
        print(f"[④ llm]      篩選層：選中 {len(selected)} 個事件送 LLM"
              + (f"；TTL 回收 {expired} 篇" if expired else ""))

    ok, fail = run_db_step(limit=limit, event_ids=selected)
    print(f"[④ llm]      完成：成功 {ok}、失敗 {fail}"
          + ("（失敗者維持 clustered，下輪自動重試）" if fail else ""))


def stage_scoring(event_ids: list[str] | None = None) -> None:
    """⑤ 重要性評分 + 市場驗證，最後把 articles 推進 scored。

    articles.status 的最後一棒原本沒有任何程式在推——分數寫得進 events，
    但文章永遠停在 summarized，狀態機收不了尾。
    """
    from backend.db.session import get_conn
    from backend.scoring.importance import score_and_update_importance

    # score_and_update_importance 是「單一事件」的入口，批次由這裡負責迴圈
    with get_conn() as conn:
        if event_ids is None:
            rows = conn.execute(
                "SELECT event_id FROM events "
                "WHERE title IS NOT NULL AND importance_stars IS NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT event_id FROM events "
                "WHERE title IS NOT NULL AND importance_stars IS NULL "
                "AND event_id = ANY(%s)",
                (event_ids,),
            ).fetchall()
        ids = [r["event_id"] for r in rows]

        ok = fail = 0
        stars_hist: dict[int, int] = {}
        for eid in ids:
            try:
                res = score_and_update_importance(conn, eid)
                ok += 1
                stars_hist[res.stars] = stars_hist.get(res.stars, 0) + 1
            except Exception as exc:
                fail += 1
                if fail <= 3:
                    print(f"    ✗ {eid}: {exc}")
        conn.commit()

    dist = "、".join(f"★{s}×{stars_hist[s]}" for s in sorted(stars_hist, reverse=True))
    print(f"[⑤ scoring]  重要性評分：成功 {ok}、失敗 {fail}" + (f"  分布 {dist}" if dist else ""))

    # 市場驗證需要 chip_data；沒有籌碼資料時略過，不擋管線
    with get_conn() as conn:
        has_chip = conn.execute("SELECT count(*) AS c FROM chip_data").fetchone()["c"]
    if not has_chip:
        print("[⑤ scoring]  chip_data 為空 → 略過市場驗證（market_validation 維持 null＝觀察中）")
    else:
        # ⚠️ 2026-08-12 修：市場驗證重評自排程從 Prefect flows 遷到 run_daily 後
        # 成了孤兒——七段管線沒有任何一段呼叫 rescore_pending_events，結果事件
        # 永遠停在「觀察中」、驗證分凍結在初評、未 verified 事件永駐籌碼需求池
        # （8/2 整批 observing、demand 膨脹到 1,400 檔都是這個洞）。
        # 掛回 ⑤ 尾端：④ 剛抓完當日籌碼，正是 D+1/3/5 演進重評的時機。
        #
        # 用獨立 autocommit 連線：存量首輪要重評數千件、跑數十分鐘，單一大交易
        # 中途斷線會整包回滾（同 8/11 夜 summarize 423 件連環滅的病）。逐件即提交，
        # 斷了也保留已完成的，明晚自動續作剩餘。try/except 隔離：重評失敗不准
        # 連累後面的交接棒收尾。
        import psycopg as _psycopg

        from backend.db.session import db_url
        from backend.finmind.jobs import rescore_pending_events

        try:
            with _psycopg.connect(db_url(), autocommit=True,
                                  options="-c timezone=Asia/Taipei") as conn:
                done, skipped = rescore_pending_events(conn)
            print(f"[⑤ scoring]  市場驗證重評：完成 {done} 件、略過 {skipped} 件（無個股/非普通股/無方向）")
        except Exception as exc:
            print(f"[⑤ scoring]  ⚠ 市場驗證重評中斷：{exc}（已完成部分保留，明晚續作）")

    with get_conn() as conn:
        with conn.cursor() as cur:
            if event_ids is None:
                cur.execute(
                    "UPDATE articles SET status='scored', updated_at=now() "
                    "WHERE status='summarized' AND event_id IN "
                    "(SELECT event_id FROM events WHERE importance_stars IS NOT NULL)"
                )
            else:
                cur.execute(
                    "UPDATE articles SET status='scored', updated_at=now() "
                    "WHERE status='summarized' AND event_id = ANY(%s) "
                    "AND event_id IN "
                    "(SELECT event_id FROM events WHERE importance_stars IS NOT NULL)",
                    (event_ids,),
                )
            moved = cur.rowcount
        conn.commit()
    print(f"[⑤ scoring]  交接棒收尾：{moved} 篇文章推進 scored")


def _describe_target() -> str:
    """印出這次要寫入哪個資料庫（不顯示帳密）。

    管線接通後，跑一次就會寫入 events 與 articles.status——.env 若指向雲端
    生產庫，那就是直接改生產資料。啟動時明確顯示目標，避免誤寫。
    """
    from urllib.parse import urlparse

    from backend.db.session import db_url

    u = urlparse(db_url())
    host = u.hostname or "?"
    where = "本機" if host in ("localhost", "127.0.0.1", "::1") else f"⚠️ 遠端（{host}）"
    return f"{where}  {host}:{u.port or 5432}/{(u.path or '').lstrip('/')}"


def main() -> None:
    ap = argparse.ArgumentParser(description="事件訊號管線總調度（預設走資料庫）")
    ap.add_argument("--offline", action="store_true",
                    help="離線檔案版：讀凍結 JSON、產物寫 jsonl，完全不碰 DB")
    ap.add_argument("--reembed", action="store_true", help="強制重跑向量化（否則沿用快取）")
    ap.add_argument("--batch", type=int, default=16, help="向量化 batch size")
    ap.add_argument("--llm-limit", type=int, default=None,
                    help="LLM 段只處理前 N 個事件（試跑省額度）")
    ap.add_argument("--skip-llm", action="store_true", help="跳過 LLM 與評分（只跑到聚類）")
    ap.add_argument("--stages", default="all",
                    help="只跑指定階段，逗號分隔：embedding,clustering,llm,scoring。"
                         "各段靠 articles.status 交接，所以能單獨重跑——"
                         "例如已聚類完只想補跑摘要：--stages llm,scoring")
    ap.add_argument("--no-prescreen", action="store_true",
                    help="關閉篩選層（關卡1 進場濾網＋關卡2 LLM 配額），回到全量行為")
    args = ap.parse_args()

    print("=== 管線開始 ===")
    stage_crawler()

    if args.offline:
        articles, vectors = stage_embedding(args.reembed, args.batch)
        stage_clustering(articles, vectors, write_db=False)
        print("=== 管線結束（離線模式，未寫 DB）===")
        return

    print(f"[DB] 寫入目標：{_describe_target()}")

    all_stages = ("embedding", "clustering", "llm", "scoring")
    if args.stages != "all":
        want = tuple(s.strip() for s in args.stages.split(",") if s.strip())
        unknown = [s for s in want if s not in all_stages]
        if unknown:
            raise SystemExit(f"未知階段 {unknown}，可用：{', '.join(all_stages)}")
    elif args.skip_llm:
        want = ("embedding", "clustering")
    else:
        want = all_stages

    # 篩選層關卡 1：pending 先擋週期性/過短，省掉它們的 embedding 成本。
    # 放在 embedding 之前；bge_m3 只會處理存活的 pending。
    if "embedding" in want and not args.no_prescreen:
        from backend.db.session import get_conn
        from backend.scoring.prescreen import apply_prescreen_pending

        with get_conn() as conn:
            apply_prescreen_pending(conn)
            conn.commit()

    # embedding 與 clustering 綁在一起——後者要吃前者回傳的向量，不能單獨跑
    batch_event_ids: list[str] | None = None
    if "clustering" in want or "embedding" in want:
        articles, vectors = stage_embedding_db()
        if "clustering" in want:
            batch_events = stage_clustering(articles, vectors, write_db=True)
            batch_event_ids = [event["event_id"] for event in batch_events]
    if "llm" in want:
        llm_limit = args.llm_limit
        if llm_limit is None and args.no_prescreen and batch_event_ids is not None:
            # 舊行為（僅 --no-prescreen 時保留）：以本批事件數當 limit。
            # 注意舊落差：run_db_step 取的是「全域」前 N 個 clustered 事件，
            # 未必是本批次的。篩選層（預設開）改用名單制，已無此問題。
            llm_limit = len(batch_event_ids)
        stage_llm(llm_limit, prescreen=not args.no_prescreen)
    if "scoring" in want:
        # ⚠️ 一律全域收尾，不傳本批 ids（P1-04）。
        # LLM 段走篩選層時處理的是「全域」backlog（含前幾批的 clustered 事件），
        # scoring 若只評本批，那些跨批被摘要的事件會永遠停在 summarized、
        # API 也不顯示。stage_scoring(None) 撈的是「已摘要未評分」全集，
        # 天生冪等，多跑只是空轉，不會重複計分。
        stage_scoring(None)
    print("=== 管線結束 ===")


if __name__ == "__main__":
    main()
