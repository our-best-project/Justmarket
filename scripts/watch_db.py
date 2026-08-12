"""監控 Neon 資料庫的寫入狀況。

用法（在 / 或任何目錄下）：
    python scripts/watch_db.py              # 每 5 秒刷新一次
    python scripts/watch_db.py -i 10        # 每 10 秒
    python scripts/watch_db.py -t articles events   # 只看指定表
    python scripts/watch_db.py --once       # 只查一次就結束
    python scripts/watch_db.py --status     # 改看 articles.status 分佈（管線推進進度）

預設模式看的是「筆數 + 最後寫入時間」，適合盯爬蟲有沒有在灌新資料。
管線各段（向量化/聚類/摘要/評分）不會新增文章、也不改 created_at，
只把 status 往前推——盯那個要用 --status。

連線資訊讀 .env 的 DATABASE_URL。Ctrl+C 結束。
"""

import argparse
import os
import re
import sys
import time
from datetime import UTC, datetime

import psycopg

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")

# 表 -> 用來判斷「最後寫入時間」的欄位
TIME_COL = {
    "articles": "created_at",
    "events": "created_at",
    "chip_data": "updated_at",
    "tickers": "updated_at",
    "market_index_daily": "updated_at",
    "market_indices": "updated_at",
}


def load_url() -> str:
    with open(ENV_PATH, encoding="utf-8") as f:
        m = re.search(r"^DATABASE_URL=(.+)$", f.read(), re.M)
    if not m:
        sys.exit(f"找不到 DATABASE_URL：{ENV_PATH}")
    return m.group(1).strip().strip('"').strip("'")


def snapshot(conn, tables):
    """回傳 {table: (筆數, 最後寫入時間)}"""
    parts = []
    for t in tables:
        col = TIME_COL.get(t)
        latest = f"max({col})::text" if col else "null"
        parts.append(f"select '{t}' as t, count(*)::bigint as n, {latest} as ts from {t}")
    sql = " union all ".join(parts) + " order by 1"
    return {t: (n, ts) for t, n, ts in conn.execute(sql)}


def fmt_age(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    sec = (datetime.now(UTC) - dt).total_seconds()
    if sec < 90:
        return f"{sec:.0f} 秒前"
    if sec < 5400:
        return f"{sec / 60:.0f} 分前"
    if sec < 172800:
        return f"{sec / 3600:.1f} 小時前"
    return f"{sec / 86400:.1f} 天前"


# articles.status 的交接棒順序（run.py 的狀態機），照這個順序顯示才看得出推進方向
STATUS_ORDER = ["pending", "vectorized", "clustered", "summarized", "scored", "periodic"]


def snapshot_status(conn):
    """回傳 {status: (筆數, 該狀態最後被推進的時間)}"""
    rows = conn.execute(
        "select status, count(*)::bigint, max(updated_at)::text "
        "from articles group by 1"
    ).fetchall()
    return {s: (n, ts) for s, n, ts in rows}


def render_status(cur, prev):
    os.system("cls" if os.name == "nt" else "clear")
    total = sum(n for n, _ in cur.values())
    print(f"articles.status 推進監控  {datetime.now():%H:%M:%S}   共 {total:,} 篇   (Ctrl+C 結束)\n")
    print(f"{'status':<14}{'筆數':>10}{'本輪增量':>12}   最後推進")
    print("-" * 60)
    # 已知狀態照交接棒順序排，未知狀態（將來新增的）補在後面
    keys = [s for s in STATUS_ORDER if s in cur] + [s for s in cur if s not in STATUS_ORDER]
    for s in keys:
        n, ts = cur[s]
        delta = ""
        if prev and s in prev:
            d = n - prev[s][0]
            delta = f"+{d}" if d > 0 else ("" if d == 0 else str(d))
        elif prev:
            delta = f"+{n}"          # 這輪才出現的新狀態
        print(f"{s:<14}{n:>10,}{delta:>12}   {fmt_age(ts)}")


def render(cur, prev, tables):
    os.system("cls" if os.name == "nt" else "clear")
    print(f"Neon 寫入監控  {datetime.now():%H:%M:%S}   (Ctrl+C 結束)\n")
    print(f"{'表':<20}{'筆數':>12}{'本輪增量':>12}   最後寫入")
    print("-" * 66)
    for t in tables:
        n, ts = cur[t]
        delta = ""
        if prev and t in prev:
            d = n - prev[t][0]
            delta = f"+{d}" if d > 0 else ("" if d == 0 else str(d))
        print(f"{t:<20}{n:>12,}{delta:>12}   {fmt_age(ts)}")


def main():
    p = argparse.ArgumentParser(description="監控 Neon 資料寫入")
    p.add_argument("-i", "--interval", type=float, default=5, help="刷新間隔秒數（預設 5）")
    p.add_argument("-t", "--tables", nargs="+", help="只監控指定的表")
    p.add_argument("--once", action="store_true", help="只查一次就結束")
    p.add_argument("--status", action="store_true",
                   help="改看 articles.status 分佈（管線推進進度）")
    args = p.parse_args()

    url = load_url()
    with psycopg.connect(url) as conn:
        if args.status:
            prev = None
            while True:
                cur = snapshot_status(conn)
                render_status(cur, prev)
                prev = cur
                if args.once:
                    return
                time.sleep(args.interval)

        tables = args.tables or [
            r[0] for r in conn.execute(
                "select tablename from pg_tables where schemaname='public' order by 1"
            )
        ]
        prev = None
        while True:
            cur = snapshot(conn, tables)
            render(cur, prev, tables)
            prev = cur
            if args.once:
                return
            time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n停止監控。")
