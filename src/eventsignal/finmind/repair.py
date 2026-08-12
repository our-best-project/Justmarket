"""假零值仲裁：向資料源重新確認「三大法人全零」列的真偽，是假零就修復。

【為什麼落庫後分不出來】批次跑在法人資料未出齊的時刻，抓不到的股票被以 0
補位寫入——「真零」（當日法人真的沒動作，基線約 6–10%）與「假零」（資料
還沒出）在 DB 裡一模一樣。但資料源可以仲裁：法人資料公布後即為最終版，
重抓一次即知。

【誰在用】
  daily_batch.run() 收尾   → 當天自動仲裁（只看最新交易日，額度上限保護）
  scripts/repair_fake_zeros.py → 手動深度救援（回看 N 天，一次性清存量）

【為何每日批次不會自己治好】daily_batch 只抓 db_max+1 之後的日子——已寫入
的列永遠不會被重看。假零一旦落庫，沒有本模組就是永久的。
"""
import time
from datetime import date, timedelta

from eventsignal.finmind.client import FinMindClient, RateLimitError
from eventsignal.finmind.daily_batch import aggregate_institutional


def scan_zero_rows(conn, days: int) -> dict[str, list[date]]:
    """全零列掃描（DB-only，零 API 呼叫）。回傳 {ticker: [date, ...]}。"""
    with conn.cursor() as cur:
        cur.execute(
            """select ticker, date from chip_data
               where date > current_date - %s
                 and foreign_net = 0 and trust_net = 0 and dealer_net = 0
               order by ticker, date""", (days,))
        rows = cur.fetchall()
    by_tk: dict[str, list[date]] = {}
    for tk, d in rows:
        by_tk.setdefault(tk, []).append(d)
    return by_tk


def scan_latest_day(conn) -> dict[str, list[date]]:
    """只掃最新交易日的全零列（每日自動仲裁用）。"""
    with conn.cursor() as cur:
        cur.execute(
            """select ticker, date from chip_data
               where date = (select max(date) from chip_data)
                 and foreign_net = 0 and trust_net = 0 and dealer_net = 0""")
        rows = cur.fetchall()
    return {tk: [d] for tk, d in rows}


def arbitrate(conn, client: FinMindClient, by_tk: dict,
              cap: int = 500, pause: float = 0.3) -> tuple[int, int, int]:
    """逐檔向資料源仲裁。回傳 (確認真零列, 修復假零列, 跳過檔數)。

    一檔一次 range 呼叫（涵蓋該檔全部嫌疑日）；RateLimitError 停損，
    下次再跑會從剩餘的繼續（冪等）。
    """
    confirmed = fixed = skipped = 0
    tickers = sorted(by_tk)[:cap]
    if len(by_tk) > len(tickers):
        print(f"  仲裁檔數 {len(by_tk)} 超過單次上限 {cap}，其餘下次再跑")
    for i, tk in enumerate(tickers):
        dates = by_tk[tk]
        try:
            inst = aggregate_institutional(client.institutional_stock(
                tk, dates[0].isoformat(), dates[-1].isoformat()))
        except RateLimitError:
            print(f"  額度用盡，已仲裁 {i}/{len(tickers)} 檔，其餘下次再跑")
            skipped = len(tickers) - i
            break
        except Exception as exc:
            print(f"  {tk} 重抓失敗（跳過）：{exc}")
            skipped += 1
            continue
        fixed_dates = []
        for d in dates:
            truth = inst.get((tk, d.isoformat()))
            if not truth or not any(truth.values()):
                confirmed += 1          # 資料源也說零 → 真零，市場事實
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """update chip_data set foreign_net=%s, trust_net=%s, dealer_net=%s,
                       updated_at=now() where ticker=%s and date=%s""",
                    (truth["foreign_net"], truth["trust_net"], truth["dealer_net"], tk, d))
            fixed += 1
            fixed_dates.append(d)
        if fixed_dates:
            recompute_net_derived(conn, tk, min(fixed_dates))
            conn.commit()
            print(f"  {tk}: 修復假零 {[str(x) for x in fixed_dates]}")
        if pause:
            time.sleep(pause)
    return confirmed, fixed, skipped


def recompute_net_derived(conn, tk: str, from_date: date) -> None:
    """nets 修復後重算受其影響的兩欄：外資連續天數、淨額佔 20 日均量比。

    連續天數是往前累積的鏈——從修復點之前的值當 seed，往後整段重推。
    報酬與 σ 只依價量，不受 nets 影響，不用動。
    """
    warm = from_date - timedelta(days=35)      # 35 天含 20 日均量暖機
    with conn.cursor() as cur:
        cur.execute(
            """select date, foreign_net, trust_net, dealer_net, volume
               from chip_data where ticker=%s and date >= %s order by date""",
            (tk, warm))
        rows = cur.fetchall()
        cur.execute(
            """select foreign_consecutive_days from chip_data
               where ticker=%s and date < %s order by date desc limit 1""",
            (tk, warm))
        seed_row = cur.fetchone()
    run = seed_row[0] if seed_row and seed_row[0] is not None else 0
    vols: list[int] = []
    for d, fnet, tnet, dnet, vol in rows:
        s = 1 if fnet > 0 else (-1 if fnet < 0 else 0)
        run = (run + s if (run > 0 and s > 0) or (run < 0 and s < 0) else s) if s != 0 else 0
        vols.append(vol)
        avg20 = sum(vols[-20:]) / min(len(vols), 20) if vols else None
        pct = round((fnet + tnet + dnet) / avg20 * 100, 2) if avg20 else None
        with conn.cursor() as cur:
            cur.execute(
                """update chip_data set foreign_consecutive_days=%s,
                   net_vs_avg20_volume_pct=%s where ticker=%s and date=%s""",
                (run, pct, tk, d))
