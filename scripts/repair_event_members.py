"""修復 P1-01 造成的 members 脫節（一次性資料修復）。

【怎麼壞的】舊版 event_id 是「每批從 01 重數的流水號」，跨批次撞號後被
run.py 的 ON CONFLICT DO UPDATE 以 EXCLUDED.members 整包覆寫——事件實際掛載
的文章（articles.event_id）沒有變，但 events.members 名單被換成了後一批的。
實測 3,908 個事件受損，其中 2,813 個已有 LLM 摘要。

【修復原則】articles.event_id 是**權威來源**（兩條寫入路徑都會正確維護它），
members 只是它的快照。所以修復＝用 articles 重建 members：
  - 已在 members 裡的成員：原條目保留（has_unique_detail 是聚類時算的，重建不出來）
  - 掛在事件上但不在 members 的文章：補進去，has_unique_detail=False
  - source_count 同步重算為「不同來源數」（P2-07 的統一語意）

【不動什麼】不動 LLM 摘要。2,813 個摘要是舊名單寫的，重寫要燒 LLM 額度；
補上名單後 source_count 上升的事件會被 stage_scoring 的重評接住（星等用新
數字算），摘要文字的補寫由品誠的段落另行決定。

用法：
    python repair_event_members.py           # 影子模式，只印不寫
    python repair_event_members.py --apply   # 實際寫入
"""
import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def _load_env() -> str:
    for folder in [Path.cwd(), *Path.cwd().parents]:
        f = folder / ".env"
        if f.is_file():
            for line in open(f, encoding="utf-8", errors="replace"):
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return "".join(line.split("=", 1)[1].split()).strip("\"'")
    raise SystemExit("找不到 DATABASE_URL")


FIND_SQL = """
select e.event_id, e.members
from events e
where (select count(*) from articles a where a.event_id = e.event_id)
    > (select count(*) from jsonb_array_elements(coalesce(e.members,'[]'::jsonb)))
"""

ARTICLES_SQL = """
select article_id, source, title, url
from articles where event_id = %s
"""

UPDATE_SQL = """
update events set
  members = %s::jsonb,
  source_count = (select count(distinct m->>'source')
                  from jsonb_array_elements(%s::jsonb) m),
  updated_at = now()
where event_id = %s
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="實際寫入（預設影子模式）")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    with psycopg.connect(_load_env(), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(FIND_SQL)
        damaged = cur.fetchall()
        print(f"members 與實際掛載脫節的事件：{len(damaged)}")

        fixed = added_total = 0
        for row in damaged:
            cur.execute(ARTICLES_SQL, (row["event_id"],))
            actual = cur.fetchall()
            old = {m.get("article_id"): m for m in (row["members"] or [])}
            rebuilt, added = [], 0
            for a in actual:
                if a["article_id"] in old:
                    rebuilt.append(old[a["article_id"]])
                else:
                    added += 1
                    rebuilt.append({
                        "article_id": a["article_id"],
                        "source": a["source"],
                        "title": a["title"] or "",
                        "url": a["url"] or "",
                        "has_unique_detail": False,
                        # 標記這筆是修復補進來的，之後查案有跡可循
                        "restored_by": "repair_event_members_20260808",
                    })
            if not added:
                continue
            fixed += 1
            added_total += added
            if fixed <= 5:
                print(f"  {row['event_id']}: members {len(old)} → {len(rebuilt)}（補 {added}）")
            if args.apply:
                mjson = json.dumps(rebuilt, ensure_ascii=False)
                cur.execute(UPDATE_SQL, (mjson, mjson, row["event_id"]))
        if args.apply:
            conn.commit()
            print(f"✅ 已修復 {fixed} 個事件、補回 {added_total} 筆成員")
            # 驗證：脫節數應歸零
            cur.execute(FIND_SQL)
            print(f"驗證：剩餘脫節事件 = {len(cur.fetchall())}")
        else:
            print(f"（影子模式）將修復 {fixed} 個事件、補回 {added_total} 筆成員；"
                  f"加 --apply 實際寫入")


if __name__ == "__main__":
    main()
