# 2026-08-08 自 _handoff/crawler/ 救援至此（repo 唯一副本）。
# 用途：把凍結語料 data/cnyes_news_2026.json 灌入 articles 表，供離線重建管線。
"""橋 2：凍結新聞樣本 → articles 表（status='pending'）。

【為什麼需要這支】管線第一棒斷了：crawler_Arku 只會產 JSON（Anue.py），
沒有任何 production 程式會 INSERT INTO articles。下游 bge_m3.py 本該讀
articles WHERE status='pending'，但它目前直接讀 JSON 檔。
這支暫時代勞「爬蟲寫進 DB」那一步，讓管線後半段有東西可吃。

【資料來源】data/cnyes_news_2026.json（5409 篇，鉅亨 Anue）
【冪等性】ON CONFLICT (article_id) DO NOTHING —— 重跑不會覆蓋已被管線推進的
  status（若用 DO UPDATE 會把已 clustered 的文章打回 pending，毀掉交接棒）。

【欄位對映】
  article_id      cnyes_<newsId>   （DDL 註解：來源縮寫 + 原站ID）
  source          鉅亨網
  source_type     media
  stance_flag     neutral          （財經媒體報導，非分析師個股推薦）
  language        zh-TW
  content_scope   full             ⚠️ 見下方「已知牴觸」
  related_tickers crawler_Arku.base.extract_related_tickers（三層策略）
  published_at    publishAt（Unix）→ TIMESTAMPTZ
  status          pending
  event_id        NULL             （去重段回填）

【⚠️ 已知牴觸——決策 D2，明知故犯】
  03_create_articles.sql:73-74 註解訂「media 類僅存導言/摘要，不存全文；
  官方=full，媒體=summary_only」；crawler_Arku/README.md 第一站亦同。
  但本次決策保留全文（實測 content 中位數 739 字），故 content_scope 誠實填 'full'
  ——寧可欄位如實描述內容，也不要存全文卻標 summary_only（那是在資料裡說謊）。
  要守界線：改用 media.py --scope summary_only 重抓，並把此處改為 'summary_only'。

用法：
    python _handoff/crawler/seed_articles.py --dry-run   # 只看統計
    python _handoff/crawler/seed_articles.py --limit 100  # 先灌 100 篇試
    python _handoff/crawler/seed_articles.py              # 全量
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from eventsignal.crawler_legacy.base import (
    extract_related_tickers,
    load_stoplist,
    load_ticker_index,
)
from eventsignal.db.session import db_url

REPO_ROOT = Path(__file__).resolve().parents[1]
NEWS = REPO_ROOT / "data" / "cnyes_news_2026.json"

SOURCE = "鉅亨網"
SOURCE_TYPE = "media"
STANCE_FLAG = "neutral"
LANGUAGE = "zh-TW"
CONTENT_SCOPE = "full"      # 決策 D2；守界線時改 'summary_only'

INSERT_SQL = """
    INSERT INTO articles (
        article_id, event_id, source, source_type, stance_flag, language,
        content_scope, title, content, url, related_tickers,
        published_at, status
    ) VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
    ON CONFLICT (article_id) DO NOTHING
