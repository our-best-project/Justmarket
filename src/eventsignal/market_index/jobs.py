"""各國大盤指數盤後 job：給 APScheduler 掛（沿用 finmind_Bright/jobs.py 範式）。

冪等；例外一律捕捉記 log、不外拋——job 掛掉不能炸掉整個 scheduler。

排程建議（多時區，各市場收盤時間不同，故一天兩班撿齊）：
  - 亞股班：台北 15:00（台/日/韓/港/中都收盤後）
  - 歐美班：台北 08:00（撿前一夜歐股 23:30、美股 04:00 收盤）
  兩班都跑全 8 指數；Yahoo 給的是「各指數最近可得收盤」，冪等 upsert 不重複。

給 Arku 的註冊範例（貼進 core/scheduler.py）：

    from apscheduler.schedulers.blocking import BlockingScheduler
    from eventsignal.market_index.jobs import daily_index_job

    sched = BlockingScheduler(timezone="Asia/Taipei")
    sched.add_job(daily_index_job, "cron", hour=15, minute=5,
                  id="market_index_asia", misfire_grace_time=3600,
                  coalesce=True, max_instances=1)
    sched.add_job(daily_index_job, "cron", hour=8, minute=5,
                  id="market_index_eu_us", misfire_grace_time=3600,
                  coalesce=True, max_instances=1)

環境需求：.env 的 DATABASE_URL（worker 容器啟動時載入）。
⚠️ Neon pooler：若寫入用 -pooler 端點，勿在連線上下 SET（PgBouncer 會殘留污染連線池）；
   用直連端點或 options=-c（見 DECISIONS.md 2026-07-17）。
"""
from __future__ import annotations

import logging

from . import daily_batch

logger = logging.getLogger("market_index_jobs")


def daily_index_job() -> None:
    """抓 8 指數日線 → upsert market_index_daily。冪等；例外不外拋。"""
    logger.info("=== 各國大盤指數盤後 job 開始 ===")
    try:
        daily_batch.run()
        logger.info("各國大盤指數更新完成")
    except Exception:
        logger.exception("各國大盤指數更新失敗（等下一班重試，冪等安全）")
