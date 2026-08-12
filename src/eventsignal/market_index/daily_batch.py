"""各國大盤指數每日盤後批次：抓 8 指數日線 → 算當日漲跌 % → upsert market_index_daily。

沿用 finmind_Bright/daily_batch.py 的 upsert 範式（ON CONFLICT 冪等、psycopg executemany）。
資料源 Yahoo chart API（見 client.py），一次抓 3 個月日線（足夠回算 20 日報酬）。

冪等：重跑只更新既有 (index_code, date) 列的 close/change_pct，不重複插入。
單一指數抓失敗只跳過該指數、不中止整批（一國掛掉不影響其他國）。

用法（從 backend/ 執行，才吃得到 .env 的 DATABASE_URL）：
  python -m eventsignal.market_index.daily_batch --dry-run   # 只抓不寫，印摘要
  python -m eventsignal.market_index.daily_batch             # 正式：抓 8 指數 upsert
"""
from __future__ import annotations

import argparse

import psycopg

from eventsignal.db.session import db_url

from .client import YahooFetchError, fetch_index_history

FETCH_RANGE = "3mo"  # 約 40~60 交易日，確保回算 20 日報酬有足夠 21 根收盤

SELECT_INDICES = (
    "SELECT index_code, yahoo_symbol FROM market_indices ORDER BY display_order"
)

UPSERT_SQL = """
insert into market_index_daily (
  index_code, date, close, change_pct, session_state, updated_at
) values (
  %(index_code)s, %(date)s, %(close)s, %(change_pct)s, %(session_state)s, now()
)
on conflict (index_code, date) do update set
  close = excluded.close,
  change_pct = excluded.change_pct,
  session_state = excluded.session_state,
  updated_at = now();
"""


def _rows_for_index(index_code: str, history: list[dict]) -> list[dict]:
    """把 client 的 [{date, close}]（正序）轉成含當日漲跌 % 的 upsert 列。

    change_pct[i] = (close[i] / close[i-1] - 1) * 100；序列首列無前一日 → NULL。
    session_state 一律 'closed'：批次抓的是已收盤日線；即時開/收盤狀態由 API 依時區算。
    """
    rows: list[dict] = []
    prev_close: float | None = None
    for point in history:
        close = point["close"]
        change_pct = None if prev_close is None else (close / prev_close - 1) * 100
        rows.append({
            "index_code": index_code,
            "date": point["date"],
            "close": close,
            "change_pct": change_pct,
            "session_state": "closed",
        })
        prev_close = close
    return rows


def run(dry_run: bool = False) -> None:
    """抓所有主檔指數並 upsert。主檔為空時提示先建表+種子。"""
    conn = psycopg.connect(db_url(), connect_timeout=30)
    try:
        with conn.cursor() as cur:
            cur.execute(SELECT_INDICES)
            indices = cur.fetchall()  # [(index_code, yahoo_symbol), ...]
        if not indices:
            print("market_indices 無資料 —— 請先對 DB 跑 "
                  "DATABASE/postgres/05_market_index.sql（建表 + 8 指數種子）。")
            return

        total = 0
        for index_code, yahoo_symbol in indices:
            try:
                history = fetch_index_history(yahoo_symbol, FETCH_RANGE)
            except YahooFetchError as e:
                print(f"[SKIP] {index_code:7s} 抓取失敗：{e}")
                continue
            rows = _rows_for_index(index_code, history)
            latest = rows[-1]
            if dry_run:
                print(f"[DRY] {index_code:7s} {len(rows):2d} 列 | 最新 {latest['date']} "
                      f"close={latest['close']:.2f} change={latest['change_pct']:+.2f}%")
                continue
            with conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, rows)
            conn.commit()
            total += len(rows)
            print(f"[OK ] {index_code:7s} upsert {len(rows):2d} 列 | 最新 {latest['date']} "
                  f"close={latest['close']:.2f} change={latest['change_pct']:+.2f}%")
        print(f"完成：{len(indices)} 指數"
              + ("（dry-run 未寫入）" if dry_run else f"，共 {total} 列 upsert。"))
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="各國大盤指數每日盤後批次")
    ap.add_argument("--dry-run", action="store_true", help="只抓不寫入，印摘要")
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
