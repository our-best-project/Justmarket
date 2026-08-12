"""T21（Bright 側）：給 APScheduler 掛的盤後 job。

排程分工（見 core/scheduler.py 殼註）：
  - Arku 主責 scheduler 骨架＋worker 容器（新聞管線每 10–15 分一輪）
  - Bright 提供本模組的 `daily_after_market_job`（FinMind 每日盤後一次）

job 做兩件事（順序固定）：
  1. 籌碼增量：finmind_Bright.daily_batch.run()——抓新交易日、算衍生欄位、
     upsert chip_data、回填到期報酬。冪等（重跑無害，(ticker,date) upsert）。
  2. 盤後重評：對 events 中 verify_state != 'verified' 的事件重跑市場驗證分數
     （報酬視窗到期 → 分數與狀態隨資料累積更新，SD §2.5 狀態機）。

排程安全設計：
  - 例外一律捕捉記 log、不外拋——job 掛掉不能炸掉整個 scheduler。
  - FinMind 盤後資料偶爾延遲（實例：2026-07-10 當天無資料）→ 增量批次會自動
    「無新日期，結束」，因此建議一天排兩班（18:10 主班 + 21:10 補班），
    第二班撿延遲資料，冪等所以安全。

給 Arku 的註冊範例（貼進 core/scheduler.py）：

    from apscheduler.schedulers.blocking import BlockingScheduler
    from backend.finmind.jobs import daily_after_market_job

    sched = BlockingScheduler(timezone="Asia/Taipei")
    sched.add_job(daily_after_market_job, "cron", hour=18, minute=10,
                  id="finmind_daily", misfire_grace_time=3600, coalesce=True,
                  max_instances=1)
    sched.add_job(daily_after_market_job, "cron", hour=21, minute=10,
                  id="finmind_daily_retry", misfire_grace_time=3600, coalesce=True,
                  max_instances=1)

    misfire_grace_time=3600：worker 重啟錯過班次，1 小時內補跑。
    coalesce=True＋max_instances=1：堆積多班只跑一次、不重疊。

環境需求：.env 的 DATABASE_URL、FINMIND_TOKEN（worker 容器啟動時載入）。
"""
from __future__ import annotations

import logging
from datetime import date as Date
from datetime import timedelta
from zoneinfo import ZoneInfo

import psycopg

from . import daily_batch

logger = logging.getLogger("finmind_jobs")

TAIPEI = ZoneInfo("Asia/Taipei")
MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN = 13, 30  # 台股收盤 13:30


def resolve_d0(occurred_at) -> Date:
    """事件時間 → D0（市場第一個可反應的交易『日曆日下界』）。

    規則：收盤（台北 13:30）前發生 → 當日；收盤後 → 次一日曆日。
    週末/休市不用特別處理——下游 load_chip_rows 取 date >= D0 的第一個交易列，
    自然落到次一交易日。
    """
    local = occurred_at.astimezone(TAIPEI)
    d0 = local.date()
    if (local.hour, local.minute) >= (MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN):
        d0 += timedelta(days=1)
    return d0


def rescore_pending_events(conn) -> tuple[int, int]:
    """對尚未 verified 的事件重跑市場驗證分數。回傳 (重評數, 略過數)。

    略過：無 ticker / 非普通股 / 無 expected_direction（LLM 段還沒填）。
    """
    from psycopg.rows import tuple_row

    from backend.scoring.market_validation import TICKER_4DIGIT, score_and_update_event

    # 下面用位置解包，強制 tuple_row——正式管線的連線是 dict_row（backend.db.session），
    # 不指定的話解包出來會是欄位名稱字串
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """select event_id, related_tickers, occurred_at_iso,
                      expected_direction, direction_confidence
               from events
               where (verify_state is distinct from 'verified')
                 and occurred_at_iso is not null""")
        rows = cur.fetchall()

    done = skipped = 0
    for event_id, tickers, occurred, direction, conf in rows:
        ticker = str(tickers[0]) if tickers else None
        if not ticker or not TICKER_4DIGIT.match(ticker) or not direction:
            skipped += 1
            continue
        try:
            res = score_and_update_event(
                conn, event_id, ticker, resolve_d0(occurred),
                direction, conf or "high")
            done += 1
            logger.info("重評 %s(%s) → %s 分 [%s]",
                        event_id, ticker, res.market_validation, res.verify_state)
        except Exception:
            skipped += 1
            logger.exception("重評 %s 失敗，略過", event_id)
    return done, skipped


def daily_after_market_job() -> None:
    """盤後 job：籌碼增量 → 重評未 verified 事件。給 APScheduler 掛。

    冪等；例外不外拋（排程 job 掛掉不能炸掉 scheduler）。
    """
    logger.info("=== FinMind 盤後 job 開始 ===")
    # 第一步：籌碼增量
    try:
        daily_batch.run()
        logger.info("籌碼增量完成")
    except Exception:
        logger.exception("籌碼增量失敗（本班次略過重評，等下一班）")
        return
    # 第二步：盤後重評（增量成功才做——分數要基於最新籌碼）
    try:
        conn = psycopg.connect(daily_batch._db_url(), connect_timeout=30)
        try:
            done, skipped = rescore_pending_events(conn)
            logger.info("盤後重評完成：重評 %d 件、略過 %d 件", done, skipped)
        finally:
            conn.close()
    except Exception:
        logger.exception("盤後重評失敗")
    logger.info("=== FinMind 盤後 job 結束 ===")


if __name__ == "__main__":
    # 手動觸發（測試用）：python -m backend.finmind.jobs
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    daily_after_market_job()
