"""維運紀錄：每站每次 graph 跑完 append 到集中式 records/runs.jsonl。

一站一筆、只追加不覆蓋——這是系統的「實況帳本」：做了幾支 spider、平均幾次成功、
花多少 token、哪些站卡住，全靠這份檔回答。
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime

from ..config import RUNS_LOG

_LOG = RUNS_LOG
_lock = threading.Lock()


def append_run(record: dict) -> None:
    """追加一筆紀錄（自動蓋 ts 時間戳在最前）。"""
    row = {"ts": datetime.now(UTC).isoformat(), **record}
    with _lock:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_runs() -> list[dict]:
    if not _LOG.exists():
        return []
    return [json.loads(l) for l in _LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def summarize() -> dict:
    """彙總成功率、首次通過率、修復次數與程式模型用量。"""
    rows = load_runs()
    # 同一 source_prefix 可能跑多次，以「最後一次」為該站現況
    latest: dict[str, dict] = {}
    for r in rows:
        key = r.get("source_prefix") or r.get("site_name", "?")
        latest[key] = r

    sites = list(latest.values())
    success = [r for r in sites if r.get("status") == "success"]
    retries = [r.get("retry_count", 0) for r in success if r.get("retry_count") is not None]
    repair_counts = [
        row.get("repair_count", row.get("retry_count", 0))
        for row in sites
        if row.get("repair_count", row.get("retry_count")) is not None
    ]
    first_pass = sum(
        1
        for row in sites
        if row.get("first_pass_success", row.get("retry_count") == 0)
    )
    total_tokens = sum(r.get("coder_tokens", 0) or 0 for r in rows)
    return {
        "runs_total": len(rows),
        "sites_distinct": len(sites),
        "success": len(success),
        "success_rate": round(len(success) / len(sites), 4) if sites else None,
        "escalated": sum(1 for r in sites if r.get("status") == "escalated"),
        "error": sum(1 for r in sites if r.get("status") == "error"),
        "avg_retries_on_success": round(sum(retries) / len(retries), 2) if retries else None,
        "first_try_success": first_pass,
        "first_pass_rate": round(first_pass / len(sites), 4) if sites else None,
        "repairs_total": sum(repair_counts),
        "coder_tokens_total": total_tokens,
    }


if __name__ == "__main__":
    print(json.dumps(summarize(), ensure_ascii=False, indent=2))
    for r in load_runs():
        print(f"  {r.get('ts', '')[:19]}  {r.get('source_prefix', '?'):9} "
              f"{r.get('status', '?'):10} retry={r.get('retry_count')} "
              f"items={r.get('item_count')} strat={r.get('strategy')} {r.get('duration_s')}s")
