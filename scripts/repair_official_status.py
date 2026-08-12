"""一次性修復：把「成員無官方來源卻標 official_confirmed」的存量事件降級。

背景（LLM段健檢報告 §B）：prompt 寫明「媒體轉述——即使逐字引述——不算官方確認」，
但 flash-lite 級模型系統性忽略，實測 69% 的 official_confirmed 成員清一色媒體。
品誠已在 summarize 加 enforce_official_guard 純函式擋住新事件，本腳本補救存量。

判準：直接呼叫 summarize.enforce_official_guard——與正式管線、regression 跑器
共用同一份邏輯，不另寫規則（避免三邊漂移）。

連帶：status 是重要性評分「權威維度」的輸入（official_confirmed 走 Route A
100/85/72，非 official 走來源分級），降級後必須重評星等。

用法（從 repo 根目錄 執行）：
    python ../scripts/repair_official_status.py            # dry-run
    python ../scripts/repair_official_status.py --apply
"""
from __future__ import annotations

import argparse
import os
import pathlib

import psycopg

from eventsignal.llm.summarize import enforce_official_guard


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = psycopg.connect(_db_url(), connect_timeout=30)

    rows = conn.execute("""
        select e.event_id, e.title, e.status,
               coalesce((select array_agg(distinct a.source_type)
                         from articles a where a.event_id = e.event_id), '{}') types
        from events e
        where e.title is not null and e.status = 'official_confirmed'
        order by e.event_id""").fetchall()
    print(f"official_confirmed 事件 {len(rows)} 個（dry-run={not args.apply}）")

    demoted = []
    for eid, title, status, types in rows:
        fields = {"status": status}
        articles = [{"source_type": t} for t in (types or [])]
        if enforce_official_guard(fields, articles):        # 與 live 同一份判準
            demoted.append((eid, title, fields["status"]))

    for eid, title, new in demoted[:10]:
        print(f"   [降級→{new}] {str(title)[:34]}")
    if len(demoted) > 10:
        print(f"   …（其餘 {len(demoted) - 10} 筆略）")

    if not args.apply:
        print(f"\ndry-run：{len(demoted)} 個事件待降級。加 --apply 寫入")
        conn.close()
        return

    conn.execute("update events set status='developing', updated_at=now() "
                 "where event_id = any(%s)", ([e for e, _, _ in demoted],))
    conn.commit()

    # status 是權威維度的輸入 → 重評星等
    from eventsignal.scoring.importance import score_and_update_importance
    n = 0
    for eid, _, _ in demoted:
        score_and_update_importance(conn, eid)
        n += 1
    conn.commit()
    print(f"\n完成：降級 {len(demoted)} 個事件、重評星等 {n} 個")
    conn.close()


if __name__ == "__main__":
    main()
