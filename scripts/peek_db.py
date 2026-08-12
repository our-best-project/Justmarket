"""直接看 Neon 某張表的內容。

用法：
    python scripts/peek_db.py                       # 列出所有表 + 筆數
    python scripts/peek_db.py articles              # articles 最新 10 筆
    python scripts/peek_db.py articles -n 30        # 最新 30 筆
    python scripts/peek_db.py articles -c id title source   # 只看指定欄位
    python scripts/peek_db.py articles -w "source='cnyes'"  # 加篩選條件
    python scripts/peek_db.py articles --schema     # 只看欄位結構
    python scripts/peek_db.py articles -n 1 -x      # 直式顯示，看單筆全文
    python scripts/peek_db.py -q "select source, count(*) from articles group by 1"

連線資訊讀 .env 的 DATABASE_URL。
"""

import argparse
import os
import re
import sys
import unicodedata

import psycopg

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")

# 表 -> 預設排序欄位（由新到舊）
ORDER_COL = {
    "articles": "created_at",
    "events": "created_at",
    "chip_data": "date",
    "tickers": "updated_at",
    "market_index_daily": "date",
    "market_indices": "updated_at",
}


def load_url() -> str:
    with open(ENV_PATH, encoding="utf-8") as f:
        m = re.search(r"^DATABASE_URL=(.+)$", f.read(), re.M)
    if not m:
        sys.exit(f"找不到 DATABASE_URL：{ENV_PATH}")
    return m.group(1).strip().strip('"').strip("'")


def width(s: str) -> int:
    """終端顯示寬度（中文字算 2 格）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, n: int) -> str:
    return s + " " * max(0, n - width(s))


def clip(s: str, n: int) -> str:
    """截斷到 n 格寬。省略號用 ASCII 的 '..'——'…' 在多數終端算 2 格，會弄歪對齊。"""
    if width(s) <= n:
        return s
    out, w = "", 0
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if w + cw > n - 2:
            return out + ".." + " " * (n - 2 - w)
        out, w = out + c, w + cw
    return out


def cell(v, maxw: int) -> str:
    s = "" if v is None else str(v).replace("\n", " ").replace("\t", " ")
    return clip(s, maxw) if maxw else s


def print_table(cols, rows, maxw):
    body = [[cell(v, maxw) for v in r] for r in rows]
    widths = [max(width(c), *(width(r[i]) for r in body)) if body else width(c)
              for i, c in enumerate(cols)]
    print("  ".join(pad(c, w) for c, w in zip(cols, widths)))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for r in body:
        print("  ".join(pad(v, w) for v, w in zip(r, widths)))


def print_vertical(cols, rows):
    w = max(width(c) for c in cols)
    for i, r in enumerate(rows, 1):
        print(f"--- [{i}] " + "-" * 50)
        for c, v in zip(cols, r):
            print(f"{pad(c, w)} | {'' if v is None else v}")
        print()


def main():
    p = argparse.ArgumentParser(description="看 Neon 某張表的內容")
    p.add_argument("table", nargs="?", help="表名；省略則列出所有表")
    p.add_argument("-n", "--limit", type=int, default=10, help="筆數（預設 10）")
    p.add_argument("-c", "--columns", nargs="+", help="只顯示這些欄位")
    p.add_argument("-w", "--where", help="WHERE 條件，例如 \"source='cnyes'\"")
    p.add_argument("-o", "--order", help="排序欄位（預設用該表的時間欄位由新到舊）")
    p.add_argument("-x", "--vertical", action="store_true", help="直式顯示，適合看長文")
    p.add_argument("--full", action="store_true", help="不截斷欄位內容")
    p.add_argument("--schema", action="store_true", help="只看欄位結構")
    p.add_argument("-q", "--query", help="直接跑一段 SQL")
    args = p.parse_args()

    maxw = 0 if (args.full or args.vertical) else 40

    with psycopg.connect(load_url()) as conn:
        if args.query:
            cur = conn.execute(args.query)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
            (print_vertical if args.vertical else lambda c, r: print_table(c, r, maxw))(cols, rows)
            print(f"\n{len(rows)} 筆")
            return

        if not args.table:
            rows = conn.execute("""
                select relname, n_live_tup from pg_stat_user_tables order by 2 desc
            """).fetchall()
            print_table(["表", "筆數(估計)"], rows, 0)
            print("\n用法：python scripts/peek_db.py <表名>")
            return

        if args.schema:
            rows = conn.execute("""
                select column_name, data_type, is_nullable
                from information_schema.columns
                where table_schema='public' and table_name=%s
                order by ordinal_position
            """, (args.table,)).fetchall()
            if not rows:
                sys.exit(f"找不到表：{args.table}")
            print_table(["欄位", "型別", "可空"], rows, 0)
            return

        cols_sql = ", ".join(args.columns) if args.columns else "*"
        order = args.order or ORDER_COL.get(args.table)
        sql = f"select {cols_sql} from {args.table}"
        if args.where:
            sql += f" where {args.where}"
        if order:
            sql += f" order by {order} desc nulls last"
        sql += f" limit {args.limit}"

        cur = conn.execute(sql)
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
        if args.vertical:
            print_vertical(cols, rows)
        else:
            print_table(cols, rows, maxw)
        print(f"\n{len(rows)} 筆　|　{sql}")


if __name__ == "__main__":
    main()
