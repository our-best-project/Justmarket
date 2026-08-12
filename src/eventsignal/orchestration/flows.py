"""EventSignal 的三條 Prefect flow。

- news_daily：crawler → Neon → embedding → clustering → LLM → scoring
- market_index_daily：各國大盤 Yahoo 日線 → Neon
- finmind_after_market：FinMind 籌碼 → 市場驗證重評

Spider Forge Graph 不在任何 flow 內。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from prefect import flow, get_run_logger, task

from eventsignal.ingestion import APPROVED_CRAWLERS, crawl_and_ingest

# orchestration → eventsignal → src → <repo>。子程序的工作目錄；套件本身已安裝進
# venv，cwd 不影響 import，但 crawler/ 與 data/ 的相對路徑要從這裡算。
REPO_ROOT = Path(__file__).resolve().parents[3]


@task(retries=2, retry_delay_seconds=[60, 300], timeout_seconds=1200)
def crawl_source(spider_name: str, dry_run: bool = False) -> dict[str, int]:
    logger = get_run_logger()
    logger.info("crawler 啟動：%s", spider_name)
    result = crawl_and_ingest(spider_name, dry_run=dry_run)
    logger.info("crawler 完成：%s %s", spider_name, result)
    return result


@task(retries=1, retry_delay_seconds=300, timeout_seconds=43200)
def run_news_pipeline() -> None:
    """用既有 CLI 串接向量化與事件管線；子程序失敗會正確回報 Prefect。"""
    logger = get_run_logger()
    batch = os.environ.get("EMBEDDING_BATCH_SIZE", "32")
    device = os.environ.get("EMBEDDING_DEVICE", "auto")
    stages = os.environ.get("NEWS_PIPELINE_STAGES", "all")
    commands = [
        [
            sys.executable,
            "-m",
            "eventsignal.embedding.bge_m3",
            "--source",
            "db",
            "--device",
            device,
            "--batch",
            batch,
        ],
        [sys.executable, "-m", "eventsignal.pipeline", "--stages", stages],
    ]
    for command in commands:
        logger.info("執行：%s", " ".join(command))
        proc = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=43200,
        )
        if proc.stdout:
            logger.info(proc.stdout[-20000:])
        if proc.returncode != 0:
            raise RuntimeError(
                f"命令失敗 code={proc.returncode}: {' '.join(command)}\n"
                f"{proc.stderr[-12000:]}"
            )


def _configured_spiders() -> list[str]:
    raw = os.environ.get(
        "NEWS_SPIDERS",
        "cnyes,twse_mops_finance",
    )
    names = [name.strip() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in APPROVED_CRAWLERS]
    if unknown:
        raise ValueError(f"NEWS_SPIDERS 含未核准項目：{unknown}")
    if not names:
        raise ValueError("NEWS_SPIDERS 不可為空")
    return names


@task(retries=2, retry_delay_seconds=60, timeout_seconds=120)
def pending_article_count() -> int:
    from eventsignal.db.session import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT count(*) AS count FROM articles WHERE status='pending'"
        ).fetchone()
    return int(row["count"])


@flow(name="news-daily", log_prints=True)
def news_daily(
    spiders: list[str] | None = None,
    dry_run: bool = False,
    run_downstream: bool = True,
) -> dict[str, dict[str, int]]:
    """每日新聞；dry_run 只抓取及驗契約，不寫 Neon、不跑下游。"""
    selected = spiders or _configured_spiders()
    results: dict[str, dict[str, int]] = {}
    for spider_name in selected:
        results[spider_name] = crawl_source(spider_name, dry_run=dry_run)

    if not dry_run and run_downstream:
        pending = pending_article_count()
        if pending:
            get_run_logger().info("待處理 pending 文章：%d", pending)
            run_news_pipeline()
        else:
            get_run_logger().info("沒有 pending 文章；本次略過向量化與下游")
    return results


@task(retries=2, retry_delay_seconds=[300, 900], timeout_seconds=1800)
def update_market_index(dry_run: bool = False) -> None:
    from eventsignal.market_index.daily_batch import run

    run(dry_run=dry_run)


@flow(name="market-index-daily", log_prints=True)
def market_index_daily(dry_run: bool = False) -> None:
    """每日 15:05（台北）抓最近可得的亞洲、歐洲、美國八大指數收盤。"""
    update_market_index(dry_run=dry_run)


@task(retries=1, retry_delay_seconds=1800, timeout_seconds=43200)
def update_finmind(
    dry_run: bool = False,
    demand_only: bool = False,
    tickers: list[str] | None = None,
) -> None:
    from eventsignal.finmind.daily_batch import run

    run(tickers=tickers, dry_run=dry_run, demand_only=demand_only)


@task(retries=1, retry_delay_seconds=300, timeout_seconds=1800)
def rescore_market_validation() -> dict[str, int]:
    import psycopg

    from eventsignal.finmind.daily_batch import _db_url
    from eventsignal.finmind.jobs import rescore_pending_events

    with psycopg.connect(_db_url(), connect_timeout=30) as conn:
        done, skipped = rescore_pending_events(conn)
        conn.commit()
    return {"rescored": done, "skipped": skipped}


@flow(name="finmind-after-market", log_prints=True)
def finmind_after_market(
    dry_run: bool = False,
    demand_only: bool = False,
    tickers: list[str] | None = None,
) -> dict[str, int] | None:
    """每日 18:10（台北）更新籌碼；正式排程跑全量一次。"""
    update_finmind(dry_run=dry_run, demand_only=demand_only, tickers=tickers)
    if dry_run:
        return None
    return rescore_market_validation()
