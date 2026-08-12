"""一次性修復：清除既有事件 related_tickers 裡「成員文章原文未提及」的幻覺代號。

背景（2026-08-02 實測）：LLM 標註有三種失控——供應鏈腦補（可口可樂事件標
5009 榮剛/1802 台玻，5 篇原文零提及）、數字區間展開（一筆標 601 檔連號）、
大盤文標一串權值股。live 路徑已在 summarize._verify_tickers 加了交叉驗證，
本腳本補救「防線上線前」已寫入的存量事件。

判準與 live 防線一致：代號字面 或 tickers.name 公司名出現在任一成員文章的
標題/內文即保留；否則剔除。

連帶修正（代號集合有變動的事件才做）：
  - industries 重算（查表 ∪ 不可考的 LLM 部分無法重建 → 以查表結果為準補集）
  - importance 重新評分（影響範圍維度吃 ticker 數，2 檔→0 檔分數會變）
  - market_validation 歸零重驗（原分數可能算在幻覺股票的籌碼上）——
    重設 verify_state='observing'，交給下一輪 rescore_pending_events

用法（從 repo 根目錄 執行）：
    python -m scripts.repair_event_tickers            # 預設 dry-run，只列不改
    python -m scripts.repair_event_tickers --apply    # 實際寫入
（scripts 不在 backend 底下時：python ../scripts/repair_event_tickers.py）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib

import psycopg

from eventsignal.llm.summarize import _ticker_mentioned


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    for folder in [pathlib.Path.cwd(), *pathlib.Path.cwd().parents]:
        env = folder / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL 未設定")


def main() -> None:
    ap = argparse.ArgumentParser(description="清除事件 related_tickers 的幻覺代號")
    ap.add_argument("--apply", action="store_true", help="實際寫入（預設 dry-run）")
    args = ap.parse_args()

    conn = psycopg.connect(_db_url(), connect_timeout=30)
    events = conn.execute(
        """select e.event_id, e.title, e.related_tickers
           from events e
           where e.title is not null
             and jsonb_array_length(coalesce(e.related_tickers,'[]'::jsonb)) > 0"""
    ).fetchall()
    name_of = dict(conn.execute("select ticker, name from tickers").fetchall())
    print(f"待檢查事件 {len(events)} 個（dry-run={not args.apply}）")

    changed = 0
    total_dropped = 0
    for eid, title, tickers in events:
        rows = conn.execute(
            "select coalesce(title,''), coalesce(content,'') from articles where event_id=%s",
            (eid,),
        ).fetchall()
        corpus = "\n".join(f"{t}\n{c}" for t, c in rows)
        if not corpus:
            continue  # 成員文章遺失（不應發生）→ 無從驗證，跳過不動
        kept = [t for t in tickers if _ticker_mentioned(t, name_of.get(t), corpus)]
        dropped = [t for t in tickers if t not in kept]
        if not dropped:
            continue
        changed += 1
        total_dropped += len(dropped)
        show = f"{dropped[:6]}…共{len(dropped)}檔" if len(dropped) > 6 else str(dropped)
        print(f"  {eid}  {title[:24]}  剔除 {show}  保留 {kept}")
        if not args.apply:
            continue

        conn.execute(
            """update events set
                 related_tickers = %s::jsonb,
                 industries = coalesce((
                   select jsonb_agg(distinct t.industry order by t.industry)
                   from tickers t
                   where t.ticker in (select jsonb_array_elements_text(%s::jsonb))
                     and t.industry is not null), '[]'::jsonb),
                 market_validation = null, validation_breakdown = null,
                 chip_evidence = null, verify_state = 'observing',
                 updated_at = now()
               where event_id = %s""",
            (json.dumps(kept), json.dumps(kept), eid),
        )

    if args.apply:
        conn.commit()
        # 代號數變了 → 影響範圍維度變 → 重評重要性（星等可能升降）
        from eventsignal.scoring.importance import score_and_update_importance
        conn2 = psycopg.connect(_db_url(), connect_timeout=30)
        rescored = 0
        for eid, _, _ in events:
            row = conn2.execute(
                "select updated_at > now() - interval '10 minutes' from events where event_id=%s",
                (eid,)).fetchone()
            if row and row[0]:
                score_and_update_importance(conn2, eid)
                rescored += 1
        conn2.commit()
        conn2.close()
        print(f"\n完成：{changed} 個事件更新、共剔除 {total_dropped} 檔、重評星等 {rescored} 個")
        print("market_validation 已歸零為 observing，下一輪 rescore_pending_events 會重驗")
    else:
        print(f"\ndry-run：{changed} 個事件有幻覺代號、共 {total_dropped} 檔待剔除。"
              f"加 --apply 實際寫入")
    conn.close()


if __name__ == "__main__":
    main()