"""


def to_dt(publish_at):
    """publishAt（Unix 秒）→ aware datetime。缺值回 None（published_at 允許 null）。"""
    if not publish_at:
        return None
    try:
        return datetime.fromtimestamp(int(publish_at), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def retag() -> None:
    """重算既有文章的 related_tickers，並同步 events.related_tickers。

    為什麼需要：related_tickers 的抽法從「標題+內文」改成「只用標題」
    （「關於」vs「提到」）。既有 4486 篇要重標。

    不碰 status、不新增文章 —— 純粹修正一個欄位。
    events.related_tickers 重算為「成員文章 related_tickers 的聯集」，
    與 dedup.assemble_events 的組裝邏輯一致。
    """
    from collections import Counter

    with psycopg.connect(db_url()) as conn:
        tickers = conn.execute("SELECT ticker, name FROM tickers").fetchall()
        index = load_ticker_index(tickers, load_stoplist())
        arts = conn.execute("SELECT article_id, title, related_tickers FROM articles").fetchall()

        before, after, changed = Counter(), Counter(), 0
        updates = []
        for aid, title, old in arts:
            new = extract_related_tickers(title or "", index)
            before[len(old or [])] += 1
            after[len(new)] += 1
            if (old or []) != new:
                changed += 1
                updates.append((json.dumps(new, ensure_ascii=False), aid))

        print(f"文章 {len(arts)} 篇，related_tickers 有變動 {changed} 篇")
        print(f"  改前：>4家 {sum(v for k,v in before.items() if k>4)} 篇、最多 {max(before)} 家")
        print(f"  改後：>4家 {sum(v for k,v in after.items() if k>4)} 篇、最多 {max(after)} 家")

        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE articles SET related_tickers=%s, updated_at=now() WHERE article_id=%s",
                updates,
            )
            # events.related_tickers ＝ 成員文章的聯集（同 assemble_events 的邏輯）
            cur.execute("""
                UPDATE events e SET
                    related_tickers = COALESCE(agg.tks, '[]'::jsonb),
                    updated_at = now()
                FROM (
                    SELECT e2.event_id,
                           jsonb_agg(DISTINCT t ORDER BY t) FILTER (WHERE t IS NOT NULL) AS tks
                    FROM events e2
                    JOIN articles a ON a.event_id = e2.event_id
                    LEFT JOIN LATERAL jsonb_array_elements_text(a.related_tickers) t ON true
                    GROUP BY e2.event_id
                ) agg
                WHERE e.event_id = agg.event_id
            """)
            ev_changed = cur.rowcount
        conn.commit()

        ev = conn.execute("""
            SELECT count(*) FILTER (WHERE jsonb_array_length(related_tickers) > 4) AS 雜訊,
                   max(jsonb_array_length(related_tickers)) AS 最多,
                   count(*) FILTER (WHERE jsonb_array_length(related_tickers) = 0) AS 空的,
                   count(*) AS 總數
            FROM events
        """).fetchone()

    print(f"\n✅ 完成：articles 更新 {len(updates)} 筆、events 重算 {ev_changed} 筆")
    print(f"   events related_tickers：>4家 {ev[0]} 筆、最多 {ev[1]} 家、"
          f"空的 {ev[2]} 筆 / 共 {ev[3]} 筆")


def main() -> None:
    ap = argparse.ArgumentParser(description="凍結新聞樣本 → articles 表")
    ap.add_argument("--json", default=str(NEWS))
    ap.add_argument("--limit", type=int, default=None, help="只處理前 N 篇")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retag", action="store_true",
                    help="只重算既有文章的 related_tickers（不新增、不動 status），"
                         "並同步更新 events.related_tickers（＝成員文章的聯集）")
    args = ap.parse_args()

    if args.retag:
        retag()
        return

    news = json.loads(Path(args.json).read_text(encoding="utf-8"))
    if args.limit:
        news = news[: args.limit]

    with psycopg.connect(db_url()) as conn:
        tickers = conn.execute("SELECT ticker, name FROM tickers").fetchall()
        if not tickers:
            raise SystemExit("tickers 表是空的——請先跑 scripts/load_tickers.py")
        index = load_ticker_index(tickers, load_stoplist())

        rows, skipped, tag_hist = [], 0, {}
        for a in news:
            nid = a.get("newsId")
            title = (a.get("title") or "").strip()
            url = (a.get("url") or "").strip()
            if not nid or not title or not url:
                skipped += 1          # 三個 NOT NULL 欄位缺一就跳過，不硬塞
                continue
            content = a.get("content") or ""
            # ⚠️ 只傳標題：related_tickers 是「事件關於誰」不是「內文提到誰」。
            #    傳 title+content 會讓「查台積電回傳聯發科」。
            #    實測：只用標題 → 雜訊 >4家 13.3%→0.1%、單篇最多 58→5 家。
            tks = extract_related_tickers(title, index)
            tag_hist[len(tks)] = tag_hist.get(len(tks), 0) + 1
            rows.append((
                f"cnyes_{nid}", SOURCE, SOURCE_TYPE, STANCE_FLAG, LANGUAGE,
                CONTENT_SCOPE, title, content, url,
                json.dumps(tks, ensure_ascii=False), to_dt(a.get("publishAt")),
            ))

        print(f"來源: {Path(args.json).name}")
        print(f"  讀入 {len(news)} 篇 / 可用 {len(rows)} / 跳過(缺必填) {skipped}")
        print(f"  ticker 索引: 可用名 {len(index['names'])}、停用 {len(index['excluded']['stoplist'])}、"
              f"同名歧義 {len(index['excluded']['ambiguous'])}")
        tagged = sum(v for k, v in tag_hist.items() if k > 0)
        print(f"  有標到 ticker: {tagged}/{len(rows)} ({tagged/len(rows):.1%})" if rows else "")

        if args.dry_run:
            print("\n[dry-run] 未寫入。前 3 筆：")
            for r in rows[:3]:
                print(f"  {r[0]} | {r[6][:30]}… | tickers={r[9]}")
            return

        with conn.cursor() as cur:
            cur.executemany(INSERT_SQL, rows)
        conn.commit()

        total = conn.execute("SELECT count(*) FROM articles").fetchone()[0]
        pending = conn.execute("SELECT count(*) FROM articles WHERE status='pending'").fetchone()[0]
        with_tk = conn.execute(
            "SELECT count(*) FROM articles WHERE jsonb_array_length(related_tickers) > 0"
        ).fetchone()[0]

    print("\n✅ 寫入完成")
    print(f"   articles 總筆數: {total}（status='pending': {pending}）")
    print(f"   有 related_tickers: {with_tk} ({with_tk/total:.1%})")


if __name__ == "__main__":
    main()
