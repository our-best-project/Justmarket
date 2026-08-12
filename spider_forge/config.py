"""Spider Forge 的集中設定與執行期路徑。"""

from __future__ import annotations

import os
import re
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SITE_QUEUE_PATH = PACKAGE_DIR / "site_queue.yaml"
DATA_DIR = Path(
    os.environ.get("SPIDERFORGE_DATA_DIR", str(PACKAGE_DIR / "runtime"))
).expanduser().resolve()

REQUESTS_DIR = DATA_DIR / "requests"
RUNS_DIR = DATA_DIR / "runs"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
CANDIDATE_DIR = ARTIFACTS_DIR / "candidates"
ACTIVE_DIR = ARTIFACTS_DIR / "active"
VERSION_DIR = ARTIFACTS_DIR / "versions"
RECORDS_DIR = DATA_DIR / "records"
RUNS_LOG = RECORDS_DIR / "runs.jsonl"
PROMOTIONS_LOG = RECORDS_DIR / "promotions.jsonl"
DEAD_LETTER_DIR = RECORDS_DIR / "dead_letter"
MODELS_DIR = DATA_DIR / "models"
TOPIC_MODELS_DIR = MODELS_DIR

MAX_REPAIRS = 2
GENERATION_PROVIDER = os.environ.get("SPIDERFORGE_GENERATION_PROVIDER", "deepseek")
REPAIR_PROVIDER = os.environ.get("SPIDERFORGE_REPAIR_PROVIDER", "deepseek")
FINAL_REPAIR_PROVIDER = os.environ.get(
    "SPIDERFORGE_FINAL_REPAIR_PROVIDER", "kimi"
)
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}")


def ensure_runtime_layout() -> None:
    """建立所有可變資料目錄。"""
    for directory in (
        REQUESTS_DIR,
        RUNS_DIR,
        CANDIDATE_DIR,
        ACTIVE_DIR,
        VERSION_DIR,
        RECORDS_DIR,
        DEAD_LETTER_DIR,
        MODELS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def run_dir(run_id: str) -> Path:
    """取得單次執行目錄，拒絕絕對路徑與路徑穿越。"""
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError(f"不安全的 run_id：{run_id!r}")
    path = RUNS_DIR / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def location_map() -> dict[str, str]:
    """供 CLI 顯示所有執行期資料位置。"""
    return {
        "data_root": str(DATA_DIR),
        "request_file": str(REQUESTS_DIR / "urls.txt"),
        "runs": str(RUNS_DIR),
        "active_spiders": str(ACTIVE_DIR),
        "candidates": str(CANDIDATE_DIR),
        "versions": str(VERSION_DIR),
        "run_ledger": str(RUNS_LOG),
        "promotion_ledger": str(PROMOTIONS_LOG),
        "dead_letter": str(DEAD_LETTER_DIR),
        "topic_models": str(MODELS_DIR),
    }
