"""一次性修復：聚類段兩類存量髒資料（2026-08-02 健檢實測）。

A. 跨批次分裂的同標題重複事件（批次孤島造成，87 組 194 個）：
   同 representative_title 的事件群，若「全部成員的發布日跨度 <= 日期閘門(3天)」
   → 併入最早的一個（優先保留已 LLM 過的），其餘刪除。
   跨度超過閘門的不併（可能是重複發布的制式公告或真不同事件，交日期閘門的判斷）。

B. 連續行情欄目滾成的巨型事件（《油價》79 篇跨 46 天等）：
   成員標題 >=80% 命中 dedup 新增的「海外/商品行情欄」規則 → 整個事件解散，
   文章標 periodic（與 live 管線新行為一致），事件刪除。

用法（從 repo 根目錄 執行）：
    python ../scripts/repair_cluster_dups.py            # dry-run
    python ../scripts/repair_cluster_dups.py --apply
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re

import psycopg

# 與 dedup._PERIODIC_OVERRIDE 的「海外/商品行情欄」同一份 pattern（該表為模組私有，
# 這裡取用其正則字串；規則變動請兩邊同步）
_COLUMN_RE = re.compile(
    r"^《(歐股|美股|美債|油價|農產品|台股盤後|日股|日股早盤|貴金屬|金屬"
    r"|貴金屬/金屬|日韓股|韓股|港股|台股|匯市|債市|強勢美股特報|美股主題週報)》")
GATE_DAYS = 3
COLUMN_RATIO = 0.8


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

    # ── B 先做（欄目事件也可能同標題，先解散避免 A 白併）─────────────
    # 逐事件往返 16k 次會逾時 → 單一 SQL 讓 Neon 端算欄目比
    pg_re = _COLUMN_RE.pattern
    dissolved = 0
    hits = conn.execute("""
        select e.event_id, e.representative_title, s.cnt, s.hit
        from events e
        join lateral (
            select count(*) cnt,
                   count(*) filter (where a.title ~ %s) hit
            from articles a where a.event_id = e.event_id) s on true
        where s.cnt > 0 and s.hit::float / s.cnt >= %s""",
        (pg_re, COLUMN_RATIO)).fetchall()
    for eid, title, cnt, hit in hits:
        dissolved += 1
        print(f"  [B 解散] {str(title)[:30]}（{cnt} 篇，欄目比 {hit}/{cnt}）")
        if args.apply:
            conn.execute("update articles set event_id=null, status='periodic', "
                         "updated_at=now() where event_id=%s", (eid,))
            conn.execute("delete from events where event_id=%s", (eid,))

    # ── A 同標題群合併 ────────────────────────────────────────────
    merged_groups = merged_events = 0
    groups = conn.execute("""
        select representative_title, array_agg(event_id order by event_id)
        from events group by 1 having count(*)>1""").fetchall()
    for title, eids in groups:
        span, = conn.execute("""
            select extract(day from max(coalesce(published_at,fetched_at))
                          - min(coalesce(published_at,fetched_at)))
            from articles where event_id = any(%s)""", (eids,)).fetchone()
        if span is None or span > GATE_DAYS:
            continue
        titled = [r[0] for r in conn.execute(
            "select event_id from events where event_id=any(%s) and title is not null "
            "order by event_id", (eids,)).fetchall()]
        keeper = titled[0] if titled else eids[0]
        losers = [e for e in eids if e != keeper]
        merged_groups += 1
        merged_events += len(losers)
        print(f"  [A 合併] {str(title)[:32]}  {len(eids)} 個 → 保留 {keeper}")
        if not args.apply:
            continue
        conn.execute("update articles set event_id=%s, updated_at=now() "
                     "where event_id=any(%s)", (keeper, losers))
        # keeper.members 重建 = 其名下全部文章；source_count 重數
        conn.execute("""
            update events e set
              members = (select coalesce(jsonb_agg(jsonb_build_object(
                            'article_id', a.article_id, 'source', a.source,
                            'title', a.title, 'url', a.url,
                            'has_unique_detail', false)), '[]'::jsonb)
                         from articles a where a.event_id = e.event_id),
              source_count = (select count(distinct a.source)
                              from articles a where a.event_id = e.event_id),
              updated_at = now()
            where e.event_id = %s""", (keeper,))
        conn.execute("delete from events where event_id=any(%s)", (losers,))

    if args.apply:
        conn.commit()
        # 合併後廣度變了 → 有 title 的 keeper 重評重要性
        from backend.scoring.importance import score_and_update_importance
        n = 0
        for r in conn.execute("select event_id from events where title is not null "
                              "and updated_at > now() - interval '15 minutes'").fetchall():
            score_and_update_importance(conn, r[0]); n += 1
        conn.commit()
        print(f"\n完成：B 解散 {dissolved} 個欄目事件｜A 合併 {merged_groups} 組"
              f"（消滅 {merged_events} 個重複事件）｜重評星等 {n}")
    else:
        print(f"\ndry-run：B 可解散 {dissolved}｜A 可合併 {merged_groups} 組"
              f"（消滅 {merged_events} 個）。加 --apply 寫入")
    conn.close()


if __name__ == "__main__":
    main()
